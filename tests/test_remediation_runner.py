from core.approval import create_request, create_build_approval, load_requests, approve, transition_request
from core.remediation import load_remediations
from core.remediation_memory import get_history
from core.remediation_runner import process, get_approved
from core.execution_audit import history as audit_history


NONEXISTENT_SERVICE = "definitely-not-a-real-container-xyz"


def test_get_approved_excludes_build_architecture_and_deploy_approvals():
    # 13N: build approvals (build_id set) share the same status="approved"
    # queue as genuine incident-remediation approvals (build_id never set)
    # -- get_approved() must not sweep the former into Docker remediation.
    incident_request = create_request("restart_container", NONEXISTENT_SERVICE, "reason", incident_id="inc1")
    approve(incident_request["id"])

    build_request = create_build_approval(
        build_id="build-123", phase_id="17X", approval_type="deploy",
        title="Approve deployment for 17X", description="desc", risk="low",
        requested_action="approve_deploy",
    )
    transition_request(build_request["id"], "approved")

    approved = get_approved()

    assert [r["id"] for r in approved] == [incident_request["id"]]


def test_process_does_not_execute_an_approved_build_deploy_request():
    build_request = create_build_approval(
        build_id="build-123", phase_id="17X", approval_type="deploy",
        title="Approve deployment for 17X", description="desc", risk="low",
        requested_action="approve_deploy",
    )
    transition_request(build_request["id"], "approved")

    results = process()

    assert results == []
    assert load_requests()[0]["status"] == "approved"


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


def test_process_writes_execution_audit_entry_with_operator_identity():
    request = create_request("restart_container", NONEXISTENT_SERVICE, "reason", incident_id="inc1")
    approve(request["id"], operator="alice")

    process()

    entries = audit_history()
    assert len(entries) == 1
    assert entries[0]["operator"] == "alice"
    assert entries[0]["action"] == "restart_container"
    assert entries[0]["service"] == NONEXISTENT_SERVICE
    assert entries[0]["request_id"] == request["id"]


def test_process_records_system_operator_when_no_human_approved():
    request = create_request("restart_container", NONEXISTENT_SERVICE, "reason", incident_id="inc1")
    approve(request["id"])

    process()

    entries = audit_history()
    assert entries[0]["operator"] == "unknown"


def test_process_records_what_happened_in_remediation_history():
    create_request(
        "restart_container", NONEXISTENT_SERVICE,
        "Repeated critical incident: Container unhealthy", incident_id="inc1"
    )
    approve(load_requests()[0]["id"])

    process()

    entry = get_history()[0]
    assert entry["issue"] == "Repeated critical incident: Container unhealthy"


def test_process_records_actual_decision_reasoning_as_root_cause():
    from core.incident_manager import create_incident
    from core.decision_engine import evaluate_incidents

    create_incident(NONEXISTENT_SERVICE, "boom", "critical")
    create_incident(NONEXISTENT_SERVICE, "boom", "critical")
    incident = create_incident(NONEXISTENT_SERVICE, "boom", "critical")

    decisions = evaluate_incidents()
    approve(decisions[0]["approval_id"])

    process()

    entry = get_history()[0]
    assert entry["root_cause"] == decisions[0]["reason"]
    assert "severity=critical" in entry["root_cause"]
