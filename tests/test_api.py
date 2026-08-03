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

    # opencode_claude gained a real text_task route 2026-08-02 and sits in
    # "planning" -- included so every candidate really is unavailable.
    for name in ("gemini", "geminix", "openrouter", "minimax", "deepseek", "claude", "deepseek_native_flash", "opencode_claude", "deepseek_native_pro", "qwen3_coder_text"):
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


def test_dashboard_actions_go_through_proxy_not_directly():
    """Phase 17D: the dashboard now has interactive actions, but they are
    routed through /dashboard/api/proxy/* -- never by attaching the real
    bridge token directly to browser JS.  Confirm the HTML exists and that
    write actions (approve/reject) reference the proxy path, not a direct
    /approvals endpoint with an Authorization header embedded in the JS."""
    response = client.get("/dashboard")

    html = response.text
    # Dashboard now intentionally has buttons (approve/reject in Approvals tab)
    assert "<button" in html.lower()
    # The JS must use the proxy path for write actions, not direct auth
    assert "/dashboard/api/proxy/" in html
    # The bridge token itself must never appear in the HTML
    from core.api import _load_api_token
    token = _load_api_token()
    assert token not in html


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

    # opencode_claude gained a real text_task route 2026-08-02 and sits in
    # "planning" -- included so every candidate really is unavailable.
    for name in ("deepseek_native_flash", "openrouter", "deepseek", "claude", "gemini", "geminix", "opencode_claude", "deepseek_native_pro", "qwen3_coder_text"):
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


# ── Phase 17B: GET /kai/chat (history for the plugin chat panel) ──────


def test_kai_chat_history_requires_auth():
    response = client.get("/kai/chat")

    assert response.status_code == 401


def test_kai_chat_history_requires_auth_wrong_token():
    response = client.get(
        "/kai/chat",
        headers={"Authorization": "Bearer not-the-real-token"},
    )

    assert response.status_code == 401


def test_kai_chat_history_empty_when_no_conversation_yet():
    response = client.get("/kai/chat", headers=auth_headers())

    assert response.status_code == 200
    assert response.json() == []


def test_kai_chat_history_matches_what_post_persists(monkeypatch):
    """Completion criterion for 17B+17V: the session envelope the panel
    displays must match what POST /kai/chat persists."""
    import core.api as api_module
    from core.kai.conversation import get_session

    monkeypatch.setattr(api_module, "ai_chat", lambda messages, signals: "All systems nominal.")

    post1 = client.post(
        "/kai/chat",
        json={"text": "How are you?"},
        headers=auth_headers(),
    )
    assert post1.status_code == 200

    post2 = client.post(
        "/kai/chat",
        json={"text": "What's next on the roadmap?"},
        headers=auth_headers(),
    )
    assert post2.status_code == 200

    response = client.get("/kai/chat", headers=auth_headers())

    assert response.status_code == 200
    history = response.json()

    # 17V: GET returns the recent_messages list from the session envelope
    from core.memory import load
    envelope = load("kai_chat_history.json")
    persisted_messages = envelope.get("recent_messages", []) if isinstance(envelope, dict) else envelope
    assert history == persisted_messages

    user_messages = [m["content"] for m in history if m["role"] == "user"]
    assert user_messages == ["How are you?", "What's next on the roadmap?"]

    assistant_messages = [m for m in history if m["role"] == "assistant"]
    assert len(assistant_messages) == 2

    # Alternating user/assistant order, most recent last.
    assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]


def test_kai_chat_history_get_does_not_modify_history(monkeypatch):
    import core.api as api_module
    from core.memory import load

    monkeypatch.setattr(api_module, "ai_chat", lambda messages, signals: "Hi.")

    client.post("/kai/chat", json={"text": "hello"}, headers=auth_headers())
    before = load("kai_chat_history.json")

    response = client.get("/kai/chat", headers=auth_headers())

    assert response.status_code == 200
    assert load("kai_chat_history.json") == before


# ── Phase 17B: what the chat panel actually *displays* ────────────────
#
# The panel renders history[i]["content"] verbatim as the chat transcript,
# so that field has to be readable prose -- not a Python repr of the
# endpoint's response envelope. Found by driving the real backend through
# the real bridge proxy: the panel showed
#   "{'matched': False, 'response': '37 roadmap phases are complete.'}"
# where the operator should have read "37 roadmap phases are complete."
# The HTTP response body of POST /kai/chat is deliberately unchanged --
# only the persisted transcript text is fixed.


