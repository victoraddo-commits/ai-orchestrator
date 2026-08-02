from fastapi.testclient import TestClient

import core.api as api_module
import core.build_manager as build_manager
from core.approval import load_requests
from core.build_learning import get_build_history


client = TestClient(api_module.app)


def auth_headers():
    return {"Authorization": f"Bearer {api_module._load_api_token()}"}


def _force_status(build_id, status):
    # Same pattern as tests/test_build_manager.py's helper: jump a build
    # straight to a state without running the real AI-backed step that
    # would normally produce it.
    builds = build_manager.load_builds()
    for b in builds:
        if b["id"] == build_id:
            b["status"] = status
    build_manager.save_builds(builds)


def _disable_code_review(monkeypatch):
    # Same helper as tests/test_build_manager.py -- code review is a
    # fallback chain (build_manager.CODE_REVIEW_CANDIDATES: opencode_claude
    # then deepseek_native_pro), both real env-credential-available
    # providers on this host/process, so tests that don't care about the
    # review step's outcome must disable the whole chain, not just one name.
    import core.ai_provider as ai_provider
    for name in build_manager.CODE_REVIEW_CANDIDATES:
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)


def _create_build_via_api():
    response = client.post(
        "/builds",
        json={"name": "todo-app", "description": "Build a todo app", "project_path": "/tmp/proj"},
        headers=auth_headers(),
    )
    return response.json()["id"]


def _approval_for_build(build_id):
    matching = [a for a in client.get("/approvals").json() if a.get("build_id") == build_id]
    assert len(matching) == 1
    return matching[0]


# -- Planning splits user questions from formal approval requests -----------

def test_advance_builds_creates_an_architecture_approval_when_the_plan_has_no_open_question(monkeypatch):
    build = build_manager.create_build("todo-app", "Build a todo app", "/tmp/proj")

    monkeypatch.setattr(
        build_manager,
        "delegate",
        lambda description, **kwargs: {
            "provider": "gemini", "task_type": "planning",
            "response": "Plan: FastAPI + SQLite.", "duration_ms": 10,
        },
    )

    build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "WAITING_FOR_ARCHITECTURE_APPROVAL"

    matching = [r for r in load_requests() if r.get("build_id") == build["id"]]
    assert len(matching) == 1

    approval = matching[0]
    assert approval["approval_type"] == "architecture"
    assert approval["status"] == "pending"
    assert approval["requested_action"] == "approve_architecture"
    assert approval["title"]
    assert approval["description"]
    assert "risk" in approval


def test_advance_builds_routes_a_clarifying_question_to_user_input_not_an_approval(monkeypatch):
    build = build_manager.create_build("todo-app", "Build a todo app", "/tmp/proj")

    monkeypatch.setattr(
        build_manager,
        "delegate",
        lambda description, **kwargs: {
            "provider": "gemini", "task_type": "planning",
            "response": "Any preference on database?", "duration_ms": 10,
        },
    )

    build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "WAITING_FOR_USER_INPUT"
    assert [r for r in load_requests() if r.get("build_id") == build["id"]] == []


def test_advance_builds_creates_a_deploy_approval_after_security_review(monkeypatch):
    build = build_manager.create_build("todo-app", "Build a todo app", "/tmp/proj")
    _force_status(build["id"], "GENERATING")

    monkeypatch.setattr(
        build_manager,
        "delegate",
        lambda description, **kwargs: {
            "provider": "opencode_claude", "task_type": "coding", "duration_ms": 10,
            "response": {"success": True, "response_text": "Done.", "files_changed": ["app.py"], "commits": [], "tool_errors": []},
        },
    )
    monkeypatch.setattr(
        build_manager,
        "run_all_scans",
        lambda project_path: {"scanners": {}, "total_findings": 2, "highest_severity": "medium"},
    )
    _disable_code_review(monkeypatch)

    build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "WAITING_FOR_DEPLOY_APPROVAL"

    matching = [r for r in load_requests() if r.get("build_id") == build["id"]]
    assert len(matching) == 1

    approval = matching[0]
    assert approval["approval_type"] == "deploy"
    assert approval["status"] == "pending"
    assert approval["requested_action"] == "approve_deploy"
    assert approval["risk"] == "medium"


