"""Phase 12J: multi-AI provider router.

Routes a task to the best-fit provider by role, not by "wait until Claude's
credits run out" -- Gemini/Groq are used for their designated roles from the
first task that matches, per the AI-team model:

    Claude: coding, architecture implementation, difficult debugging
    Gemini: reviews, planning, documentation, architecture critique
    Groq:   logs, quick analysis, simple tasks

Falls back through each role's candidate list on unavailability or failure,
ultimately landing on Claude (the one provider guaranteed capable of
anything) if every role-specific candidate fails. Every attempt -- success
or failure -- is recorded to memory/ai_usage_history.json.
"""

import time
from datetime import datetime

import core.ai_provider as ai_provider
import core.ai.provider_health as provider_health
from core.memory import load, save


TASK_TYPE_KEYWORDS = {
    "coding": ["build", "implement", "code", "debug", "fix", "develop", "create"],
    "planning": ["design", "architecture", "plan", "review", "critique"],
    "log_analysis": ["log", "logs", "analyze", "analysis", "quick"],
    "documentation": ["document", "documentation", "readme", "docs"],
}

# Ordered by preference within each role; every list ends with "claude" as
# the universal fallback, since it's the one provider guaranteed capable of
# any task type.
ROLE_PROVIDERS = {
    # Claude stays first -- most proven, deepest test/review discipline.
    # OpenCode (MiniMax-m2.7 via its own sandboxed agent loop) only engages
    # if Claude fails or is unavailable, per explicit user decision: remove
    # Claude as a single point of failure without making it secondary.
    "coding": ["claude", "opencode"],
    "planning": ["gemini", "openrouter", "minimax", "claude"],
    "log_analysis": ["groq", "openrouter", "claude"],
    "documentation": ["gemini", "groq", "openrouter", "minimax", "claude"],
}

DEFAULT_TASK_TYPE = "coding"

USAGE_HISTORY_FILE = "ai_usage_history.json"
MAX_DESCRIPTION_LENGTH = 200


class AllProvidersFailed(Exception):
    """Raised when every candidate provider for a task type is unavailable or fails."""


def classify_task(description):
    text = description.lower()

    scores = {
        category: sum(1 for keyword in keywords if keyword in text)
        for category, keywords in TASK_TYPE_KEYWORDS.items()
    }

    best_category = max(scores, key=scores.get)

    return best_category if scores[best_category] > 0 else DEFAULT_TASK_TYPE


def _candidates_for(task_type):
    return ROLE_PROVIDERS.get(task_type, ["claude"])


def get_usage_history():
    return load(USAGE_HISTORY_FILE) or []


def record_usage(provider, task_type, description, success, duration_ms, error=None):
    history = get_usage_history()

    entry = {
        "provider": provider,
        "task_type": task_type,
        "description": description[:MAX_DESCRIPTION_LENGTH],
        "success": success,
        "duration_ms": duration_ms,
        "error": error,
        "timestamp": datetime.now().isoformat(),
    }

    history.append(entry)
    save(USAGE_HISTORY_FILE, history)

    return entry


# Confirmed live (2026-07-28): Claude Code returns this exact wording when
# the account's weekly usage limit is hit -- unlike ai_provider.py's
# _claude_run_text_task, which deliberately never pattern-matches since it
# had no verified example, this one is real and durable (not transient), so
# it's worth recording as quota_exceeded so K4's skip-known-quota-exceeded
# check actually prevents wasted retries for the rest of the reset window.
_QUOTA_EXCEEDED_MARKERS = ("weekly limit", "usage limit")


def _record_coding_failure_health(provider_name, detail):
    if any(marker in detail.lower() for marker in _QUOTA_EXCEEDED_MARKERS):
        provider_health.capture_quota_exceeded(provider_name, detail=detail)
    else:
        provider_health.capture_provider_error(provider_name, detail=detail)


