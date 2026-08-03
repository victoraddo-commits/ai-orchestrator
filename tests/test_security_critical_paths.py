"""Phase 15B: Security-critical path enforcement for self-modifying builds."""

import pytest
import core.build_manager as build_manager
from core.lifecycle import InvalidTransition
from core import authz


def _force_state(build_id, status, **kwargs):
    builds = build_manager.load_builds()
    for b in builds:
        if b["id"] == build_id:
            b["status"] = status
            b.update(kwargs)
    build_manager.save_builds(builds)


# ── SECURITY_CRITICAL_PATHS constant ─────────────────────────────────────

def test_security_critical_paths_defined():
    assert isinstance(authz.SECURITY_CRITICAL_PATHS, set)
    assert "core/authz.py" in authz.SECURITY_CRITICAL_PATHS
    assert "core/security.py" in authz.SECURITY_CRITICAL_PATHS
    assert "memory/accounts.json" in authz.SECURITY_CRITICAL_PATHS
    assert "core/llm_clients.py" in authz.SECURITY_CRITICAL_PATHS
    assert "core/api.py" in authz.SECURITY_CRITICAL_PATHS


# ── Build approval: security-critical path detection ─────────────────────

def test_build_touching_security_critical_file_gets_flagged(monkeypatch):
    mock_calls = []
    monkeypatch.setattr(build_manager, "create_build_approval", lambda **kw: mock_calls.append(kw))

    build = {
        "id": "test-sc-1",
        "name": "Auth Change",
        "description": "Change authz",
        "project_path": "/tmp/test-sc-1",
        "generation_result": {"files_changed": ["core/authz.py", "src/main.py"]},
        "plan": "Modify authz logic",
        "status": "WAITING_FOR_ARCHITECTURE_APPROVAL",
    }
    build_manager._create_architecture_approval(build)

    assert len(mock_calls) == 1
    assert mock_calls[0]["risk"] == "security-critical"


def test_build_touching_only_normal_files_not_flagged(monkeypatch):
    mock_calls = []
    monkeypatch.setattr(build_manager, "create_build_approval", lambda **kw: mock_calls.append(kw))

    build = {
        "id": "test-sc-2",
        "name": "Normal Change",
        "description": "Change UI",
        "project_path": "/tmp/test-sc-2",
        "generation_result": {"files_changed": ["src/main.py", "src/utils.py"]},
        "plan": "Modify UI logic",
        "status": "WAITING_FOR_ARCHITECTURE_APPROVAL",
    }
    build_manager._create_architecture_approval(build)

    assert len(mock_calls) == 1
    assert mock_calls[0]["risk"] is None


def test_build_with_no_files_changed_not_flagged(monkeypatch):
    mock_calls = []
    monkeypatch.setattr(build_manager, "create_build_approval", lambda **kw: mock_calls.append(kw))

    build = {
        "id": "test-sc-3",
        "name": "No Changes",
        "description": "Nothing changed",
        "project_path": "/tmp/test-sc-3",
        "generation_result": {"files_changed": []},
        "plan": "No diff",
        "status": "WAITING_FOR_ARCHITECTURE_APPROVAL",
    }
    build_manager._create_architecture_approval(build)

    assert len(mock_calls) == 1
    assert mock_calls[0]["risk"] is None


def test_build_with_no_generation_result_not_flagged(monkeypatch):
    mock_calls = []
    monkeypatch.setattr(build_manager, "create_build_approval", lambda **kw: mock_calls.append(kw))

    build = {
        "id": "test-sc-4",
        "name": "No Gen Result",
        "description": "Planning only",
        "project_path": "/tmp/test-sc-4",
        "plan": "Architecture plan",
        "status": "WAITING_FOR_ARCHITECTURE_APPROVAL",
    }
    build_manager._create_architecture_approval(build)

    assert len(mock_calls) == 1
    assert mock_calls[0]["risk"] is None


# ── approve_architecture: security-critical enforcement ──────────────────

def test_operator_can_approve_security_critical_architecture():
    build = build_manager.create_build("sc-build", "desc", "/tmp/sc-test")
    _force_state(build["id"], "WAITING_FOR_ARCHITECTURE_APPROVAL", risk="security-critical")

    updated = build_manager.approve_architecture(build["id"], operator="cloudcli-plugin")

    assert updated["status"] == "ARCHITECTURE_APPROVED"
    assert updated["architecture_approved_by"] == "cloudcli-plugin"


