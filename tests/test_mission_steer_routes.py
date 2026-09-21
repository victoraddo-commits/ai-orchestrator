"""Tests for mission steering routes + engine wiring (roadmap 20D).

`core/mission_steering` posts /pause /resume /redirect to
``POST /kai/missions/{id}/steer`` and /stop to ``POST /kai/missions/{id}/execute``.
Both routes previously 404'd, so Telegram/CC steering never reached the
Mission Engine.
"""

import pytest
from fastapi.testclient import TestClient

AUTH = {"Authorization": "Bearer test-bridge"}


@pytest.fixture
def client():
    from core.api import app
    return TestClient(app)


def _mission(status="running"):
    from core.kai import mission_store as ms

    mission = ms.new_mission(objective="ship the diagnostics panel")
    if status != "proposed":
        ms.transition_mission(mission["id"], "approved")
        if status == "running":
            ms.transition_mission(mission["id"], "running")
    return mission["id"]


def test_steer_requires_operator(client):
    r = client.post("/kai/missions/msn_x/steer", json={"action": "pause"})
    assert r.status_code == 401


def test_pause_persists_to_engine(client):
    from core.kai.mission_store import get_mission

    mid = _mission("running")
    r = client.post(f"/kai/missions/{mid}/steer", json={"action": "pause"}, headers=AUTH)
    assert r.status_code == 200
    assert r.json()["mission"]["status"] == "paused"
    persisted = get_mission(mid)
    assert persisted["status"] == "paused"
    assert any(h.get("status") == "paused" for h in persisted["history"])


def test_resume_after_pause(client):
    mid = _mission("running")
    client.post(f"/kai/missions/{mid}/steer", json={"action": "pause"}, headers=AUTH)
    r = client.post(f"/kai/missions/{mid}/steer", json={"action": "resume"}, headers=AUTH)
    assert r.status_code == 200
    assert r.json()["mission"]["status"] == "running"


def test_stop_via_execute(client):
    from core.kai.mission_store import get_mission

    mid = _mission("running")
    r = client.post(f"/kai/missions/{mid}/execute", json={"action": "stop"}, headers=AUTH)
    assert r.status_code == 200
    assert r.json()["mission"]["status"] == "stopped"
    assert get_mission(mid)["status"] == "stopped"


def test_redirect_updates_objective_and_records_history(client):
    from core.kai.mission_store import get_mission

    mid = _mission("running")
    r = client.post(f"/kai/missions/{mid}/steer",
                    json={"action": "redirect", "objective": "new objective entirely"},
                    headers=AUTH)
    assert r.status_code == 200
    persisted = get_mission(mid)
    assert persisted["objective"] == "new objective entirely"
    assert persisted["drift_score"] >= 0.0
    assert any(h.get("status") == "redirected" for h in persisted["history"])


def test_unknown_mission_is_404(client):
    r = client.post("/kai/missions/does-not-exist/steer", json={"action": "pause"}, headers=AUTH)
    assert r.status_code == 404


def test_invalid_transition_is_409(client):
    mid = _mission("proposed")
    r = client.post(f"/kai/missions/{mid}/steer", json={"action": "pause"}, headers=AUTH)
    assert r.status_code == 409


def test_redirect_without_objective_is_422(client):
    mid = _mission("running")
    r = client.post(f"/kai/missions/{mid}/steer", json={"action": "redirect"}, headers=AUTH)
    assert r.status_code == 422


def test_mission_steering_module_end_to_end(client):
    """The module's own poster path must hit a real, working route."""
    from core import mission_steering

    mid = _mission("running")

    def post(path, payload):
        r = client.post(path, json=payload, headers=AUTH)
        return (r.status_code, r.json())

    msg = mission_steering.handle_steer_command(f"/pause {mid}", post=post)
    assert "accepted" in msg

    msg = mission_steering.handle_steer_command(f"/stop {mid}", post=post)
    assert "accepted" in msg