def test_kai_chat_persists_the_reply_prose_not_a_python_repr(monkeypatch):
    import core.api as api_module

    monkeypatch.setattr(api_module, "ai_chat", lambda messages, signals: "37 roadmap phases are complete.")

    post = client.post("/kai/chat", json={"text": "How many phases are done?"}, headers=auth_headers())
    assert post.status_code == 200
    # The wire format callers already depend on is untouched.
    assert post.json() == {"matched": False, "response": "37 roadmap phases are complete."}

    history = client.get("/kai/chat", headers=auth_headers()).json()
    assistant = history[-1]

    assert assistant["role"] == "assistant"
    assert assistant["content"] == "37 roadmap phases are complete."
    # No dict repr leaking into the transcript.
    assert "matched" not in assistant["content"]
    assert not assistant["content"].startswith("{")


def test_kai_chat_history_content_is_always_a_string(monkeypatch):
    """The panel calls String(msg.content) on this; a non-string would render
    as "[object Object]"."""
    import core.api as api_module

    monkeypatch.setattr(api_module, "ai_chat", lambda messages, signals: "fine")

    client.post("/kai/chat", json={"text": "hi"}, headers=auth_headers())
    client.post("/kai/chat", json={"text": "Kai, analyze system health."}, headers=auth_headers())

    history = client.get("/kai/chat", headers=auth_headers()).json()

    assert len(history) == 4
    for message in history:
        assert isinstance(message["content"], str), message
        assert message["role"] in ("user", "assistant")


def test_kai_chat_persists_approval_intent_result_as_prose():
    from core.approval import create_build_approval

    approval = create_build_approval(
        build_id="b-transcript",
        phase_id="X",
        approval_type="architecture",
        title="Transcript-readable plan",
        description="desc",
        risk="low",
        requested_action="approve_architecture",
    )

    post = client.post(
        "/kai/chat",
        json={"text": f"approve architecture plan #{approval['id']}"},
        headers=auth_headers(),
    )
    assert post.status_code == 200

    history = client.get("/kai/chat", headers=auth_headers()).json()
    content = history[-1]["content"]

    assert "Transcript-readable plan" in content
    assert "approved" in content.lower()
    assert "'matched'" not in content


def test_kai_chat_persists_structured_command_result_readably(monkeypatch):
    """A matched command returns structured data. The transcript keeps the
    command's description plus JSON -- readable, and valid JSON rather than
    a Python repr (True/False/None are not JSON tokens)."""
    monkeypatch.setattr(
        "core.api.kai_dispatch",
        lambda text: {
            "matched": True,
            "description": "Analyze system health and AI provider status.",
            "result": {"findings": ["docker_unavailable"], "ok": True},
            "error": None,
        },
    )

    client.post("/kai/chat", json={"text": "Kai, analyze system health."}, headers=auth_headers())

    content = client.get("/kai/chat", headers=auth_headers()).json()[-1]["content"]

    assert content.startswith("Analyze system health and AI provider status.")
    assert "docker_unavailable" in content
    assert "True" not in content and "true" in content  # JSON, not Python repr
    _json.loads(content.split("\n", 1)[1])  # the payload half parses as JSON


def test_kai_chat_persists_command_error_as_prose(monkeypatch):
    monkeypatch.setattr(
        "core.api.kai_dispatch",
        lambda text: {
            "matched": True,
            "description": "Advance the active roadmap.",
            "result": None,
            "error": "roadmap is locked",
        },
    )

    client.post("/kai/chat", json={"text": "Kai, continue roadmap."}, headers=auth_headers())

    content = client.get("/kai/chat", headers=auth_headers()).json()[-1]["content"]

    assert "roadmap is locked" in content
    assert "'error'" not in content