def test_viewer_session_rejected_on_security_critical_architecture(monkeypatch):
    monkeypatch.setattr(authz, "_sessions", {
        "viewer-session": {"username": "test-viewer", "role": "viewer", "created": "..."}
    })

    build = build_manager.create_build("sc-build-v", "desc", "/tmp/sc-test-v")
    _force_state(build["id"], "WAITING_FOR_ARCHITECTURE_APPROVAL", risk="security-critical")

    with pytest.raises(PermissionError, match="operator role"):
        build_manager.approve_architecture(build["id"], operator="viewer-session")


def test_unknown_token_rejected_on_security_critical_architecture():
    build = build_manager.create_build("sc-build-u", "desc", "/tmp/sc-test-u")
    _force_state(build["id"], "WAITING_FOR_ARCHITECTURE_APPROVAL", risk="security-critical")

    with pytest.raises(PermissionError, match="operator role"):
        build_manager.approve_architecture(build["id"], operator="unknown-token")


def test_none_operator_rejected_on_security_critical_architecture():
    build = build_manager.create_build("sc-build-none", "desc", "/tmp/sc-test-none")
    _force_state(build["id"], "WAITING_FOR_ARCHITECTURE_APPROVAL", risk="security-critical")

    with pytest.raises(PermissionError, match="operator role"):
        build_manager.approve_architecture(build["id"], operator=None)


def test_bridge_token_operator_can_approve_security_critical_architecture():
    build = build_manager.create_build("sc-build-bt", "desc", "/tmp/sc-test-bt")
    _force_state(build["id"], "WAITING_FOR_ARCHITECTURE_APPROVAL", risk="security-critical")

    updated = build_manager.approve_architecture(build["id"], operator="dashboard-proxy")

    assert updated["status"] == "ARCHITECTURE_APPROVED"
    assert updated["architecture_approved_by"] == "dashboard-proxy"


def test_normal_build_approval_unaffected_by_security_check():
    build = build_manager.create_build("normal-build", "desc", "/tmp/normal-test")
    _force_state(build["id"], "WAITING_FOR_ARCHITECTURE_APPROVAL")

    updated = build_manager.approve_architecture(build["id"], operator="alice")

    assert updated["status"] == "ARCHITECTURE_APPROVED"


# ── approve_deploy: security-critical enforcement ────────────────────────

def test_operator_can_approve_security_critical_deploy():
    build = build_manager.create_build("sc-deploy", "desc", "/tmp/sc-dep-test")
    _force_state(build["id"], "WAITING_FOR_DEPLOY_APPROVAL", risk="security-critical")

    updated = build_manager.approve_deploy(build["id"], operator="cloudcli-plugin")

    assert updated["status"] == "DEPLOYING"
    assert updated["deploy_approved_by"] == "cloudcli-plugin"


def test_viewer_session_rejected_on_security_critical_deploy(monkeypatch):
    monkeypatch.setattr(authz, "_sessions", {
        "viewer-session": {"username": "test-viewer", "role": "viewer", "created": "..."}
    })

    build = build_manager.create_build("sc-deploy-v", "desc", "/tmp/sc-dep-test-v")
    _force_state(build["id"], "WAITING_FOR_DEPLOY_APPROVAL", risk="security-critical")

    with pytest.raises(PermissionError, match="operator role"):
        build_manager.approve_deploy(build["id"], operator="viewer-session")


def test_unknown_token_rejected_on_security_critical_deploy():
    build = build_manager.create_build("sc-deploy-u", "desc", "/tmp/sc-dep-test-u")
    _force_state(build["id"], "WAITING_FOR_DEPLOY_APPROVAL", risk="security-critical")

    with pytest.raises(PermissionError, match="operator role"):
        build_manager.approve_deploy(build["id"], operator="unknown-token")


def test_normal_deploy_approval_unaffected_by_security_check():
    build = build_manager.create_build("normal-deploy", "desc", "/tmp/normal-dep-test")
    _force_state(build["id"], "WAITING_FOR_DEPLOY_APPROVAL")

    updated = build_manager.approve_deploy(build["id"], operator="alice")

    assert updated["status"] == "DEPLOYING"


# ── approve_architecture: state-transition guard unaffected ──────────────

def test_approve_architecture_state_guard_still_works():
    build = build_manager.create_build("state-guard", "desc", "/tmp/sg-test")

    with pytest.raises(InvalidTransition):
        build_manager.approve_architecture(build["id"], operator="cloudcli-plugin")


def test_approve_deploy_state_guard_still_works():
    build = build_manager.create_build("state-guard-d", "desc", "/tmp/sg-d-test")

    with pytest.raises(InvalidTransition):
        build_manager.approve_deploy(build["id"], operator="cloudcli-plugin")
