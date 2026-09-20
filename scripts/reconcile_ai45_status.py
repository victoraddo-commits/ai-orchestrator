"""One-shot reconciliation of the AI-4 / AI-5 status inconsistency (2026-09-20).

truth = git history + code, not the roadmap flag or the build record alone:

- roadmap.json marks AI-4 and AI-5 completed_at 2026-08-07.
- commit 336011a (2026-08-07) delivered BOTH:
    AI-4 -> core/ai_serverless/{handler,vercel_handler,__init__}.py
    AI-5 -> core/ai/cost_tracker.py
  and tests/test_ai_serverless.py (13) + tests/test_budget_monitor.py (37)
  pass today.
- memory/builds.json holds builds 1b2ba544 (AI-4) and 65f990f9 (AI-5) as
  FAILED, created 2026-08-10 (three days AFTER the completion date) --
  a later, separate re-attempt that failed. Neither build wrote files
  (AI-4: "reported success but made no changes"; AI-5: no coding provider).

So the roadmap completion is TRUE (verified deliverables); the build_id
references are STALE. Neither is flipped to success: the failed builds stay
FAILED (they are an honest historical record of a *different* attempt) and
are annotated as orphaned; the phases keep status=completed but have their
bogus build_id cleared and the reason documented.

Idempotent: re-running adds nothing.
"""
import json
from datetime import datetime, timezone

BUILDS = "/opt/ai-orchestrator/memory/builds.json"
ROADMAP = "/opt/ai-orchestrator/roadmap.json"

NOW = datetime.now(timezone.utc).isoformat()

BUILD_RECONCILIATION = {
    "1b2ba544": (
        "Orphaned re-attempt (2026-08-10) of AI-4, which was already "
        "completed 2026-08-07 by commit 336011a (core/ai_serverless/, 13 "
        "tests green). This build failed honestly ('reported success but "
        "made no changes'); it is not the delivering build and its failure "
        "does not affect AI-4's verified deliverables. Left FAILED as the "
        "historical record. See roadmap.json AI-4 completion_correction."
    ),
    "65f990f9": (
        "Orphaned re-attempt (2026-08-10) of AI-5, which was already "
        "completed 2026-08-07 by commit 336011a (core/ai/cost_tracker.py, 37 "
        "budget-monitor tests green). This build failed honestly (no coding "
        "provider reachable); it is not the delivering build. Left FAILED as "
        "the historical record. See roadmap.json AI-5 completion_correction."
    ),
}

PHASE_NOTE = (
    "build_id reference reconciled 2026-09-20: the stated build was a later, "
    "failed re-attempt (created 2026-08-10), not the delivering build. The "
    "phase was genuinely completed 2026-08-07 by commit 336011a, whose "
    "deliverables are present in code and whose tests pass today. Roadmap "
    "status=completed is correct; the stale build_id has been cleared."
)


def load(path):
    with open(path) as f:
        return json.load(f)


def save(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f, indent=2)
        f.write("\n")


def main():
    builds = load(BUILDS)
    records = builds["records"] if isinstance(builds, dict) else builds

    build_changes = 0
    for b in records:
        note = BUILD_RECONCILIATION.get(str(b.get("id")))
        if note and b.get("reconciliation") != note:
            b["reconciliation"] = note
            b["reconciled_at"] = NOW
            build_changes += 1
    save(BUILDS, builds)
    print(f"builds: {build_changes} record(s) annotated")
    check = load(BUILDS)
    cr = check["records"] if isinstance(check, dict) else check
    print("verify after save:",
          [(x["id"], "reconciliation" in x) for x in cr
           if str(x.get("id")) in BUILD_RECONCILIATION])

    roadmap = load(ROADMAP)
    phase_changes = 0
    for p in roadmap["phases"]:
        if p.get("id") not in ("AI-4", "AI-5"):
            continue
        if p.get("completed_at") != "2026-08-07":
            print(f"WARNING: {p['id']} completed_at is {p.get('completed_at')!r}; "
                  f"not touching status")
            continue
        if p.get("build_id") is not None:
            p["stale_build_id"] = p.pop("build_id")
            p["build_id_reconciliation"] = PHASE_NOTE
            p["updated_at"] = NOW
            phase_changes += 1
        elif p.get("build_id_reconciliation") != PHASE_NOTE:
            p["build_id_reconciliation"] = PHASE_NOTE
            p["updated_at"] = NOW
            phase_changes += 1
    save(ROADMAP, roadmap)
    print(f"roadmap: {phase_changes} phase(s) reconciled")


if __name__ == "__main__":
    main()
