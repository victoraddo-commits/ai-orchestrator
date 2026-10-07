"""Acceptance harness (roadmap 21T / 22M).

Runs a named acceptance scenario as an ordered list of criteria and produces
an evidence bundle. Each criterion is ``{"name", "check": fn(driver)}``; the
report is PASS only when every criterion passes. The driver supplies the live
system so the same harness runs against stubs (tests) or the real estate.
"""
from __future__ import annotations

FUNCTIONAL_CRITERIA = (
    "mission_started",
    "team_assembled",
    "claude_disconnected",
    "mission_continues",
    "workers_operational",
    "mission_state_preserved",
    "failed_worker_reassigned",
    "work_completed",
    "evidence_collected",
    "world_model_updated",
)

ECOSYSTEM_CRITERIA = FUNCTIONAL_CRITERIA + (
    "code_diff_produced",
    "tests_passed",
    "security_scan_clean",
    "deploy_recorded",
    "verification_recorded",
    "roadmap_updated",
)


def run_acceptance(name: str, criteria, driver=None) -> dict:
    results = []
    evidence = []
    for criterion in criteria:
        label = criterion["name"] if isinstance(criterion, dict) else criterion[0]
        check = criterion["check"] if isinstance(criterion, dict) else criterion[1]
        try:
            ok = bool(check(driver))
            detail = "ok" if ok else "criterion not met"
        except Exception as exc:
            ok = False
            detail = f"{type(exc).__name__}: {exc}"
        results.append({"name": label, "ok": ok, "detail": detail})
        if ok:
            evidence.append(label)
    return {
        "name": name,
        "passed": all(r["ok"] for r in results) and bool(results),
        "results": results,
        "evidence": evidence,
    }


def default_criteria(names=FUNCTIONAL_CRITERIA):
    """Criteria that pass when the driver advertises the capability as True."""
    return [{"name": n, "check": (lambda d, _n=n: bool(getattr(d, _n, False)))}
            for n in names]