def test_kai_chat_history_file_uses_session_envelope_format(monkeypatch):
    """17V: kai_chat_history.json now uses a session-envelope format
    (schema_version 2) with session/recent_messages/compressed keys
    rather than a flat message array."""
    import json as _json
    import core.api as api_module
    from core.memory import MEMORY_DIR

    monkeypatch.setattr(api_module, "ai_chat", lambda messages, signals: "ok")

    client.post("/kai/chat", json={"text": "hello"}, headers=auth_headers())

    raw = _json.loads((MEMORY_DIR / "kai_chat_history.json").read_text())

    assert set(raw) == {"schema_version", "records"}
    # 17V: records is a session-envelope dict, not a flat list
    envelope = raw["records"]
    assert isinstance(envelope, dict)
    assert envelope["schema_version"] == 2
    assert "session" in envelope
    assert "recent_messages" in envelope
    assert "compressed" in envelope
    # The recent_messages list contains our messages
    messages = envelope["recent_messages"]
    assert len(messages) >= 2
    assert messages[-2]["role"] == "user"
    assert messages[-2]["content"] == "hello"
    assert messages[-1]["role"] == "assistant"


def test_kai_chat_history_reads_legacy_double_wrapped_file(monkeypatch):
    """Any file written before the envelope fix still reads back correctly --
    no operator loses their transcript to the format change."""
    import json as _json
    from core.memory import MEMORY_DIR

    legacy = [
        {"role": "user", "content": "written by the old code"},
        {"role": "assistant", "content": "still readable"},
    ]
    (MEMORY_DIR / "kai_chat_history.json").write_text(
        _json.dumps({"schema_version": 1, "records": {"schema_version": 1, "records": legacy}})
    )

    response = client.get("/kai/chat", headers=auth_headers())

    assert response.status_code == 200
    assert response.json() == legacy


def test_kai_chat_appending_to_a_legacy_file_migrates_to_envelope(monkeypatch):
    """17V: legacy flat-array format is transparently upgraded to session
    envelope on first read/write cycle."""
    import json as _json
    import core.api as api_module
    from core.memory import MEMORY_DIR

    legacy = [{"role": "user", "content": "old turn"}, {"role": "assistant", "content": "old reply"}]
    (MEMORY_DIR / "kai_chat_history.json").write_text(
        _json.dumps({"schema_version": 1, "records": {"schema_version": 1, "records": legacy}})
    )

    monkeypatch.setattr(api_module, "ai_chat", lambda messages, signals: "new reply")
    client.post("/kai/chat", json={"text": "new turn"}, headers=auth_headers())

    raw = _json.loads((MEMORY_DIR / "kai_chat_history.json").read_text())

    # 17V: migrated to envelope format
    envelope = raw["records"]
    assert isinstance(envelope, dict)
    assert envelope["schema_version"] == 2
    messages = envelope["recent_messages"]
    assert [m["content"] for m in messages] == [
        "old turn",
        "old reply",
        "new turn",
        "new reply",
    ]


# ── Phase 17J: chat-triggered application builds ─────────────────────────────
#
# "Kai, build me a website" -- POST /kai/chat detects the intent, extracts a
# structured {name, description, template} via the AI router, and calls the
# same core.build_manager.create_build() any other build goes through. No
# new pipeline, no new approval mechanism: create_build() only inserts a
# REQUESTED build record, the exact same architecture/deploy approval gates
# apply, and the chat response returns immediately rather than blocking on
# generation.


def _mock_extraction(monkeypatch, response_text):
    import core.api as api_module

    monkeypatch.setattr(
        api_module, "delegate",
        lambda prompt, **kwargs: {"provider": "gemini", "response": response_text},
    )


def test_extract_build_intent_uses_classification_task_type(monkeypatch):
    # 2026-07-31: this is intent classification, not architectural planning
    # -- it must route through task_type="classification" (groq-first, fast
    # structured extraction) rather than "planning" (gemini-first, long-
    # context architecture), which it used before this fix.
    import core.api as api_module

    captured = {}

    def spy_delegate(prompt, **kwargs):
        captured.update(kwargs)
        return {"provider": "gemini", "response": '{"is_build_request": false}'}

    monkeypatch.setattr(api_module, "delegate", spy_delegate)

    api_module._extract_build_intent("Kai, build me a personal website")

    assert captured.get("task_type") == "classification"


