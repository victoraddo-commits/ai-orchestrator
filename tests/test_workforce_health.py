"""§27 worker health + §18 resource snapshot."""
from __future__ import annotations


def test_registry_health_report_rates_and_aggregate():
    from core.teammate.registry import TeammateRegistry

    reg = TeammateRegistry()
    t = reg.create({"name": "coder-x", "specialization": "coder",
                    "skills": ["write_code"], "capabilities": ["code"]})
    reg.record_performance(t.id, success=True, latency_ms=100,
                           model="kai_coder", skill_id="write_code")
    reg.record_performance(t.id, success=False, latency_ms=300,
                           model="llama_coder_cpu", skill_id="write_code")

    rep = reg.health_report()
    row = rep["teammates"][t.id]
    assert row["tasks_completed"] == 1
    assert row["tasks_failed"] == 1
    assert row["success_rate"] == 0.5
    assert row["error_rate"] == 0.5
    assert row["last_model"] == "llama_coder_cpu"
    assert set(row["models"]) == {"kai_coder", "llama_coder_cpu"}
    assert row["alive"] is True
    assert rep["counts"]["total"] == 1
    assert rep["by_status"]["CREATED"] == 1


def test_health_report_marks_quarantined_and_retired():
    from core.teammate.registry import TeammateRegistry

    reg = TeammateRegistry()
    a = reg.create({"name": "a", "specialization": "coder",
                    "skills": [], "capabilities": []})
    b = reg.create({"name": "b", "specialization": "coder",
                    "skills": [], "capabilities": []})
    reg.transition(a.id, "CONFIGURED", "test")
    reg.transition(a.id, "READY", "test")
    reg.transition(a.id, "ASSIGNED", "test")
    reg.transition(a.id, "EXECUTING", "test")
    reg.record_failure(a.id, "security_violation", "bad action")
    reg.retire(b.id, "done")

    rep = reg.health_report()
    assert rep["teammates"][a.id]["quarantined"] is True
    assert rep["counts"]["quarantined"] == 1
    assert rep["teammates"][b.id]["alive"] is False


def test_engine_workforce_health_includes_resources():
    from core.teammate.routes import reset_engine
    from core.teammate.runtime import reset_runtime

    reset_runtime()
    reset_engine()
    try:
        from core.teammate.engine import WorkforceEngine
        out = WorkforceEngine().workforce_health()
        assert "health" in out and "resources" in out
        assert out["resources"]["max_workers"] >= 1
        assert "workers" in out["resources"]
        assert out["resources"]["schema"] == 1
    finally:
        reset_runtime()
        reset_engine()
