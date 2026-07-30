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


def test_roadmap_autonomous_status_defaults_to_disabled():
    response = client.get("/roadmap/autonomous/status")

    assert response.status_code == 200
    assert response.json()["enabled"] is False


def test_roadmap_autonomous_enable_requires_auth():
    response = client.post("/roadmap/autonomous/enable")

    assert response.status_code == 401


def test_roadmap_autonomous_enable_and_disable_roundtrip():
    enable_response = client.post("/roadmap/autonomous/enable", headers=auth_headers())
    assert enable_response.status_code == 200
    assert enable_response.json()["enabled"] is True
    assert client.get("/roadmap/autonomous/status").json()["enabled"] is True

    disable_response = client.post("/roadmap/autonomous/disable", headers=auth_headers())
    assert disable_response.status_code == 200
    assert disable_response.json()["enabled"] is False
    assert client.get("/roadmap/autonomous/status").json()["enabled"] is False


def test_add_roadmap_phase_endpoint_requires_auth(isolated_roadmap):
    response = client.post("/roadmap/phases", json={"id": "C", "name": "n", "description": "d", "priority": 3})

    assert response.status_code == 401


def test_add_roadmap_phase_endpoint_creates_phase_defaulting_to_proposed(isolated_roadmap):
    response = client.post(
        "/roadmap/phases",
        json={"id": "C", "name": "n", "description": "d", "dependencies": ["A"], "priority": 3},
        headers=auth_headers(),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "proposed"


def test_add_roadmap_phase_endpoint_returns_400_for_bad_dependency(isolated_roadmap):
    response = client.post(
        "/roadmap/phases",
        json={"id": "C", "name": "n", "description": "d", "dependencies": ["nope"], "priority": 3},
        headers=auth_headers(),
    )

    assert response.status_code == 400


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

    for name in ("gemini", "openrouter", "minimax", "deepseek", "claude"):
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


def test_kai_command_endpoint_requires_auth():
    response = client.post("/kai/command", json={"text": "Kai, analyze system health"})

    assert response.status_code == 401


def test_kai_command_endpoint_dispatches_to_kai_commands(monkeypatch):
    import core.kai.commands as commands

    monkeypatch.setattr(commands, "advance_roadmap", lambda: {"action": "nothing_to_do"})

    response = client.post(
        "/kai/command",
        json={"text": "Kai, continue roadmap"},
        headers=auth_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["matched"] is True
    assert body["result"] == {"action": "nothing_to_do"}


def test_kai_command_endpoint_returns_200_with_matched_false_for_unknown_phrase():
    response = client.post(
        "/kai/command",
        json={"text": "Kai, do a barrel roll"},
        headers=auth_headers(),
    )

    assert response.status_code == 200
    assert response.json()["matched"] is False


# ── Phase 16A: Standalone Kai Dashboard ──────────────────────


def test_dashboard_endpoint_returns_valid_html():
    response = client.get("/dashboard")

    assert response.status_code == 200
    content_type = response.headers.get("content-type", "")
    assert "text/html" in content_type
    html = response.text
    assert "<html" in html or "<!DOCTYPE html>" in html
    assert "<title>" in html
    assert "Kai Dashboard" in html


def test_dashboard_endpoint_html_contains_expected_ui_elements():
    response = client.get("/dashboard")

    html = response.text
    assert "Kai Dashboard</title>" in html or "Kai Dashboard" in html
    assert "System Health" in html
    assert "Roadmap Progress" in html
    assert "Active Builds" in html
    assert "Pending Approvals" in html
    assert "Provider Health" in html
    assert "Learning Summary" in html


def test_root_endpoint_redirects_to_dashboard():
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/dashboard"


def test_root_endpoint_redirect_follow_lands_on_dashboard():
    response = client.get("/", follow_redirects=True)

    assert response.status_code == 200
    assert "Kai Dashboard" in response.text


def test_dashboard_does_not_contain_approve_reject_actions():
    """Phase 16A is read-only -- no approve/reject buttons or forms."""
    response = client.get("/dashboard")

    html = response.text.lower()
    assert "<button" not in html and "<form" not in html


def test_dashboard_html_does_not_contain_bridge_token():
    """The bridge token must never be embedded in browser-servable files."""
    from core.api import _load_api_token

    response = client.get("/dashboard")

    token = _load_api_token()
    assert token not in response.text


def test_dashboard_serving_does_not_import_cloudcli_bridge():
    """Smoke test: core.api must not import the CloudCLI plugin bridge.
    The dashboard is served directly from core.api, so any import of
    core.coding_bridge would couple it to CloudCLI's lifecycle."""
    from pathlib import Path

    api_source = (Path(__file__).resolve().parent.parent / "core" / "api.py").read_text()

    assert "from core.coding_bridge" not in api_source
    assert "import core.coding_bridge" not in api_source


# ── Phase 13X: POST /kai/chat ───────────────────────────────


def test_kai_chat_endpoint_requires_auth():
    response = client.post("/kai/chat", json={"text": "Hello, Kai!"})

    assert response.status_code == 401


def test_kai_chat_endpoint_requires_auth_wrong_token():
    response = client.post(
        "/kai/chat",
        json={"text": "Hello, Kai!"},
        headers={"Authorization": "Bearer not-the-real-token"},
    )

    assert response.status_code == 401


def test_kai_chat_endpoint_rejects_empty_text():
    response = client.post(
        "/kai/chat",
        json={"text": ""},
        headers=auth_headers(),
    )

    assert response.status_code == 400


def test_kai_chat_endpoint_dispatches_command_patterns_first(monkeypatch):
    import core.kai.commands as commands

    monkeypatch.setattr(commands, "advance_roadmap", lambda: {"action": "nothing_to_do"})

    response = client.post(
        "/kai/chat",
        json={"text": "Kai, continue roadmap"},
        headers=auth_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["matched"] is True
    assert body["result"] == {"action": "nothing_to_do"}


def test_kai_chat_endpoint_approve_architecture_resolves_through_existing_system():
    from core.approval import create_build_approval

    approval = create_build_approval(
        build_id="b-arch",
        phase_id="X",
        approval_type="architecture",
        title="Architecture plan for new feature",
        description="desc",
        risk="low",
        requested_action="approve_architecture",
    )

    response = client.post(
        "/kai/chat",
        json={"text": f"approve architecture plan #{approval['id']}"},
        headers=auth_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["matched"] is True
    assert "approved" in str(body["result"]).lower()
    assert "cloudcli-plugin" in str(body["result"])

    from core.approval import load_requests
    updated = next(r for r in load_requests() if r["id"] == approval["id"])
    assert updated["status"] == "approved"
    assert updated["approved_by"] == "cloudcli-plugin"


def test_kai_chat_endpoint_reject_deploy_resolves_through_existing_system():
    from core.approval import create_build_approval

    approval = create_build_approval(
        build_id="b-deploy",
        phase_id="X",
        approval_type="deploy",
        title="Deploy plan for staging",
        description="desc",
        risk="low",
        requested_action="approve_deploy",
    )

    response = client.post(
        "/kai/chat",
        json={"text": f"reject deploy #{approval['id']}"},
        headers=auth_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["matched"] is True
    assert "rejected" in str(body["result"]).lower()
    assert "cloudcli-plugin" in str(body["result"])

    from core.approval import load_requests
    updated = next(r for r in load_requests() if r["id"] == approval["id"])
    assert updated["status"] == "rejected"
    assert updated["rejected_by"] == "cloudcli-plugin"


def test_kai_chat_endpoint_approval_auto_resolves_single_pending_plan():
    from core.approval import create_build_approval

    approval = create_build_approval(
        build_id="b-solo",
        phase_id="X",
        approval_type="architecture",
        title="Solo architecture plan",
        description="desc",
        risk="low",
        requested_action="approve_architecture",
    )

    response = client.post(
        "/kai/chat",
        json={"text": "approve architecture plan"},
        headers=auth_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["matched"] is True
    assert "approved" in str(body["result"]).lower()

    from core.approval import load_requests
    updated = next(r for r in load_requests() if r["id"] == approval["id"])
    assert updated["status"] == "approved"


def test_kai_chat_endpoint_approval_multiple_pending_lists_options():
    from core.approval import create_build_approval

    create_build_approval(
        build_id="b-a",
        phase_id="X",
        approval_type="architecture",
        title="Plan alpha",
        description="desc",
        risk="low",
        requested_action="approve_architecture",
    )
    create_build_approval(
        build_id="b-b",
        phase_id="X",
        approval_type="architecture",
        title="Plan beta",
        description="desc",
        risk="low",
        requested_action="approve_architecture",
    )

    response = client.post(
        "/kai/chat",
        json={"text": "approve architecture plan"},
        headers=auth_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["matched"] is True
    result_text = str(body["result"])
    assert "multiple" in result_text.lower() or "plan alpha" in result_text.lower()


def test_kai_chat_endpoint_approval_nonexistent_matching_returns_info():
    response = client.post(
        "/kai/chat",
        json={"text": "approve architecture plan"},
        headers=auth_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["matched"] is True
    assert "no pending" in str(body["result"]).lower()


def test_kai_chat_endpoint_client_supplied_operator_is_ignored():
    from core.approval import create_build_approval

    approval = create_build_approval(
        build_id="b-forge",
        phase_id="X",
        approval_type="architecture",
        title="Verify forge-safe plan",
        description="desc",
        risk="low",
        requested_action="approve_architecture",
    )

    response = client.post(
        "/kai/chat",
        json={"text": f"approve architecture plan #{approval['id']}", "operator": "alice"},
        headers=auth_headers(),
    )

    assert response.status_code == 200
    from core.approval import load_requests
    updated = next(r for r in load_requests() if r["id"] == approval["id"])
    assert updated["approved_by"] == "cloudcli-plugin"


def test_kai_chat_endpoint_fallback_to_ai_chat_for_unknown_text(monkeypatch):
    import core.api as api_module

    monkeypatch.setattr(api_module, "ai_chat", lambda messages, signals: "Hello, operator! The roadmap is on track.")

    response = client.post(
        "/kai/chat",
        json={"text": "What are you doing right now?"},
        headers=auth_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["matched"] is False
    assert body["response"] == "Hello, operator! The roadmap is on track."


def test_kai_chat_endpoint_conversation_history_persists(monkeypatch):
    import core.api as api_module

    call_signals = []

    def fake_chat(messages, signals):
        call_signals.append((list(messages), dict(signals)))
        return "Hello, operator!"

    monkeypatch.setattr(api_module, "ai_chat", fake_chat)

    response1 = client.post(
        "/kai/chat",
        json={"text": "How are you?"},
        headers=auth_headers(),
    )
    assert response1.status_code == 200

    response2 = client.post(
        "/kai/chat",
        json={"text": "What's the roadmap status?"},
        headers=auth_headers(),
    )
    assert response2.status_code == 200

    assert len(call_signals) == 2
    messages_for_call2 = call_signals[1][0]
    user_messages = [m["content"] for m in messages_for_call2 if m["role"] == "user"]
    assert "How are you?" in user_messages
    assert "What's the roadmap status?" in user_messages

    assistant_messages = [m["content"] for m in messages_for_call2 if m["role"] == "assistant"]
    assert len(assistant_messages) >= 1


def test_kai_chat_endpoint_approval_returns_no_pending_after_request_handled():
    from core.approval import create_build_approval

    approval = create_build_approval(
        build_id="b-double",
        phase_id="X",
        approval_type="architecture",
        title="Already-approved plan",
        description="desc",
        risk="low",
        requested_action="approve_architecture",
    )

    client.post(
        "/kai/chat",
        json={"text": f"approve architecture plan #{approval['id']}"},
        headers=auth_headers(),
    )

    response = client.post(
        "/kai/chat",
        json={"text": f"approve architecture plan #{approval['id']}"},
        headers=auth_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["matched"] is True
    assert "no pending" in str(body["result"]).lower()


def test_kai_chat_endpoint_returns_502_when_all_providers_fail(monkeypatch):
    import core.ai_provider as ai_provider

    for name in ("gemini", "openrouter", "deepseek", "claude"):
        provider = ai_provider.get_provider(name)
        monkeypatch.setitem(provider, "available_fn", lambda: False)

    response = client.post(
        "/kai/chat",
        json={"text": "What are you doing right now?"},
        headers=auth_headers(),
    )

    assert response.status_code == 502


def test_kai_chat_endpoint_approval_handles_partial_id_match():
    from core.approval import create_build_approval

    approval = create_build_approval(
        build_id="b-partial",
        phase_id="X",
        approval_type="deploy",
        title="Deploy plan for partial-match test",
        description="desc",
        risk="low",
        requested_action="approve_deploy",
    )

    partial_id = approval["id"][:8]

    response = client.post(
        "/kai/chat",
        json={"text": f"approve deploy {partial_id}"},
        headers=auth_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["matched"] is True
    assert "approved" in str(body["result"]).lower()

    from core.approval import load_requests
    updated = next(r for r in load_requests() if r["id"] == approval["id"])
    assert updated["status"] == "approved"