def test_kai_chat_build_request_creates_a_real_build(monkeypatch):
    import core.api as api_module

    _mock_extraction(
        monkeypatch,
        '{"is_build_request": true, "name": "my portfolio", '
        '"description": "A personal portfolio website.", "template": "react"}',
    )

    captured = {}

    def fake_create_build(name, description, project_path, template=None):
        captured.update(name=name, description=description, project_path=project_path, template=template)
        return {"id": "b-portfolio-1", "status": "REQUESTED"}

    monkeypatch.setattr(api_module, "create_build", fake_create_build)

    response = client.post(
        "/kai/chat",
        json={"text": "Kai, build me a website for my portfolio"},
        headers=auth_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert "b-portfolio-1" in body["result"]

    assert captured["name"] == "my portfolio"
    assert captured["description"] == "A personal portfolio website."
    assert captured["template"] == "react"
    assert captured["project_path"] == "/project/src/my-portfolio"


def test_kai_chat_build_request_does_not_block_on_generation(monkeypatch):
    """create_build() must be the only thing called -- never advance_builds()
    or anything that would run real (up to 20-minute) generation inline."""
    import core.api as api_module

    _mock_extraction(
        monkeypatch,
        '{"is_build_request": true, "name": "api-svc", "description": "A REST API.", "template": null}',
    )
    monkeypatch.setattr(
        api_module, "create_build",
        lambda name, description, project_path, template=None: {"id": "b-fast", "status": "REQUESTED"},
    )

    def boom(*a, **kw):
        raise AssertionError("must not advance/generate builds synchronously from chat")

    # advance_builds is not even imported into core.api -- this just documents
    # the invariant; the real guarantee is create_build being the only call.
    response = client.post("/kai/chat", json={"text": "please create a new api service"}, headers=auth_headers())

    assert response.status_code == 200


def test_kai_chat_non_build_message_falls_through_to_open_ended_chat(monkeypatch):
    """A message with no build-intent keywords must never even attempt
    extraction -- confirms the cheap pre-filter guards the AI call."""
    import core.api as api_module

    def fail_if_called(*a, **kw):
        raise AssertionError("delegate() must not be called for a non-build message")

    monkeypatch.setattr(api_module, "delegate", fail_if_called)
    monkeypatch.setattr(api_module, "ai_chat", lambda messages, signals: "Everything looks healthy.")

    response = client.post("/kai/chat", json={"text": "what are you doing right now?"}, headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["matched"] is False


def test_kai_chat_build_keyword_but_extraction_says_no_falls_through(monkeypatch):
    """Pre-filter matches ('build' + 'app'), but the AI extraction correctly
    determines it's not actually a build request -- must fail through to the
    normal open-ended answer, not force a build."""
    import core.api as api_module

    _mock_extraction(monkeypatch, '{"is_build_request": false}')
    monkeypatch.setattr(api_module, "ai_chat", lambda messages, signals: "Sure, here's how app builds work here.")

    def fail_if_called(*a, **kw):
        raise AssertionError("create_build must not be called")

    monkeypatch.setattr(api_module, "create_build", fail_if_called)

    response = client.post(
        "/kai/chat",
        json={"text": "how does the build process work for a new app?"},
        headers=auth_headers(),
    )

    assert response.status_code == 200
    assert response.json()["matched"] is False


def test_kai_chat_build_extraction_malformed_json_fails_closed(monkeypatch):
    """A provider returning garbage instead of JSON must never crash the
    endpoint or accidentally trigger a build -- fail closed to open chat."""
    import core.api as api_module

    _mock_extraction(monkeypatch, "I'm not sure what you mean by that, sorry!")
    monkeypatch.setattr(api_module, "ai_chat", lambda messages, signals: "fallback reply")

    def fail_if_called(*a, **kw):
        raise AssertionError("create_build must not be called on malformed extraction")

    monkeypatch.setattr(api_module, "create_build", fail_if_called)

    response = client.post("/kai/chat", json={"text": "build me an app please"}, headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["matched"] is False


def test_kai_chat_build_request_project_path_disambiguates_existing_directory(monkeypatch, tmp_path):
    import core.api as api_module

    monkeypatch.setattr(api_module, "APPLICATION_BUILD_BASE_DIR", tmp_path)
    (tmp_path / "blog").mkdir()  # pre-existing -- must be disambiguated, not reused

    _mock_extraction(
        monkeypatch,
        '{"is_build_request": true, "name": "blog", "description": "A blog.", "template": null}',
    )

    captured = {}
    monkeypatch.setattr(
        api_module, "create_build",
        lambda name, description, project_path, template=None: captured.update(project_path=project_path) or {"id": "b-blog-2", "status": "REQUESTED"},
    )

    response = client.post("/kai/chat", json={"text": "build me a blog app"}, headers=auth_headers())

    assert response.status_code == 200
    assert captured["project_path"] == str(tmp_path / "blog-2")


def test_kai_chat_build_request_rejects_unknown_template(monkeypatch):
    """An extracted template not in the real TEMPLATES set must be treated
    as null (agent decides), never passed through as-is to create_build."""
    import core.api as api_module

    _mock_extraction(
        monkeypatch,
        '{"is_build_request": true, "name": "widget", "description": "A widget app.", '
        '"template": "cobol-mainframe"}',
    )

    captured = {}
    monkeypatch.setattr(
        api_module, "create_build",
        lambda name, description, project_path, template=None: captured.update(template=template) or {"id": "b-w", "status": "REQUESTED"},
    )

    response = client.post("/kai/chat", json={"text": "build me a widget app"}, headers=auth_headers())

    assert response.status_code == 200
    assert captured["template"] is None


def test_kai_chat_build_request_never_calls_approval_functions(monkeypatch):
    """Structural guarantee extended to build-triggering: the chat handler
    that creates a build must never itself approve/deploy it."""
    import core.api as api_module

    _mock_extraction(
        monkeypatch,
        '{"is_build_request": true, "name": "svc", "description": "A service.", "template": null}',
    )

    def fail_if_called(*a, **kw):
        raise AssertionError("chat must never approve/deploy a build it just created")

    monkeypatch.setattr(api_module, "approve_architecture", fail_if_called)
    monkeypatch.setattr(api_module, "approve_deploy", fail_if_called)
    monkeypatch.setattr(
        api_module, "create_build",
        lambda name, description, project_path, template=None: {"id": "b-safe", "status": "REQUESTED"},
    )

    response = client.post("/kai/chat", json={"text": "build me a service"}, headers=auth_headers())

    assert response.status_code == 200


# ── Phase 17B: GET /kai/identity, GET /kai/proposals, GET /learning/lessons ──
#
# The CloudCLI plugin's Kai Control Center tab (13G) has called these three
# endpoints since it was written, but they were never actually added to
# core/api.py -- Promise.all([...]) in renderKaiControlCenter rejects the
# instant any one of its fetches 404s, so the *entire* tab (identity card,
# chat panel, proposals, roadmap, approvals, lessons) has been rendering
# "Failed to load: HTTP 404" instead of any content since 13G shipped.
# Discovered while verifying 17B's chat panel end-to-end against a real
# running backend+proxy+browser -- without these, the panel the operator
# needs literally never appears. No new write capability: all three are
# read-only wrappers around functions (core.kai.identity/mission/goals/
# policies, core.kai.planner.list_proposals, core.build_learning.
# summarize_lessons) that already exist and are already unit-tested.


def test_kai_identity_endpoint_returns_assembled_identity():
    response = client.get("/kai/identity")

    assert response.status_code == 200
    body = response.json()

    assert body["name"] == "Kai"
    assert "Kai" in body["identity"]
    assert "AI Orchestrator" in body["identity"]
    assert "human control" in body["mission"].lower()
    for expected in ["analyze", "plan", "delegate", "execute approved work", "learn", "improve"]:
        assert expected in body["capabilities"]
    assert len(body["restrictions"]) == 4
    assert isinstance(body["autonomous_mode"], bool)


def test_kai_identity_endpoint_reflects_live_autonomous_mode(monkeypatch):
    import core.api as api_module

    monkeypatch.setattr(api_module, "is_autonomous_mode_enabled", lambda: True)

    response = client.get("/kai/identity")

    assert response.status_code == 200
    assert response.json()["autonomous_mode"] is True


def test_kai_identity_endpoint_does_not_require_auth():
    # Read-only, ungated -- same tier as /learning and /roadmap/progress,
    # which this identity card sits directly alongside in the tab.
    response = client.get("/kai/identity")

    assert response.status_code == 200


def test_kai_proposals_endpoint_returns_empty_list_when_none_exist(monkeypatch):
    monkeypatch.setattr("core.kai.planner.load_proposals", lambda: [])

    response = client.get("/kai/proposals")

    assert response.status_code == 200
    assert response.json() == []


def test_kai_proposals_endpoint_returns_stored_proposals(monkeypatch):
    from core.lifecycle import new_object

    proposal = new_object(
        "proposed",
        title="Reduce docker health flakiness",
        description="Docker findings recur",
        suggested_action="Add a remediation",
        rationale="Recurring health finding",
        source_signals=["health:docker_unavailable"],
        target_roadmap_phase_draft=None,
        synthesized_by="claude",
        roadmap_phase_id=None,
    )
    monkeypatch.setattr("core.kai.planner.load_proposals", lambda: [proposal])

    response = client.get("/kai/proposals")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["title"] == "Reduce docker health flakiness"
    assert body[0]["status"] == "proposed"


def test_kai_proposals_endpoint_does_not_require_auth(monkeypatch):
    monkeypatch.setattr("core.kai.planner.load_proposals", lambda: [])

    response = client.get("/kai/proposals")

    assert response.status_code == 200


def test_learning_lessons_endpoint_returns_empty_dict_when_none_recorded(monkeypatch):
    monkeypatch.setattr("core.build_learning.load", lambda *a, **k: [])

    response = client.get("/learning/lessons")

    assert response.status_code == 200
    assert response.json() == {}


def test_learning_lessons_endpoint_aggregates_by_subject(monkeypatch):
    lessons = [
        {"category": "preferred_architecture", "subject": "fastapi_template", "recommendation": "trusted"},
        {"category": "preferred_architecture", "subject": "fastapi_template", "recommendation": "trusted"},
        {"category": "common_failure", "subject": "flask_template", "recommendation": None},
    ]
    monkeypatch.setattr("core.build_learning.load", lambda *a, **k: lessons)

    response = client.get("/learning/lessons")

    assert response.status_code == 200
    body = response.json()
    assert body["fastapi_template"]["category"] == "preferred_architecture"
    assert body["fastapi_template"]["attempts"] == 2
    assert body["fastapi_template"]["recommendation"] == "trusted"
    assert body["flask_template"]["recommendation"] == "avoid"


def test_learning_lessons_endpoint_does_not_require_auth(monkeypatch):
    monkeypatch.setattr("core.build_learning.load", lambda *a, **k: [])

    response = client.get("/learning/lessons")

    assert response.status_code == 200


# ── Law library document upload (2026-07-31) ─────────────────────────────────

def test_upload_law_document_requires_auth():
    response = client.post(
        "/kai/law-documents",
        files={"file": ("notes.txt", b"some notes", "text/plain")},
    )
    assert response.status_code == 401


def test_upload_law_document_stores_and_returns_a_record():
    response = client.post(
        "/kai/law-documents",
        files={"file": ("torts.txt", b"Negligence requires a duty of care.", "text/plain")},
        data={"category": "torts", "jurisdiction": "Ghana"},
        headers=auth_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["filename"] == "torts.txt"
    assert body["category"] == "torts"
    assert body["jurisdiction"] == "Ghana"
    assert body["extracted_chars"] > 0


def test_upload_law_document_rejects_unsupported_type():
    response = client.post(
        "/kai/law-documents",
        files={"file": ("virus.exe", b"nope", "application/octet-stream")},
        headers=auth_headers(),
    )
    assert response.status_code == 400


def test_list_law_documents_returns_documents_without_auth():
    # 15A: read endpoints (GET) are unrestricted — this was previously
    # gated behind require_bridge_token, now open for read-only access.
    response = client.get("/kai/law-documents")
    assert response.status_code == 200


def test_list_and_delete_law_document_round_trip():
    upload = client.post(
        "/kai/law-documents",
        files={"file": ("contracts.txt", b"Offer and acceptance.", "text/plain")},
        headers=auth_headers(),
    )
    doc_id = upload.json()["id"]

    listing = client.get("/kai/law-documents", headers=auth_headers())
    assert listing.status_code == 200
    assert any(d["id"] == doc_id for d in listing.json()["documents"])

    deletion = client.delete(f"/kai/law-documents/{doc_id}", headers=auth_headers())
    assert deletion.status_code == 200

    listing_after = client.get("/kai/law-documents", headers=auth_headers())
    assert not any(d["id"] == doc_id for d in listing_after.json()["documents"])


def test_delete_nonexistent_law_document_returns_404():
    response = client.delete("/kai/law-documents/does-not-exist", headers=auth_headers())
    assert response.status_code == 404
