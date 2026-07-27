from core.approval import create_request, load_requests, approve
from core.remediation import load_remediations
from core.remediation_memory import get_history
from core.remediation_runner import process


NONEXISTENT_SERVICE = "definitely-not-a-real-container-xyz"


def test_process_executes_approved_request_against_missing_container():
    request = create_request("restart_container", NONEXISTENT_SERVICE, "reason", incident_id="inc1")
    approve(request["id"])

    results = process()

    assert len(results) == 1
    assert results[0]["status"] == "failed"
    assert results[0]["service"] == NONEXISTENT_SERVICE
    assert results[0]["trace_id"] == "inc1"


def test_process_creates_remediation_record_linked_to_incident():
    request = create_request("restart_container", NONEXISTENT_SERVICE, "reason", incident_id="inc1")
    approve(request["id"])

    process()

    remediations = load_remediations()
    assert len(remediations) == 1
    assert remediations[0]["trace_id"] == "inc1"
    assert remediations[0]["approval_id"] == request["id"]
    assert remediations[0]["status"] == "failed"
    assert remediations[0]["snapshot"]["before"] == "unknown"
    assert remediations[0]["snapshot"]["command"] == f"restart_container on {NONEXISTENT_SERVICE}"
    assert remediations[0]["snapshot"]["expected_result"] == "container running"


def test_process_marks_approval_executed():
    request = create_request("restart_container", NONEXISTENT_SERVICE, "reason", incident_id="inc1")
    approve(request["id"])

    process()

    requests = load_requests()
    assert requests[0]["status"] == "executed"


def test_process_records_outcome_in_remediation_memory():
    request = create_request("restart_container", NONEXISTENT_SERVICE, "reason", incident_id="inc1")
    approve(request["id"])

    process()

    history = get_history()
    assert len(history) == 1
    assert history[0]["incident"] == "inc1"
    assert history[0]["action"] == "restart_container"
    assert history[0]["result"] == "failed"


def test_process_ignores_non_approved_requests():
    create_request("restart_container", NONEXISTENT_SERVICE, "reason", incident_id="inc1")

    results = process()

    assert results == []
