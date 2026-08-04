"""Phase 16C: Continuous self-improvement — scheduled analysis of real data.

Answers concrete questions from existing usage/build/learning data:
- Which provider performs best for each task type?
- Which build patterns succeed/fail most?
- Any recurring failure patterns?
- Test flakiness trends?
"""

import json
from datetime import datetime, timezone
from collections import Counter


def analyze_provider_performance():
    """Which provider performs best per task type?  Returns rankings."""
    from core.ai.ai_router import get_usage_history

    history = get_usage_history()
    if not history:
        return {"message": "no usage data yet"}

    # Group by (provider, task_type)
    groups: dict[str, dict[str, list]] = {}
    for entry in history:
        provider = entry.get("provider", "unknown")
        task_type = entry.get("task_type", "unknown")
        success = entry.get("success", False)
        duration = entry.get("duration_ms", 0)

        key = task_type
        if key not in groups:
            groups[key] = {}
        if provider not in groups[key]:
            groups[key][provider] = []
        groups[key][provider].append({"success": success, "duration_ms": duration})

    rankings = {}
    for task_type, providers in groups.items():
        ranked = []
        for provider, attempts in providers.items():
            total = len(attempts)
            successes = sum(1 for a in attempts if a["success"])
            durations = [a["duration_ms"] for a in attempts if a["duration_ms"]]
            avg_ms = round(sum(durations) / max(len(durations), 1))
            ranked.append({
                "provider": provider,
                "success_rate": round(successes / max(total, 1) * 100, 1),
                "total": total,
                "avg_duration_ms": avg_ms,
            })
        ranked.sort(key=lambda x: x["success_rate"], reverse=True)
        rankings[task_type] = ranked

    return rankings


def analyze_build_patterns():
    """What build patterns succeed/fail most?"""
    from core.build_manager import load_builds

    builds = load_builds() or []
    if not builds:
        return {"message": "no build history yet"}

    total = len(builds)
    succeeded = sum(1 for b in builds if b.get("status") == "COMPLETED")
    failed = sum(1 for b in builds if b.get("status") == "FAILED")

    # Failure reasons
    reasons = Counter()
    for b in builds:
        if b.get("status") == "FAILED" and b.get("failure_reason"):
            reason = b["failure_reason"][:100]
            reasons[reason] += 1

    # Per-phase success
    phase_stats: dict[str, dict] = {}
    for b in builds:
        phase = b.get("name", "?")
        if phase not in phase_stats:
            phase_stats[phase] = {"total": 0, "succeeded": 0, "failed": 0}
        phase_stats[phase]["total"] += 1
        if b.get("status") == "COMPLETED":
            phase_stats[phase]["succeeded"] += 1
        elif b.get("status") == "FAILED":
            phase_stats[phase]["failed"] += 1

    return {
        "total_builds": total,
        "success_rate": round(succeeded / max(total, 1) * 100, 1),
        "top_failure_reasons": [{"reason": r, "count": c} for r, c in reasons.most_common(5)],
        "phase_stats": {p: s for p, s in sorted(phase_stats.items(), key=lambda x: x[1]["total"], reverse=True)[:10]},
    }


def analyze_flaky_tests():
    """Any test files failing repeatedly?"""
    import subprocess
    import os

    test_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tests")
    if not os.path.isdir(test_dir):
        return {"message": "test directory not found"}

    # Quick scan: count test files and check for recent test result files
    import glob
    test_files = glob.glob(os.path.join(test_dir, "test_*.py"))
    return {
        "test_files": len(test_files),
        "note": "Full flaky-test analysis requires test run history. Run tests to build history.",
    }


def get_improvement_report():
    """Full continuous improvement report."""
    now = datetime.now(timezone.utc).isoformat()
    return {
        "generated_at": now,
        "provider_performance": analyze_provider_performance(),
        "build_patterns": analyze_build_patterns(),
        "test_analysis": analyze_flaky_tests(),
    }
