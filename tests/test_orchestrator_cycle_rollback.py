import core.orchestrator_cycle as orchestrator_cycle
from core.remediation import create_remediation, start_remediation, complete_remediation, load_remediations


def test_failed_verification_triggers_rollback_attempt(monkeypatch):
    remediation = create_remediation(
        approval_id="a1", trace_id="inc1", action="restart_container", service="svc-a"
    )
    start_remediation(remediation["id"], snapshot={"before": "running"})
    complete_remediation(remediation["id"], {"status": "success"})

    monkeypatch.setattr(orchestrator_cycle, "refresh_state", lambda: {})
    monkeypatch.setattr(orchestrator_cycle, "analyze", lambda: [])
    monkeypatch.setattr(orchestrator_cycle, "evaluate_incidents", lambda: [])
    monkeypatch.setattr(orchestrator_cycle, "process", lambda: [
        {"service": "svc-a", "trace_id": "inc1", "remediation_id": remediation["id"], "status": "success"}
    ])
    monkeypatch.setattr(
        orchestrator_cycle,
        "verify_service",
        lambda service, trace_id=None: {"status": "unresolved", "service": service}
    )

    orchestrator_cycle.run_cycle()

    updated = [r for r in load_remediations() if r["id"] == remediation["id"]][0]
    assert updated["rollback"]["attempted"] is True


def test_resolved_verification_does_not_trigger_rollback(monkeypatch):
    remediation = create_remediation(
        approval_id="a1", trace_id="inc1", action="restart_container", service="svc-a"
    )
    start_remediation(remediation["id"], snapshot={"before": "running"})
    complete_remediation(remediation["id"], {"status": "success"})

    monkeypatch.setattr(orchestrator_cycle, "refresh_state", lambda: {})
    monkeypatch.setattr(orchestrator_cycle, "analyze", lambda: [])
    monkeypatch.setattr(orchestrator_cycle, "evaluate_incidents", lambda: [])
    monkeypatch.setattr(orchestrator_cycle, "process", lambda: [
        {"service": "svc-a", "trace_id": "inc1", "remediation_id": remediation["id"], "status": "success"}
    ])
    monkeypatch.setattr(
        orchestrator_cycle,
        "verify_service",
        lambda service, trace_id=None: {"status": "resolved", "service": service}
    )

    orchestrator_cycle.run_cycle()

    updated = [r for r in load_remediations() if r["id"] == remediation["id"]][0]
    assert "rollback" not in updated
