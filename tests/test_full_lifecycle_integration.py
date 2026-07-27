from core.incident_manager import create_incident, mark_investigating, mark_approved as mark_incident_approved
from core.decision_engine import evaluate_incidents
from core.approval import approve, load_requests
from core.remediation_runner import process
from core.remediation import load_remediations
from core.verification import verify_service
from core.remediation_memory import get_history


NONEXISTENT_SERVICE = "definitely-not-a-real-container-xyz"


def test_complete_incident_lifecycle_is_linked_by_a_single_trace_id():
    incident = create_incident(NONEXISTENT_SERVICE, "boom", "critical")
    create_incident(NONEXISTENT_SERVICE, "boom", "critical")
    incident = create_incident(NONEXISTENT_SERVICE, "boom", "critical")

    decisions = evaluate_incidents()
    assert len(decisions) == 1
    decision = decisions[0]

    request = load_requests()[0]
    approve(request["id"], operator="alice")

    remediation_results = process()
    assert len(remediation_results) == 1

    verification = verify_service(
        remediation_results[0]["service"],
        trace_id=remediation_results[0]["trace_id"]
    )

    remediation = load_remediations()[0]

    trace_id = incident["id"]
    assert decision["trace_id"] == trace_id
    assert request["trace_id"] == trace_id
    assert remediation["trace_id"] == trace_id
    assert verification["trace_id"] == trace_id

    history_entry = get_history()[0]
    assert history_entry["incident"] == trace_id
    assert history_entry["root_cause"] == decision["reason"]


def test_complete_incident_lifecycle_reaches_a_terminal_state_on_every_object():
    incident = create_incident(NONEXISTENT_SERVICE, "boom", "critical")
    create_incident(NONEXISTENT_SERVICE, "boom", "critical")
    incident = create_incident(NONEXISTENT_SERVICE, "boom", "critical")

    decisions = evaluate_incidents()
    request = load_requests()[0]
    approve(request["id"])

    process()

    decisions = evaluate_incidents()
    remediation = load_remediations()[0]

    assert decisions[0]["status"] == "executed"
    assert load_requests()[0]["status"] == "executed"
    assert remediation["status"] == "failed"

    mark_investigating(incident["id"])
    mark_incident_approved(incident["id"])

    from core.incident_manager import load_incidents
    updated_incident = [i for i in load_incidents() if i["id"] == incident["id"]][0]
    assert updated_incident["status"] == "approved"