def delegate(description, task_type=None, timeout=60, project_path=None, capability="text_task"):
    resolved_type = task_type or classify_task(description)
    candidates = _candidates_for(resolved_type)

    failures = []

    for name in candidates:
        provider = ai_provider.get_provider(name)

        if provider is None:
            failures.append(f"{name}: not registered")
            continue

        if not provider["available_fn"]():
            failures.append(f"{name}: not available (no credentials configured)")
            continue

        # Only a verified quota_exceeded status skips the call outright --
        # a plain "error" status is deliberately not treated the same way
        # (see provider_health.capture_provider_error's docstring: it can't
        # distinguish a real limit from a transient blip), so those
        # candidates still get a real attempt.
        quota = provider_health.get_quota_snapshot(name)
        if quota and quota.get("status") == "quota_exceeded":
            failures.append(f"{name}: skipped, known quota_exceeded ({quota.get('detail')})")
            continue

        run_fn = provider.get("run_coding_task" if capability == "coding_agent" else "run_text_task")
        if run_fn is None:
            failures.append(f"{name}: does not support {capability}")
            continue

        start = time.time()
        try:
            if capability == "coding_agent":
                response = run_fn(project_path, description, timeout=timeout)
            else:
                response = run_fn(description, timeout=timeout, project_path=project_path)
        except Exception as error:
            duration_ms = int((time.time() - start) * 1000)
            record_usage(name, resolved_type, description, success=False, duration_ms=duration_ms, error=str(error))
            if capability == "coding_agent":
                _record_coding_failure_health(name, str(error))
            failures.append(f"{name}: {error}")
            continue

        duration_ms = int((time.time() - start) * 1000)

        # A coding_agent call can return normally (no exception) yet still
        # represent a failed generation (result["success"] is False) -- that
        # must fall through to the next candidate too, since a failed
        # generation is exactly the case that must not stall Kai.
        if capability == "coding_agent" and isinstance(response, dict) and not response.get("success"):
            duration_ms = int((time.time() - start) * 1000)
            detail = "; ".join(e.get("content", "") for e in response.get("tool_errors") or []) or "generation did not succeed"
            record_usage(name, resolved_type, description, success=False, duration_ms=duration_ms, error=detail)
            _record_coding_failure_health(name, detail)
            failures.append(f"{name}: {detail}")
            continue

        record_usage(name, resolved_type, description, success=True, duration_ms=duration_ms)

        return {
            "provider": name,
            "task_type": resolved_type,
            "response": response,
            "duration_ms": duration_ms,
        }

    raise AllProvidersFailed(
        f"No available provider could handle task_type={resolved_type!r}: " + "; ".join(failures)
    )


def get_provider_dashboard():
    history = get_usage_history()
    providers = ai_provider.list_providers()

    dashboard = {}

    for name, info in providers.items():
        last_entry = next((e for e in reversed(history) if e["provider"] == name), None)

        attempts = [e for e in history if e["provider"] == name]
        successes = [e for e in attempts if e["success"]]

        # Claude's "quota" isn't a provider-verified figure (see
        # provider_health.claude_usage_snapshot's docstring) -- keep it
        # visibly distinct from the other three's real/attempted quota data.
        # A recorded error (e.g. a failed call, possibly a usage limit) takes
        # priority over the self-tracked count -- that's more actionable
        # signal than "N requests logged".
        if name == "claude":
            recorded_error = provider_health.get_quota_snapshot("claude")
            quota = recorded_error if recorded_error and recorded_error.get("status") == "error" else provider_health.claude_usage_snapshot()
        else:
            quota = provider_health.get_quota_snapshot(name) or {
                "percent_remaining": None,
                "detail": "quota not yet checked -- no request has been made to this provider",
            }

        dashboard[name] = {
            "status": "connected" if info["available"] else "not_configured",
            "description": info["description"],
            "last_task_type": last_entry["task_type"] if last_entry else None,
            "last_success": last_entry["success"] if last_entry else None,
            "last_response_time_ms": last_entry["duration_ms"] if last_entry else None,
            "last_request_at": last_entry["timestamp"] if last_entry else None,
            "total_attempts": len(attempts),
            "total_successes": len(successes),
            "percent_remaining": quota.get("percent_remaining"),
            "quota_detail": quota.get("detail"),
        }

    return dashboard
