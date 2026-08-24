"""P11 goals/missions engine tests."""

import pytest

from core import kai_missions as km
from core.kai_tools import builtin  # noqa: F401 — registers real tools


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(km, "MISSIONS_PATH", tmp_path / "missions.json")
    return tmp_path


def _plan():
    return [
        {"tool_id": "kai.system.health", "description": "check health"},
        {"tool_id": "kai.alerts.pending_approvals", "description": "check approvals"},
    ]


def test_goal_and_mission_lifecycle(isolated):
    g = km.create_goal("Keep infra healthy", "zero critical incidents")
    m = km.create_mission("morning sweep", _plan(), goal_id=g["id"])
    assert m["status"] == "planned" and len(m["tasks"]) == 2
    m = km.start_mission(m["id"])
    assert m["status"] == "running"
    res = km.execute_mission(m["id"])
    got = km.get_mission(m["id"])
    # both tasks are SAFE read-only tools — should complete
    assert got["progress_pct"] == 100
    states = [t["state"] for t in got["tasks"]]
    assert all(s == "DONE" for s in states), states
    # review gate: requires_review default True → verifying until approved
    km.verify_mission(m["id"], approved=True, operator="victor")
    assert km.get_mission(m["id"])["status"] == "done"
    goals = km.list_goals()
    assert goals[0]["progress_pct"] == 100


def test_unknown_tool_rejected_at_plan_time(isolated):
    with pytest.raises(ValueError, match="unknown tool"):
        km.create_mission("bad", [{"tool_id": "kai.not.real"}])


def test_cancel_blocks_pending_tasks(isolated):
    m = km.create_mission("sweep", _plan())
    km.cancel_mission(m["id"], reason="operator stop")
    got = km.get_mission(m["id"])
    assert got["status"] == "cancelled"
    assert all(t["state"] in ("BLOCKED",) for t in got["tasks"])


def test_concurrent_mission_cap(isolated):
    ids = [km.create_mission(f"m{i}", _plan())["id"] for i in range(km.MAX_CONCURRENT_MISSIONS)]
    for mid in ids:
        km.start_mission(mid)
    with pytest.raises(ValueError, match="concurrent"):
        km.create_mission("one too many", _plan())


def test_schedule_lifecycle(tmp_path, monkeypatch):
    from core.kai_tools import builtin  # noqa: F401
    from core import kai_missions as km
    monkeypatch.setattr(km, "SCHEDULES_PATH", tmp_path / "schedules.json")
    monkeypatch.setattr(km, "MISSIONS_PATH", tmp_path / "missions.json")
    s = km.create_schedule("daily sweep", "check health",
                           [{"tool_id": "kai.system.health"}],
                           interval_hours=24, requires_review=False)
    assert s["enabled"] and s["last_run"] is None
    due = km.due_schedules()
    assert len(due) == 1                      # never run → due
    res = km.run_due_schedules()
    assert len(res["ran"]) == 1
    st = km.list_schedules()[0]
    assert st["last_run"] is not None         # persisted
    assert km.due_schedules() == []           # not due again
    # SAFE-only enforcement for auto schedules
    import pytest
    with pytest.raises(ValueError, match="may only contain SAFE tools"):
        km.create_schedule("bad", "x", [{"tool_id": "kai.docker.container_action",
                                         "args": {"container": "c", "action": "restart"}}],
                           requires_review=False)
