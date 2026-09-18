#!/usr/bin/env python
"""§23 dependency management — live DAG evidence (2026-09-18).

Prints the live Skill Registry dependency graph and the parallel/sequential
partition + DAG execution order the executor derives for a feature mission,
using a stubbed model runner (no network inference). Proves dependencies are
declared and honoured: write_code completes before run_tests/run_security_scan,
and a failed dependency blocks its dependents instead of running them.

Run:  .venv/bin/python scripts/kai_dependency_graph_demo.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.ai.ai_router as ai_router  # noqa: E402
from core.teammate.execution import Team  # noqa: E402
from core.teammate.runtime import get_runtime  # noqa: E402


def main() -> int:
    ai_router.delegate = lambda description, **kw: {
        "provider": kw.get("provider"), "response": "stub", "duration_ms": 1,
        "attempts": []}

    rt = get_runtime()
    graph = {
        rec.skill_id: list(rec.dependencies or [])
        for rec in rt.skills.list()
    }
    graph = {k: v for k, v in graph.items() if v}

    # A QA mission: verify_endpoint must NOT drag the deploy chain in.
    ordered = ["inspect_repository", "write_code", "run_tests", "verify_endpoint"]
    team = Team(mission_id="demo", members=[], plan=None,
                skill_registry=rt.skills)
    run_order = []

    def runner(_mate, skill):
        run_order.append(skill)
        return "ok"

    team.execute(runner, tasks=ordered)

    # A failed dependency blocks its dependents (never runs them).
    blocked_team = Team(mission_id="demo2", members=[], plan=None,
                        skill_registry=rt.skills)
    blocked_run = []

    def failing(_mate, skill):
        blocked_run.append(skill)
        if skill == "write_code":
            raise RuntimeError("injected build failure")
        return "ok"

    blocked = blocked_team.execute(failing,
                                   tasks=["write_code", "run_tests", "deploy_service"])

    print(json.dumps({
        "dependency_graph": graph,
        "qa_mission_run_order": run_order,
        "write_code_before_run_tests": (
            run_order.index("write_code") < run_order.index("run_tests")),
        "failed_dependency": {
            r.skill_id: r.status for r in blocked},
        "dependent_never_ran": "run_tests" not in blocked_run,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
