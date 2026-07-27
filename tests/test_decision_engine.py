from core.incident_manager import create_incident
from core.decision_engine import evaluate_incidents, load_decisions, analyze_incident
from core.approval import load_requests, approve, mark_executed
from core.remediation_memory import record_result


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


def test_strong_track_record_boosts_confidence():
    for _ in range(4):
        record_result("prior-incident", "restart_container", "success")

    incident = make_critical_incident()

    analysis = analyze_incident(incident, related_incidents=[incident])

    assert analysis["confidence"] > 85
    assert analysis["confidence"] <= 95


def test_poor_track_record_reduces_confidence():
    for _ in range(4):
        record_result("prior-incident", "restart_container", "failed")

    incident = make_critical_incident()

    analysis = analyze_incident(incident, related_incidents=[incident])

    assert analysis["confidence"] < 85


def test_sparse_track_record_does_not_adjust_confidence():
    record_result("prior-incident", "restart_container", "success")

    incident = make_critical_incident()

    analysis = analyze_incident(incident, related_incidents=[incident])

    assert analysis["confidence"] == 85


def test_multiple_other_incidents_on_same_service_lowers_confidence():
    incident = make_critical_incident("svc-a")
    other1 = create_incident("svc-a", "Disk pressure", "warning")
    other2 = create_incident("svc-a", "Memory pressure", "warning")

    analysis = analyze_incident(incident, related_incidents=[incident, other1, other2])

    assert analysis["confidence"] < 85
    assert "svc-a" in analysis["reason"] or "noisy" in analysis["reason"].lower() or "other" in analysis["reason"].lower()


def test_cause_probability_reflects_adjusted_confidence():
    for _ in range(4):
        record_result("prior-incident", "restart_container", "success")

    incident = make_critical_incident()
    decisions = evaluate_incidents()

    assert decisions[0]["cause_probability"] == round(decisions[0]["confidence"] / 100, 2)
