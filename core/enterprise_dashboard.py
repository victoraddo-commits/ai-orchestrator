"""Phase 15E: Enterprise Dashboard — consolidated system health, provider
health, usage analytics, and notification summary.

Reuses existing endpoints and data sources. No new data collection — all
values come from modules that already track them.
"""

import json
from datetime import datetime, timezone, timedelta


def _safe_load(name):
    """Load a memory file, returning an empty dict on any error."""
    from core.memory import load

    try:
        return load(name) or {}
    except Exception:
        return {}


def get_enterprise_snapshot() -> dict:
    """One-call snapshot for the Enterprise Dashboard tab.  Returns a dict
    with sections for health, providers, usage, approvals, and roadmap."""
    now = datetime.now(timezone.utc).isoformat()

    # ── System health ──
    system_state = _safe_load("system_state.json")
    incidents = _safe_load("incidents.json")
    active_incidents = [
        i for i in (incidents.get("records", []) if isinstance(incidents, dict) else [])
        if i.get("status") not in ("closed", "resolved")
    ]

    # ── Provider health ──
    from core.ai_provider import list_providers

    providers_raw = list_providers()
    provider_quota = _safe_load("provider_quota.json")
    quota_records = provider_quota.get("records", provider_quota) if isinstance(provider_quota, dict) else {}

    providers = {}
    for name, info in providers_raw.items():
        quota = quota_records.get(name, {}) if isinstance(quota_records, dict) else {}
        quota_status = quota.get("status", "ok") if isinstance(quota, dict) else "ok"
        providers[name] = {
            "available": info.get("available", False),
            "enabled": info.get("enabled", True),
            "capabilities": info.get("capabilities", []),
            "cost_tier": info.get("cost_tier", "unknown"),
            "quota_status": quota_status,
            "description": info.get("description", ""),
        }

    # ── Usage analytics ──
    from core.ai.ai_router import get_usage_history

    history = get_usage_history()
    total_calls = len(history)
    success_count = sum(1 for e in history if e.get("success"))
    success_rate = round(success_count / max(total_calls, 1) * 100, 1)

    # Count by task type
    task_counts: dict[str, int] = {}
    for e in history:
        task_type = e.get("task_type", "unknown")
        task_counts[task_type] = task_counts.get(task_type, 0) + 1

    # ── Pending approvals ──
    approvals_data = _safe_load("approval_queue.json")
    pending = [
        a for a in (approvals_data.get("records", []) if isinstance(approvals_data, dict) else [])
        if a.get("status") == "pending"
    ]

    # ── Roadmap ──
    from core.roadmap_engine import get_progress_summary

    roadmap = get_progress_summary()

    # ── Active builds ──
    from core.build_manager import load_builds

    all_builds = load_builds() or []
    active_builds = []
    failed_today = []
    today_str = now[:10]
    for b in all_builds:
        if b.get("status") in ("GENERATING", "CODE_REVIEW", "DEPLOYING", "PLANNING", "ARCHITECTURE_APPROVED"):
            active_builds.append({
                "id": b.get("id", "")[:8],
                "name": b.get("name", "?"),
                "status": b.get("status", "?"),
            })
        if b.get("status") == "FAILED" and (b.get("updated", "") or "").startswith(today_str):
            failed_today.append({
                "id": b.get("id", "")[:8],
                "name": b.get("name", "?"),
                "reason": (b.get("failure_reason", "") or "")[:120],
            })

    # ── Notifications ──
    stale_failures = _safe_load("stale_failure_reminders.json")
    stale_approvals = _safe_load("stale_approval_reminders.json")
    stale_failure_count = len(stale_failures.get("records", [])) if isinstance(stale_failures, dict) else 0
    stale_approval_count = len(stale_approvals.get("records", [])) if isinstance(stale_approvals, dict) else 0

    return {
        "timestamp": now,
        "system": {
            "hostname": system_state.get("records", {}).get("host", {}).get("hostname", "?") if isinstance(system_state, dict) else "?",
            "active_incidents": len(active_incidents),
            "docker_available": isinstance(system_state, dict) and system_state.get("records", {}).get("docker", {}).get("available", False),
        },
        "providers": providers,
        "usage": {
            "total_calls": total_calls,
            "success_rate": success_rate,
            "by_task_type": task_counts,
        },
        "approvals": {
            "pending": len(pending),
            "stale_reminders": stale_approval_count,
        },
        "roadmap": roadmap,
        "builds": {
            "active": active_builds,
            "failed_today": failed_today,
            "stale_failure_reminders": stale_failure_count,
        },
    }
