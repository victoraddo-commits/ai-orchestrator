"""Kai V3 Recovery — Retry logic, failure reassignment, and error recovery.

Failure handling:
- Provider failure → retry once → switch to omniroute fallback
- Generation failure → return to builder with error context
- Review failure → return to builder with reviewer findings
- Timeout → retry once
- Repeated failure → human approval queue
"""

import time
from datetime import datetime, timezone

from core.memory import load, save
from core.logger import info, error as log_error

RECOVERY_FILE = "recovery_queue.json"

MAX_RETRIES = 2  # Total attempts = 1 initial + 1 retry
OMNIROUTE_FALLBACK = "omniroute"


class RecoveryAction:
    """Actions available for failed operations."""
    RETRY = "retry"
    SWITCH_PROVIDER = "switch_provider"
    RETURN_TO_BUILDER = "return_to_builder"
    HUMAN_APPROVAL = "human_approval"
    ABANDON = "abandon"


def handle_provider_failure(build_id: str, provider: str, error: str,
                            attempt: int) -> dict:
    """Decide what to do when a provider call fails.

    Strategy:
    - attempt 1: retry with same provider
    - attempt 2: switch to omniroute fallback
    - attempt 3+: escalate to human approval
    """
    if attempt < MAX_RETRIES:
        action = RecoveryAction.RETRY
        reason = f"Provider {provider} failed (attempt {attempt}), retrying"
    elif provider != OMNIROUTE_FALLBACK:
        action = RecoveryAction.SWITCH_PROVIDER
        reason = f"Switching from {provider} to {OMNIROUTE_FALLBACK} after "
        f"{attempt} failures"
        provider = OMNIROUTE_FALLBACK
    else:
        action = RecoveryAction.HUMAN_APPROVAL
        reason = f"All providers failed after {attempt} attempts for {build_id}"

    info(f"Recovery decision for {build_id[:12]}: {action} — {reason}")

    return {
        "action": action,
        "build_id": build_id,
        "provider": provider,
        "attempt": attempt,
        "reason": reason,
        "error": error[:300],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def handle_generation_failure(build_id: str, error: str) -> dict:
    """Handle a build generation failure.

    Returns the build to builder status with error context so the builder
    can address the issue and retry.
    """
    info(f"Generation failure for {build_id[:12]}: returning to builder")

    return {
        "action": RecoveryAction.RETURN_TO_BUILDER,
        "build_id": build_id,
        "reason": f"Generation failed: {error[:300]}",
        "new_status": "PLANNING",  # Builder replans from here
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def handle_review_failure(build_id: str, reviewer: str,
                          findings: list[str]) -> dict:
    """Handle a review failure.

    Returns the build to builder with reviewer findings so they can
    address the issues and resubmit.
    """
    info(f"Review failure for {build_id[:12]} (reviewer={reviewer}): "
         f"{len(findings)} findings")

    return {
        "action": RecoveryAction.RETURN_TO_BUILDER,
        "build_id": build_id,
        "reviewer": reviewer,
        "reason": f"Review by {reviewer} found {len(findings)} issues",
        "findings": findings[:20],  # Cap at 20 findings
        "new_status": "GENERATING",  # Return to builder to fix
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def handle_timeout(build_id: str, status: str,
                   elapsed_seconds: float) -> dict:
    """Handle a build timeout. Retry once, then escalate."""

    # Check how many times this build has timed out
    history = _get_build_timeout_history(build_id)

    if len(history) == 0:
        action = RecoveryAction.RETRY
        reason = (f"First timeout for {build_id[:12]} [{status}] "
                  f"after {elapsed_seconds:.0f}s — retrying once")
    else:
        action = RecoveryAction.HUMAN_APPROVAL
        reason = (f"Repeated timeout for {build_id[:12]} [{status}] "
                  f"after {elapsed_seconds:.0f}s — escalating to human")

    info(f"Timeout recovery: {action} — {reason}")

    record = {
        "action": action,
        "build_id": build_id,
        "status": status,
        "elapsed_seconds": elapsed_seconds,
        "reason": reason,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    _record_timeout(record)

    return record


def handle_repeated_failure(build_id: str, failure_count: int,
                            failures: list[dict]) -> dict:
    """Handle a build that has failed repeatedly.

    Escalates to the human approval queue with a summary.
    """
    summary = _summarize_failures(failures)

    info(f"Repeated failure ({failure_count}x) for {build_id[:12]}: "
         f"escalating to human approval queue")

    record = {
        "action": RecoveryAction.HUMAN_APPROVAL,
        "build_id": build_id,
        "failure_count": failure_count,
        "summary": summary,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    _queue_for_approval(record)

    return record


def get_recovery_queue() -> list[dict]:
    """Get all items in the human approval/recovery queue."""
    data = load(RECOVERY_FILE)
    return data.get("records", []) if data else []


def _get_build_timeout_history(build_id: str) -> list[dict]:
    """Get timeout history for a specific build."""
    data = load(RECOVERY_FILE)
    records = data.get("records", []) if data else []
    return [r for r in records
            if r.get("build_id") == build_id and r.get("status")]


def _record_timeout(record: dict):
    """Record a timeout event."""
    data = load(RECOVERY_FILE)
    if not data:
        data = {"schema_version": 1, "records": []}
    data.setdefault("records", []).append(record)
    save(RECOVERY_FILE, data)


def _queue_for_approval(record: dict):
    """Add a build to the human approval queue."""
    data = load(RECOVERY_FILE)
    if not data:
        data = {"schema_version": 1, "records": []}

    # Avoid duplicates
    existing_ids = {r.get("build_id") for r in data.get("records", [])
                    if r.get("action") == RecoveryAction.HUMAN_APPROVAL}
    if record.get("build_id") not in existing_ids:
        data.setdefault("records", []).append(record)
        save(RECOVERY_FILE, data)


def _summarize_failures(failures: list[dict]) -> str:
    """Create a human-readable summary of repeated failures."""
    if not failures:
        return "No failure details available"

    types = Counter(f.get("reason", "").split(":")[0] for f in failures)
    return ", ".join(f"{t}: {c}x" for t, c in types.most_common(5))
