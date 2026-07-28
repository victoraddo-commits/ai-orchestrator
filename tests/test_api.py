import pytest
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


def test_token_file_is_created_with_owner_only_permissions(tmp_path, monkeypatch):
    import stat
    import core.api as api_module

    token_path = tmp_path / "nested" / "api_token"
    monkeypatch.setattr(api_module, "API_TOKEN_PATH", token_path)

    api_module._load_api_token()

    file_mode = stat.S_IMODE(token_path.stat().st_mode)
    dir_mode = stat.S_IMODE(token_path.parent.stat().st_mode)

    assert file_mode == 0o600
    assert dir_mode == 0o700


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


def test_build_learning_endpoint_returns_templates_and_history():
    response = client.get("/learning/builds")

    assert response.status_code == 200
    body = response.json()
    assert "templates" in body
    assert "history" in body


import json as _json
import core.roadmap_engine as roadmap_engine


@pytest.fixture
def isolated_roadmap(tmp_path, monkeypatch):
    roadmap_path = tmp_path / "roadmap.json"
    roadmap_path.write_text(_json.dumps({
        "schema_version": 1,
        "phases": [
            {"id": "A", "status": "completed", "dependencies": [], "priority": 1},
            {"id": "B", "status": "pending", "dependencies": ["A"], "priority": 2},
        ],
    }))
    monkeypatch.setattr(roadmap_engine, "ROADMAP_PATH", roadmap_path)
    return roadmap_path


def test_roadmap_endpoint_returns_phases(isolated_roadmap):
    response = client.get("/roadmap")

    assert response.status_code == 200
    assert len(response.json()["phases"]) == 2


def test_roadmap_next_endpoint_returns_next_actionable_phase(isolated_roadmap):
    response = client.get("/roadmap/next")

    assert response.status_code == 200
    assert response.json()["id"] == "B"


def test_roadmap_progress_endpoint_returns_summary(isolated_roadmap):
    response = client.get("/roadmap/progress")

    assert response.status_code == 200
    assert response.json()["total"] == 2


def test_roadmap_status_endpoint_requires_auth(isolated_roadmap):
    response = client.post("/roadmap/B/status", json={"status": "completed"})

    assert response.status_code == 401


