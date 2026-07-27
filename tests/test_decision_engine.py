from core.incident_manager import create_incident
from core.decision_engine import evaluate_incidents, load_decisions
from core.approval import load_requests, approve, mark_executed


def make_critical_incident(service="svc-a"):
    create_incident(service, "boom", "critical")
    create_incident(service, "boom", "critical")
    return create_incident(service, "boom", "critical")


def test_no_decision_for_non_actionable_incident():
    create_incident("svc-a", "Healthy", "info")

    decisions = evaluate_incidents()

    assert decisions == []


def test_repeated_critical_incident_produces_decision_with_reasoning_fields():
    incident = make_critical_incident()

    decisions = evaluate_incidents()

    assert len(decisions) == 1
    decision = decisions[0]

    assert decision["trace_id"] == incident["id"]
    assert decision["problem"] == "boom"
    assert decision["recommended_action"] == "restart_container"
    assert decision["confidence"] == 85
    assert 0 <= decision["cause_probability"] <= 1
    assert decision["risk_level"] == "low"
    assert decision["requires_approval"] is True
    assert decision["status"] == "proposed"


def test_decision_creates_linked_approval_request():
    make_critical_incident()

    decisions = evaluate_incidents()

    requests = load_requests()
    assert len(requests) == 1
    assert decisions[0]["approval_id"] == requests[0]["id"]


def test_running_evaluate_twice_does_not_duplicate_decision_or_approval():
    make_critical_incident()

    evaluate_incidents()
    decisions = evaluate_incidents()

    assert len(decisions) == 1
    assert len(load_decisions()) == 1
    assert len(load_requests()) == 1


def test_decision_status_syncs_with_approval_lifecycle():
    make_critical_incident()

    decisions = evaluate_incidents()
    approve(decisions[0]["approval_id"])

    decisions = evaluate_incidents()
    assert decisions[0]["status"] == "approved"

    mark_executed(decisions[0]["approval_id"])
    decisions = evaluate_incidents()
    assert decisions[0]["status"] == "executed"
