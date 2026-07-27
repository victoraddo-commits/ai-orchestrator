from fastapi.testclient import TestClient

from core.api import app
from core.incident_manager import create_incident
from core.decision_engine import evaluate_incidents
from core.approval import create_request
from core.remediation import create_remediation
from core.verification import verify_service


client = TestClient(app)


def test_health_endpoint_returns_ok_shape():
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert "status" in body
    assert "findings" in body


def test_incidents_endpoint_returns_created_incidents():
    create_incident("svc-a", "boom", "critical")

    response = client.get("/incidents")

    assert response.status_code == 200
    incidents = response.json()
    assert len(incidents) == 1
    assert incidents[0]["service"] == "svc-a"


def test_decisions_endpoint_returns_created_decisions():
    create_incident("svc-a", "boom", "critical")
    create_incident("svc-a", "boom", "critical")
    create_incident("svc-a", "boom", "critical")
    evaluate_incidents()

    response = client.get("/decisions")

    assert response.status_code == 200
    decisions = response.json()
    assert len(decisions) == 1
    assert decisions[0]["recommended_action"] == "restart_container"


def test_approvals_endpoint_returns_created_approvals():
    create_request("restart_container", "svc-a", "reason", incident_id="inc1")

    response = client.get("/approvals")

    assert response.status_code == 200
    approvals = response.json()
    assert len(approvals) == 1
    assert approvals[0]["status"] == "pending"


def test_actions_endpoint_returns_remediation_records():
    create_remediation(approval_id="a1", trace_id="inc1", action="restart_container", service="svc-a")

    response = client.get("/actions")

    assert response.status_code == 200
    actions = response.json()
    assert len(actions) == 1
    assert actions[0]["action"] == "restart_container"


def test_verifications_endpoint_returns_verification_history():
    verify_service("svc-a", trace_id="inc1")

    response = client.get("/verifications")

    assert response.status_code == 200
    verifications = response.json()
    assert len(verifications) == 1
    assert verifications[0]["service"] == "svc-a"


def test_learning_endpoint_returns_action_classification():
    from core.remediation_memory import record_result

    for _ in range(4):
        record_result("inc1", "restart_container", "success")

    response = client.get("/learning")

    assert response.status_code == 200
    body = response.json()
    assert body["restart_container"]["recommendation"] == "trusted"


def auth_headers():
    import core.api as api_module
    return {"Authorization": f"Bearer {api_module._load_api_token()}"}


def test_write_endpoints_reject_requests_with_no_token():
    request = create_request("restart_container", "svc-a", "reason", incident_id="inc1")

    response = client.post(f"/approvals/{request['id']}/approve")

    assert response.status_code == 401


def test_write_endpoints_reject_requests_with_wrong_token():
    request = create_request("restart_container", "svc-a", "reason", incident_id="inc1")

    response = client.post(
        f"/approvals/{request['id']}/approve",
        headers={"Authorization": "Bearer not-the-real-token"},
    )

    assert response.status_code == 401


def test_approve_endpoint_transitions_request_and_records_server_derived_operator():
    request = create_request("restart_container", "svc-a", "reason", incident_id="inc1")

    response = client.post(f"/approvals/{request['id']}/approve", headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"
    assert body["approved_by"] == "cloudcli-plugin"


def test_client_supplied_operator_field_is_ignored_not_trusted():
    request = create_request("restart_container", "svc-a", "reason", incident_id="inc1")

    # An attacker (or a buggy caller) claiming to be "alice" must not be able
    # to forge the audit trail -- the operator identity comes from the
    # verified token, never from the request body.
    response = client.post(
        f"/approvals/{request['id']}/approve",
        json={"operator": "alice"},
        headers=auth_headers(),
    )

    assert response.status_code == 200
    assert response.json()["approved_by"] == "cloudcli-plugin"


def test_reject_endpoint_transitions_request_and_records_server_derived_operator():
    request = create_request("restart_container", "svc-a", "reason", incident_id="inc1")

    response = client.post(f"/approvals/{request['id']}/reject", headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "rejected"
    assert body["rejected_by"] == "cloudcli-plugin"


def test_approve_endpoint_works_without_a_body():
    request = create_request("restart_container", "svc-a", "reason", incident_id="inc1")

    response = client.post(f"/approvals/{request['id']}/approve", headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["status"] == "approved"


def test_approve_endpoint_returns_404_for_unknown_request():
    response = client.post("/approvals/does-not-exist/approve", headers=auth_headers())

    assert response.status_code == 404


def test_approve_endpoint_returns_409_for_illegal_transition():
    request = create_request("restart_container", "svc-a", "reason", incident_id="inc1")
    client.post(f"/approvals/{request['id']}/reject", headers=auth_headers())

    response = client.post(f"/approvals/{request['id']}/approve", headers=auth_headers())

    assert response.status_code == 409