def test_roadmap_status_endpoint_updates_phase(isolated_roadmap):
    response = client.post("/roadmap/B/status", json={"status": "completed"}, headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["status"] == "completed"


def test_roadmap_status_endpoint_returns_404_for_unknown_phase(isolated_roadmap):
    response = client.post("/roadmap/does-not-exist/status", json={"status": "completed"}, headers=auth_headers())

    assert response.status_code == 404


def test_roadmap_status_endpoint_returns_400_for_invalid_status(isolated_roadmap):
    response = client.post("/roadmap/B/status", json={"status": "not-a-real-status"}, headers=auth_headers())

    assert response.status_code == 400


def test_providers_dashboard_endpoint_returns_all_registered_providers():
    response = client.get("/providers/dashboard")

    assert response.status_code == 200
    body = response.json()
    assert "claude" in body
    assert "gemini" in body
    assert "percent_remaining" in body["groq"]


def test_delegate_endpoint_requires_auth():
    response = client.post("/delegate", json={"description": "Analyze Docker error log"})

    assert response.status_code == 401


def test_delegate_endpoint_routes_and_returns_result(monkeypatch):
    import core.ai_provider as ai_provider

    groq = ai_provider.get_provider("groq")
    monkeypatch.setitem(groq, "available_fn", lambda: True)
    monkeypatch.setitem(groq, "run_text_task", lambda p, timeout=60, project_path=None: "log looks fine")

    response = client.post(
        "/delegate",
        json={"description": "Analyze Docker error log"},
        headers=auth_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "groq"
    assert body["response"] == "log looks fine"


def test_delegate_endpoint_returns_502_when_all_providers_fail(monkeypatch):
    import core.ai_provider as ai_provider

    for name in ("gemini", "claude"):
        provider = ai_provider.get_provider(name)
        monkeypatch.setitem(provider, "available_fn", lambda: False)

    response = client.post(
        "/delegate",
        json={"description": "Design an application architecture"},
        headers=auth_headers(),
    )

    assert response.status_code == 502


def test_providers_endpoint_lists_registered_providers():
    response = client.get("/providers")

    assert response.status_code == 200
    body = response.json()
    assert "claude" in body
    assert "local" in body
    assert "run_coding_task" not in body["claude"]


def test_templates_endpoint_lists_available_templates():
    response = client.get("/templates")

    assert response.status_code == 200
    body = response.json()
    assert "fastapi" in body
    assert body["fastapi"]["label"]


def test_create_build_endpoint_accepts_a_template():
    response = client.post(
        "/builds",
        json={"name": "api", "description": "desc", "project_path": "/tmp/proj", "template": "fastapi"},
        headers=auth_headers(),
    )

    assert response.status_code == 200
    assert response.json()["template"] == "fastapi"


def test_create_build_endpoint_returns_400_for_unknown_template():
    response = client.post(
        "/builds",
        json={"name": "api", "description": "desc", "project_path": "/tmp/proj", "template": "cobol-mainframe"},
        headers=auth_headers(),
    )

    assert response.status_code == 400


def test_create_build_endpoint_requires_auth():
    response = client.post("/builds", json={"name": "a", "description": "b", "project_path": "/tmp/p"})

    assert response.status_code == 401


def test_create_build_endpoint_creates_a_build():
    response = client.post(
        "/builds",
        json={"name": "todo-app", "description": "A todo app", "project_path": "/tmp/proj"},
        headers=auth_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "REQUESTED"
    assert body["name"] == "todo-app"


def test_builds_endpoint_lists_created_builds():
    client.post(
        "/builds",
        json={"name": "todo-app", "description": "desc", "project_path": "/tmp/proj"},
        headers=auth_headers(),
    )

    response = client.get("/builds")

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_build_endpoint_returns_404_for_unknown_build():
    response = client.get("/builds/does-not-exist")

    assert response.status_code == 404


def test_build_endpoint_returns_created_build():
    created = client.post(
        "/builds",
        json={"name": "todo-app", "description": "desc", "project_path": "/tmp/proj"},
        headers=auth_headers(),
    ).json()

    response = client.get(f"/builds/{created['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


def test_answer_build_endpoint_returns_409_from_wrong_state():
    created = client.post(
        "/builds",
        json={"name": "todo-app", "description": "desc", "project_path": "/tmp/proj"},
        headers=auth_headers(),
    ).json()

    response = client.post(
        f"/builds/{created['id']}/answer",
        json={"answer": "use Postgres"},
        headers=auth_headers(),
    )

    assert response.status_code == 409


def test_approve_architecture_endpoint_returns_404_for_unknown_build():
    response = client.post(
        "/builds/does-not-exist/approve-architecture",
        headers=auth_headers(),
    )

    assert response.status_code == 404


def test_generate_build_endpoint_returns_409_from_wrong_state():
    created = client.post(
        "/builds",
        json={"name": "todo-app", "description": "desc", "project_path": "/tmp/proj"},
        headers=auth_headers(),
    ).json()

    response = client.post(f"/builds/{created['id']}/generate", headers=auth_headers())

    assert response.status_code == 409


def test_approve_deploy_endpoint_returns_409_from_wrong_state():
    created = client.post(
        "/builds",
        json={"name": "todo-app", "description": "desc", "project_path": "/tmp/proj"},
        headers=auth_headers(),
    ).json()

    response = client.post(f"/builds/{created['id']}/approve-deploy", headers=auth_headers())

    assert response.status_code == 409


def test_rollback_endpoint_returns_400_when_no_deployment_exists():
    created = client.post(
        "/builds",
        json={"name": "todo-app", "description": "desc", "project_path": "/tmp/proj"},
        headers=auth_headers(),
    ).json()

    response = client.post(f"/builds/{created['id']}/rollback", headers=auth_headers())

    assert response.status_code == 400


def test_rollback_endpoint_returns_404_for_unknown_build():
    response = client.post("/builds/does-not-exist/rollback", headers=auth_headers())

    assert response.status_code == 404


def test_approve_deploy_and_rollback_endpoints_require_auth():
    created = client.post(
        "/builds",
        json={"name": "todo-app", "description": "desc", "project_path": "/tmp/proj"},
        headers=auth_headers(),
    ).json()

    assert client.post(f"/builds/{created['id']}/approve-deploy").status_code == 401
    assert client.post(f"/builds/{created['id']}/rollback").status_code == 401
