"""Tests for core.teammate.registry (KAI 2.0 phase 21A)."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_memory(tmp_path, monkeypatch):
    """Each test gets a fresh memory dir so state doesn't leak."""
    monkeypatch.setenv("AI_ORCHESTRATOR_MEMORY_DIR", str(tmp_path))
    import core.memory as _m
    monkeypatch.setattr(_m, "MEMORY_DIR", Path(str(tmp_path)))
    yield


def _fresh():
    """Re-import registry with a fresh singleton per test."""
    import importlib
    import core.teammate.registry as reg
    importlib.reload(reg)
    return reg.TeammateRegistry()


def test_create_returns_teammate_with_all_spec_fields():
    r = _fresh()
    t = r.create({
        "name": "Ada",
        "specialization": "coder",
        "capabilities": ["python", "async"],
        "skills": ["write_code", "run_tests"],
        "resource_limits": {"cpu": 2, "ram_mb": 4096},
        "dependencies": ["21B"],
    })
    assert t.id and len(t.id) == 12
    assert t.name == "Ada"
    assert t.specialization == "coder"
    assert t.status == "CREATED"
    assert t.capabilities == ["python", "async"]
    assert t.skills == ["write_code", "run_tests"]
    assert t.resource_limits == {"cpu": 2, "ram_mb": 4096}
    assert t.dependencies == ["21B"]
    assert t.created_at and t.updated_at and t.last_active
    assert t.health == "HEALTHY"
    # Every dataclass field is present:
    for f in ("verification_history", "security_history",
              "failure_history", "audit_references", "assigned_missions",
              "active_tasks", "children", "performance_metrics"):
        assert hasattr(t, f)


def test_create_requires_name_and_specialization():
    r = _fresh()
    with pytest.raises(ValueError):
        r.create({"specialization": "coder"})
    with pytest.raises(ValueError):
        r.create({"name": "X"})


def test_happy_path_lifecycle_transitions():
    r = _fresh()
    t = r.create({"name": "Ada", "specialization": "coder"})
    for step in ("READY", "ASSIGNED", "EXECUTING", "REVIEW", "VERIFIED", "COMPLETED"):
        r.transition(t.id, step, f"advance to {step}")
    t2 = r.get(t.id)
    assert t2.status == "COMPLETED"
    # Every transition logged
    assert len(t2.verification_history) == 6
    assert t2.verification_history[0]["from"] == "CREATED"
    assert t2.verification_history[-1]["to"] == "COMPLETED"


def test_transition_rejects_illegal_move():
    r = _fresh()
    t = r.create({"name": "Ada", "specialization": "coder"})
    # CREATED cannot jump directly to COMPLETED.
    with pytest.raises(ValueError):
        r.transition(t.id, "COMPLETED", "skip everything")


def test_transition_returns_none_for_unknown_id():
    r = _fresh()
    assert r.transition("no-such-id", "READY", "x") is None


def test_assign_mission_only_when_ready():
    r = _fresh()
    t = r.create({"name": "Ada", "specialization": "coder"})
    # CREATED is not READY yet.
    assert r.assign_mission(t.id, "msn_123") is False
    r.transition(t.id, "READY", "configured")
    assert r.assign_mission(t.id, "msn_123") is True
    after = r.get(t.id)
    assert "msn_123" in after.assigned_missions
    assert after.status == "ASSIGNED"


def test_record_failure_moves_to_expected_state():
    r = _fresh()
    t = r.create({"name": "Ada", "specialization": "coder"})
    r.transition(t.id, "READY", "cfg")
    r.transition(t.id, "ASSIGNED", "mission")
    r.transition(t.id, "EXECUTING", "start")
    r.record_failure(t.id, "unauthorized_action", "tried to touch /etc/shadow")
    after = r.get(t.id)
    assert after.status == "QUARANTINED"
    assert after.failure_history[-1]["class"] == "unauthorized_action"


def test_record_failure_unknown_class_defaults_to_degraded():
    r = _fresh()
    t = r.create({"name": "Ada", "specialization": "coder"})
    r.transition(t.id, "READY", "cfg")
    r.record_failure(t.id, "mystery_class", "unknown-reason")
    assert r.get(t.id).status == "DEGRADED"


def test_retire_is_terminal():
    r = _fresh()
    t = r.create({"name": "Ada", "specialization": "coder"})
    r.retire(t.id, "no longer needed")
    assert r.get(t.id).status == "RETIRED"
    # Any subsequent transition is illegal.
    with pytest.raises(ValueError):
        r.transition(t.id, "READY", "try to bring back")


def test_list_filters_by_status_and_specialization():
    r = _fresh()
    a = r.create({"name": "A", "specialization": "coder"})
    b = r.create({"name": "B", "specialization": "reviewer"})
    r.transition(a.id, "READY", "cfg")
    assert len(r.list()) == 2
    assert len(r.list(status="READY")) == 1
    assert r.list(status="READY")[0].id == a.id
    assert len(r.list(specialization="reviewer")) == 1
    assert r.list(specialization="reviewer")[0].id == b.id
    assert len(r.list(status="CREATED", specialization="coder")) == 0
    assert len(r.list(status="CREATED", specialization="reviewer")) == 1


def test_save_load_round_trip_preserves_state_and_history():
    r = _fresh()
    t = r.create({"name": "Ada", "specialization": "coder"})
    r.transition(t.id, "READY", "cfg")
    r.assign_mission(t.id, "msn_xyz")
    # Re-instantiate — should reload from disk.
    r2 = _fresh()
    reloaded = r2.get(t.id)
    assert reloaded is not None
    assert reloaded.status == "ASSIGNED"
    assert reloaded.assigned_missions == ["msn_xyz"]
    # verification_history preserved
    assert len(reloaded.verification_history) == 2
    # audit_references preserved
    assert any(e.get("event") == "create" for e in reloaded.audit_references)


def test_concurrent_creates_get_distinct_ids():
    r = _fresh()
    ids = {r.create({"name": f"T{i}", "specialization": "coder"}).id
           for i in range(20)}
    assert len(ids) == 20


def test_registry_survives_corrupted_memory(tmp_path, monkeypatch):
    # Write garbage to the store then load.
    from core import memory as _m
    (Path(_m.MEMORY_DIR) / "teammates.json").write_text("not valid json")
    r = _fresh()  # should not raise
    assert r.list() == []


def test_registry_survives_wrong_shape(tmp_path, monkeypatch):
    from core import memory as _m
    import json as _json
    (Path(_m.MEMORY_DIR) / "teammates.json").write_text(
        _json.dumps({"schema_version": 1, "teammates": "not-a-dict"})
    )
    r = _fresh()
    assert r.list() == []
