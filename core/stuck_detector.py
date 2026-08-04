"""Phase 14A: Stuck-phase detection — auto-detect stalled roadmap phases
and builds, self-answer judgment calls that don't need operator input.

Detects phases that have been in_progress for >24 hours with no build activity,
builds cycling WAITING_FOR_USER_INPUT >5 times (question loops), and phases
that failed >3 times with the same error pattern.
"""

import json
from datetime import datetime, timezone, timedelta
from collections import Counter


def _load_roadmap():
    from core.memory import load
    data = load("builds.json")  # We need builds for activity tracking
    if isinstance(data, dict):
        return data.get("records", [])
    return data or []


def detect_stuck_phases():
    """Find roadmap phases that appear stuck."""

    # Load builds and roadmap
    from core.roadmap_engine import load_roadmap

    builds = _load_roadmap()
    roadmap = load_roadmap() or {}

    phases = roadmap.get("phases", []) if isinstance(roadmap, dict) else []
    findings = []

    for phase in phases:
        pid = phase.get("id")
        status = phase.get("status")

        # Phases that have been in_progress for a while with no recent build
        if status == "in_progress":
            phase_builds = [b for b in builds if b.get("name") == pid]
            if not phase_builds:
                findings.append({
                    "phase": pid,
                    "type": "stuck_in_progress",
                    "detail": "No builds found for in_progress phase",
                    "severity": "warning",
                })
                continue

            latest = max(phase_builds, key=lambda b: b.get("created", ""))
            try:
                updated = datetime.fromisoformat(latest.get("updated", ""))
                hours_stale = (datetime.now(timezone.utc) - updated).total_seconds() / 3600
                if hours_stale > 24:
                    findings.append({
                        "phase": pid,
                        "type": "stale_phase",
                        "detail": f"No build activity in {hours_stale:.0f}h",
                        "severity": "warning",
                    })
            except (ValueError, TypeError):
                pass

        # Phases that failed >3 times with the same error
        if status == "failed":
            phase_builds = [b for b in builds if b.get("name") == pid and b.get("status") == "FAILED"]
            reasons = Counter(b.get("failure_reason", "")[:80] for b in phase_builds)
            for reason, count in reasons.most_common(1):
                if count >= 3:
                    findings.append({
                        "phase": pid,
                        "type": "repeated_failure",
                        "detail": f"Failed {count}x: {reason}",
                        "severity": "critical",
                    })

    return findings


def detect_question_loops():
    """Detect builds cycling WAITING_FOR_USER_INPUT repeatedly."""
    builds = _load_roadmap()
    loops = []

    for b in builds:
        if b.get("status") != "WAITING_FOR_USER_INPUT":
            continue
        history = b.get("history", [])
        # Count how many times this build entered WAITING_FOR_USER_INPUT
        wait_cycles = len([h for h in history if h.get("status") == "WAITING_FOR_USER_INPUT"])
        if wait_cycles >= 5:
            loops.append({
                "build_id": b.get("id", "")[:8],
                "name": b.get("name", "?"),
                "cycles": wait_cycles,
                "detail": f"Question loop: {wait_cycles} WAITING_FOR_USER_INPUT cycles",
            })

    return loops


def auto_resolve_question_loop(build_id):
    """Auto-answer a question loop with a definitive 'proceed' to break the cycle."""
    from core.build_manager import submit_answer

    submit_answer(
        build_id,
        "No further questions. Plan is approved. Proceed directly to implementation "
        "without additional clarification rounds. [Auto-resolved by 14A stuck-phase detector]",
    )
    return {"build_id": build_id, "action": "auto_answered"}


def get_stuck_report():
    """Full stuck-phase report for the dashboard."""
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "stuck_phases": detect_stuck_phases(),
        "question_loops": detect_question_loops(),
    }
