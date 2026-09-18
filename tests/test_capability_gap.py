"""Autonomous capability-gap loop (§31 / §43).

When a mission requests a capability no teammate has, the engine must
create/reuse a teammate that provides it and journal the gap + resolution.
The same loop is exposed as a maintenance scan for orphaned mission tasks.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_singletons():
    from core.teammate.runtime import reset_runtime
    from core.teammate.routes import reset_engine
    reset_runtime()
    reset_engine()
    yield
    reset_runtime()
    reset_engine()


@pytest.fixture
def runtime():
    from core.teammate.runtime import get_runtime
    return get_runtime()


@pytest.fixture
def engine(runtime):
    from core.teammate.engine import WorkforceEngine
    return WorkforceEngine(runtime=runtime, model_verify=False)


def test_capability_gap_creates_teammate_and_journals(engine, runtime):
    mission = engine.create_mission(
        "Perform a small capability task",
        skills=["write_code"], specialization="coder", execute=False)

    member_id = mission["team_member_ids"][0]
    member = runtime.registry.get(member_id)
    assert member is not None and "write_code" in member.skills
    assert member.status in ("READY", "ASSIGNED")

    gaps = engine.list_capability_gaps()
    mine = [g for g in gaps if g.get("mission_id") == mission["id"]]
    assert mine, gaps
    gap = mine[-1]
    assert "write_code" in gap["missing_skills"]
    assert gap["resolution"]["teammate_id"] == member_id
    assert gap["status"] == "resolved"


def test_no_gap_when_capability_already_covered(engine, runtime):
    engine.create_teammate("coder")  # materialize a covering teammate first

    mission = engine.create_mission(
        "Second capability task", skills=["write_code"],
        specialization="coder", execute=False)

    gaps = engine.list_capability_gaps()
    assert not [g for g in gaps if g.get("mission_id") == mission["id"]], gaps


def test_scan_resolves_orphaned_mission_task(engine, runtime):
    mission = engine.create_mission(
        "Orphan task mission", skills=["write_code"],
        specialization="coder", execute=False)
    member_id = mission["team_member_ids"][0]
    runtime.registry.retire(member_id, reason="test retirement")

    stored = engine.get_mission(mission["id"])
    for task in stored["tasks"]:
        task["teammate_id"] = None
    engine._put_mission(stored)

    resolved = engine.scan_capability_gaps()
    assert any(r["mission_id"] == mission["id"] for r in resolved), resolved

    after = engine.get_mission(mission["id"])
    assert all(t.get("teammate_id") for t in after["tasks"])
    for task in after["tasks"]:
        mate = runtime.registry.get(task["teammate_id"])
        assert mate is not None and "write_code" in mate.skills
        assert mate.status != "RETIRED"

    assert any(g["mission_id"] == mission["id"]
               for g in engine.list_capability_gaps())


def test_maintenance_endpoint_reports_gap_resolution(engine, runtime, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from core.teammate import routes as routes_mod

    monkeypatch.setattr(routes_mod, "_load_api_token", lambda: "test-token")
    monkeypatch.setattr(routes_mod, "_ENGINE", engine)
    app = FastAPI()
    app.include_router(routes_mod.teammate_router)
    client = TestClient(app)
    h = {"Authorization": "Bearer test-token"}

    engine.create_mission("Endpoint capability task", skills=["write_code"],
                          specialization="coder", execute=False)

    r = client.get("/api/workforce/capability-gaps", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["gaps"]

    r = client.post("/api/workforce/maintenance", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "retired" in body and "gaps_resolved" in body