# -- Approval appears in /approvals ------------------------------------------

def test_architecture_approval_appears_in_approvals_endpoint(monkeypatch):
    build_id = _create_build_via_api()

    monkeypatch.setattr(
        build_manager,
        "delegate",
        lambda description, **kwargs: {
            "provider": "gemini", "task_type": "planning",
            "response": "Plan: FastAPI + SQLite.", "duration_ms": 10,
        },
    )
    build_manager.advance_builds()

    approval = _approval_for_build(build_id)
    assert approval["status"] == "pending"
    assert approval["approval_type"] == "architecture"
    assert approval["build_id"] == build_id


# -- Approving resumes the build automatically -------------------------------

def test_approving_architecture_approval_resumes_the_build(monkeypatch):
    build_id = _create_build_via_api()

    monkeypatch.setattr(
        build_manager,
        "delegate",
        lambda description, **kwargs: {
            "provider": "gemini", "task_type": "planning",
            "response": "Plan: FastAPI + SQLite.", "duration_ms": 10,
        },
    )
    build_manager.advance_builds()

    approval = _approval_for_build(build_id)

    response = client.post(f"/approvals/{approval['id']}/approve", headers=auth_headers())
    assert response.status_code == 200
    assert response.json()["status"] == "approved"

    build = client.get(f"/builds/{build_id}").json()
    assert build["status"] == "GENERATING"
    assert build["architecture_approved_by"] == api_module.BRIDGE_OPERATOR


def test_approving_deploy_approval_resumes_the_build(monkeypatch):
    build_id = _create_build_via_api()
    _force_status(build_id, "GENERATING")

    monkeypatch.setattr(
        build_manager,
        "delegate",
        lambda description, **kwargs: {
            "provider": "opencode_claude", "task_type": "coding", "duration_ms": 10,
            "response": {"success": True, "response_text": "Done.", "files_changed": ["app.py"], "commits": [], "tool_errors": []},
        },
    )
    monkeypatch.setattr(
        build_manager,
        "run_all_scans",
        lambda project_path: {"scanners": {}, "total_findings": 0, "highest_severity": None},
    )
    _disable_code_review(monkeypatch)
    build_manager.advance_builds()

    approval = _approval_for_build(build_id)

    response = client.post(f"/approvals/{approval['id']}/approve", headers=auth_headers())
    assert response.status_code == 200

    build = client.get(f"/builds/{build_id}").json()
    assert build["status"] == "DEPLOYING"
    assert build["deploy_approved_by"] == api_module.BRIDGE_OPERATOR


# -- Rejecting stops the build and records the reason ------------------------

def test_rejecting_architecture_approval_stops_the_build_and_records_a_learning_event(monkeypatch):
    build_id = _create_build_via_api()

    monkeypatch.setattr(
        build_manager,
        "delegate",
        lambda description, **kwargs: {
            "provider": "gemini", "task_type": "planning",
            "response": "Plan: FastAPI + SQLite.", "duration_ms": 10,
        },
    )
    build_manager.advance_builds()

    approval = _approval_for_build(build_id)

    response = client.post(
        f"/approvals/{approval['id']}/reject",
        json={"note": "Wrong framework choice"},
        headers=auth_headers(),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"

    build = client.get(f"/builds/{build_id}").json()
    assert build["status"] == "FAILED"
    assert build["failure_reason"] == "Wrong framework choice"

    history = [h for h in get_build_history() if h["build_id"] == build_id]
    assert len(history) == 1
    assert history[0]["status"] == "FAILED"
