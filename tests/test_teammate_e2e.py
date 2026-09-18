"""Real end-to-end acceptance for §46/§47 — hits the live model fabric.

Skipped by default (``KAI_E2E=1`` to run). These are the only teammate tests
that make real model calls; they prove the runner genuinely executes a task
via ``ai_router.delegate`` and returns a real result.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("KAI_E2E", "") != "1",
    reason="set KAI_E2E=1 to run the live model end-to-end acceptance tests",
)


def _text(result):
    if isinstance(result, str):
        return result
    resp = result.get("response") if isinstance(result, dict) else result
    if isinstance(resp, dict):
        for k in ("response", "text", "content"):
            if isinstance(resp.get(k), str):
                return resp[k]
        return str(resp)
    return str(resp or "")


def test_e2e_46_real_teammate_runs_a_real_task():
    from core.teammate.runtime import get_runtime, reset_runtime

    reset_runtime()
    rt = get_runtime()
    result = rt.create_teammate("researcher")
    mate = rt.registry.get(result.teammate.id)
    assert mate is not None
    assert mate.status == "READY"
    assert mate.skills, "teammate must have real skills assigned"

    out = rt.task_runner(
        mate, "inspect_repository",
        instruction="In one short sentence, say what a git repository is.",
    )
    text = _text(out).strip()
    assert len(text) > 5, f"model returned no usable output: {text!r}"
    assert any(h.get("decision") == "allow" for h in mate.security_history)


def test_e2e_47_real_team_executes_a_feature_mission():
    from core.teammate.runtime import get_runtime, reset_runtime
    from core.teammate.engine import WorkforceEngine

    reset_runtime()
    engine = WorkforceEngine(runtime=get_runtime(), model_verify=False)
    mission = engine.create_mission(
        "Build a small software feature: a Python function add(a, b) that returns a + b.",
        execute=True,
    )
    assert mission["status"] == "COMPLETED", mission
    assert all(t["status"] == "COMPLETED" for t in mission["tasks"]), mission["tasks"]
    assert mission["verification"]["passed"] is True
    assert len(mission["team_member_ids"]) >= 4
