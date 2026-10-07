"""Phase 16B: Self-healing — automated recovery for known, scoped failure classes.

Detects known failure patterns and attempts recovery before escalating to
operator. Each rule: detect → attempt fix → verify → escalate if failed.
All recoveries are recorded to the incident/learning pipeline.
"""

import json
import os
import time
from pathlib import Path
from datetime import datetime, timezone


# ── Recovery rule definitions ────────────────────────────────────────────

def _now():
    return datetime.now(timezone.utc).isoformat()


def _detect_stuck_builds():
    """Detect builds that have been GENERATING for >1 hour with no output."""
    from core.memory import load

    builds_data = load("builds.json")
    if not builds_data:
        return []

    builds = builds_data.get("records", []) if isinstance(builds_data, dict) else builds_data
    stuck = []

    for b in builds:
        if b.get("status") != "GENERATING":
            continue
        gr = b.get("generation_result")
        if gr is not None:
            continue  # has output, not stuck
        updated = b.get("updated", "")
        if not updated:
            continue
        try:
            last = datetime.fromisoformat(updated)
            age_minutes = (datetime.now(timezone.utc) - last).total_seconds() / 60
            if age_minutes > 60:
                stuck.append({
                    "build_id": b["id"],
                    "name": b.get("name", "?"),
                    "age_minutes": round(age_minutes),
                })
        except (ValueError, TypeError):
            pass

    return stuck


def _detect_provider_errors():
    """Detect providers with consecutive errors that need circuit-breaker reset."""
    from core.ai.provider_health import get_all_snapshots

    snapshots = get_all_snapshots() or {}
    if not isinstance(snapshots, dict):
        return []

    degraded = []
    for name, snap in snapshots.items():
        if not isinstance(snap, dict):
            continue
        if snap.get("status") == "error":
            degraded.append({"provider": name, "detail": str(snap.get("detail", ""))[:100]})
    return degraded


def _clear_provider_errors(provider_name):
    """Reset a provider's error state so it gets retried."""
    from core.ai.provider_health import clear_quota_exceeded
    try:
        clear_quota_exceeded(provider_name)
        return True
    except Exception:
        return False


def _kill_stale_coding():
    """Kill any coding bridge processes that have been running >30 minutes."""
    import subprocess
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid,etimes,args"],
            capture_output=True, text=True, timeout=5,
        )
        killed = []
        for line in result.stdout.split("\n"):
            if "cloudcli" not in line and "coding_bridge" not in line:
                continue
            parts = line.split()
            if len(parts) < 3:
                continue
            try:
                pid = int(parts[0])
                etimes = int(parts[1])
            except ValueError:
                continue
            if etimes > 1800:  # 30 minutes
                subprocess.run(["kill", str(pid)], capture_output=True)
                killed.append(str(pid))
        return killed
    except Exception:
        return []


def _reset_stuck_build(build_id):
    """Reset a stuck build to ARCHITECTURE_APPROVED for retry."""
    from core.memory import update

    def mutate(records):
        records = records if isinstance(records, list) else []
        for r in records:
            if r.get("id") == build_id:
                r["status"] = "ARCHITECTURE_APPROVED"
                r["generation_result"] = None
                r.setdefault("history", []).append({
                    "status": "ARCHITECTURE_APPROVED",
                    "timestamp": _now(),
                    "note": "Auto-healed: reset after 1hr stuck in GENERATING with no output",
                })
                break
        return records

    update("builds.json", mutate)


# ── Main recovery loop ──────────────────────────────────────────────────

def run_self_healing():
    """One pass of self-healing.  Called from the scheduler cycle.
    Returns a list of recovery actions taken."""

    actions = []

    # 0. Workforce registry reconciliation (2026-08-22 spec §2): probe
    # degraded/dead workers, revive recovered ones, escalate the rest.
    try:
        reconcile_worker_health()
    except Exception:
        pass

    # 1. Detect and fix stuck builds
    try:
        stuck = _detect_stuck_builds()
        for build in stuck:
            _reset_stuck_build(build["build_id"])
            actions.append({
                "type": "stuck_build_reset",
                "build_id": build["build_id"],
                "name": build["name"],
                "age_minutes": build["age_minutes"],
                "timestamp": _now(),
            })
    except Exception as e:
        from core.logger import warning
        warning(f"self_healing: stuck-build step failed: {e}")

    # 2. Detect and clear provider errors
    try:
        degraded = _detect_provider_errors()
        for prov in degraded:
            if _clear_provider_errors(prov["provider"]):
                actions.append({
                    "type": "provider_error_cleared",
                    "provider": prov["provider"],
                    "detail": prov["detail"],
                    "timestamp": _now(),
                })
    except Exception as e:
        from core.logger import warning
        warning(f"self_healing: provider-error step failed: {e}")

    # 3. Kill stale coding bridge processes
    try:
        killed = _kill_stale_coding()
        if killed:
            actions.append({
                "type": "stale_coding_killed",
                "pids": killed,
                "timestamp": _now(),
            })
    except Exception as e:
        from core.logger import warning
        warning(f"self_healing: stale-coding step failed: {e}")

    return actions


# ── Workforce reconciliation (2026-08-22 spec §2 recovery flow) ──────────

_ESCALATED_KEY = "workforce_escalated"


def _probe_provider_available(worker_id: str) -> bool:
    """True when the underlying provider answers its availability probe."""
    from core.workforce import registry
    record = registry.get(worker_id)
    if record is None or record.kind != "provider":
        return False
    try:
        import core.ai_provider as ai_provider
        prov = ai_provider.get_provider(worker_id.split(":", 1)[1])
        return bool(prov and prov.get("available_fn") and prov["available_fn"]())
    except Exception:
        return False


def _notify_operator(message: str) -> None:
    try:
        from core.notifications import NotificationManager
        NotificationManager.enqueue(
            severity="important", title="Kai workforce",
            body=message, source="workforce_reconcile")
    except Exception:
        pass  # notification failure must never break healing


def _with_metadata(worker, extra: dict):
    import copy
    updated = copy.deepcopy(worker)
    updated.metadata.update(extra)
    return updated


def reconcile_worker_health() -> dict:
    """Cycle step: for every degraded/dead registry worker, probe the real
    provider. Recovered → revive(idle). Still down → escalate ONCE (flag in
    metadata until it revives). Returns counts for the cycle summary."""
    from core.workforce import registry
    counts = {"revived": 0, "escalated": 0}
    for worker in registry.list_workers(status="dead") + \
            registry.list_workers(status="degraded"):
        if _probe_provider_available(worker.worker_id):
            registry.revive(worker.worker_id)
            counts["revived"] += 1
        elif not worker.metadata.get(_ESCALATED_KEY):
            registry.register(_with_metadata(worker, {_ESCALATED_KEY: True}))
            _notify_operator(
                f"Kai workforce: worker {worker.worker_id} still "
                f"{worker.status} ({worker.health.get('last_reason')})")
            counts["escalated"] += 1
    return counts
