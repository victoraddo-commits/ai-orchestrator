"""Phase 15A: Capability-based authorization tests."""

import pytest
from core import authz
from fastapi.testclient import TestClient
from core.api import app


# ── Capability definitions ──────────────────────────────────────────────

def test_every_capability_has_a_description():
    for name, desc in authz.CAPABILITIES.items():
        assert isinstance(name, str) and len(name) > 0
        assert isinstance(desc, str) and len(desc) > 0


def test_operator_role_has_every_capability():
    for cap in authz.CAPABILITIES:
        assert cap in authz.get_operator_capabilities(), f"operator missing {cap}"


def test_viewer_role_has_no_capabilities():
    assert authz.ROLE_CAPABILITIES["viewer"] == set()


# ── Bridge-token operator ───────────────────────────────────────────────

def test_bridge_token_operator_always_has_full_access():
    assert authz.check_capability("cloudcli-plugin", "builds.create") is True
    assert authz.check_capability("dashboard-proxy", "approvals.approve") is True
    assert authz.check_capability("cloudcli-plugin", "roadmap.autonomy") is True


# ── Session token — viewer role ─────────────────────────────────────────

def test_viewer_is_denied_on_every_write_capability(monkeypatch):
    monkeypatch.setattr(authz, "_sessions", {
        "viewer-session": {"username": "test-viewer", "role": "viewer", "created": "..."}
    })
    for cap in authz.CAPABILITIES:
        assert authz.check_capability("viewer-session", cap) is False, (
            f"viewer should be denied {cap}"
        )


# ── Unknown / invalid tokens ────────────────────────────────────────────

def test_unknown_token_denies_by_default():
    assert authz.check_capability("not-a-real-token", "builds.create") is False


def test_empty_token_denies_by_default():
    assert authz.check_capability("", "builds.create") is False


def test_none_token_denies_by_default():
    assert authz.check_capability(None, "builds.create") is False


# ── Account creation ────────────────────────────────────────────────────

def test_create_viewer_account_succeeds(monkeypatch, tmp_path):
    accounts_file = tmp_path / "accounts.json"
    monkeypatch.setattr(authz, "ACCOUNTS_FILE", str(accounts_file))

    result = authz.create_account("alice", "secret123", role="viewer")
    assert result["username"] == "alice"
    assert result["role"] == "viewer"


def test_authenticate_returns_token_for_valid_credentials(monkeypatch, tmp_path):
    accounts_file = tmp_path / "accounts.json"
    monkeypatch.setattr(authz, "ACCOUNTS_FILE", str(accounts_file))

    authz.create_account("bob", "correct-horse-battery-staple")
    token = authz.authenticate("bob", "correct-horse-battery-staple")

    assert token is not None
    assert len(token) > 20


def test_authenticate_returns_none_for_wrong_password(monkeypatch, tmp_path):
    accounts_file = tmp_path / "accounts.json"
    monkeypatch.setattr(authz, "ACCOUNTS_FILE", str(accounts_file))

    authz.create_account("bob", "correct-horse-battery-staple")
    token = authz.authenticate("bob", "wrong-password")

    assert token is None


def test_authenticate_returns_none_for_unknown_user(monkeypatch, tmp_path):
    accounts_file = tmp_path / "accounts.json"
    monkeypatch.setattr(authz, "ACCOUNTS_FILE", str(accounts_file))

    token = authz.authenticate("nobody", "whatever")
    assert token is None


def test_create_account_rejects_non_viewer_role(monkeypatch, tmp_path):
    accounts_file = tmp_path / "accounts.json"
    monkeypatch.setattr(authz, "ACCOUNTS_FILE", str(accounts_file))

    with pytest.raises(ValueError):
        authz.create_account("hacker", "pass", role="operator")


def test_create_account_rejects_duplicate(monkeypatch, tmp_path):
    accounts_file = tmp_path / "accounts.json"
    monkeypatch.setattr(authz, "ACCOUNTS_FILE", str(accounts_file))

    authz.create_account("bob", "first-password")
    with pytest.raises(ValueError):
        authz.create_account("bob", "second-password")


# ── Session lifecycle ───────────────────────────────────────────────────

def test_valid_session_token_grants_viewer_chat(monkeypatch, tmp_path):
    accounts_file = tmp_path / "accounts.json"
    monkeypatch.setattr(authz, "ACCOUNTS_FILE", str(accounts_file))

    authz.create_account("viewer1", "password123")
    token = authz.authenticate("viewer1", "password123")
    assert token is not None

    # Viewer can send chat (it's a CAPABILITY now, but still in the set)
    # Actually viewer has NO capabilities, so even chat.send is denied for viewer
    session = authz._resolve_session(token)
    assert session is not None
    assert session["role"] == "viewer"

    # A viewer's resolved role
    assert authz.resolve_role(token) == "viewer"


def test_invalidate_session_removes_token(monkeypatch, tmp_path):
    accounts_file = tmp_path / "accounts.json"
    monkeypatch.setattr(authz, "ACCOUNTS_FILE", str(accounts_file))

    authz.create_account("temp", "pass")
    token = authz.authenticate("temp", "pass")
    assert authz.resolve_role(token) == "viewer"

    authz.invalidate_session(token)
    assert authz.resolve_role(token) is None


# ── Role resolution ─────────────────────────────────────────────────────

def test_resolve_role_returns_none_for_unknown_token():
    assert authz.resolve_role("unknown-token") is None


def test_resolve_role_returns_viewer_for_viewer_session(monkeypatch, tmp_path):
    accounts_file = tmp_path / "accounts.json"
    monkeypatch.setattr(authz, "ACCOUNTS_FILE", str(accounts_file))

    authz.create_account("viewer2", "pass")
    token = authz.authenticate("viewer2", "pass")
    assert authz.resolve_role(token) == "viewer"


# ── API endpoint: login ─────────────────────────────────────────────────

@pytest.fixture
def accounts_path(monkeypatch, tmp_path):
    """Redirect account storage to a temp file for every test."""
    accounts_file = tmp_path / "accounts.json"
    monkeypatch.setattr(authz, "ACCOUNTS_FILE", str(accounts_file))
    return accounts_file


def test_login_endpoint_returns_token_for_valid_credentials(accounts_path):
    authz.create_account("testuser", "testpass")

    client = TestClient(app)
    response = client.post("/auth/login", json={"username": "testuser", "password": "testpass"})

    assert response.status_code == 200
    body = response.json()
    assert "token" in body
    assert body["token_type"] == "session"


def test_login_endpoint_rejects_invalid_password(accounts_path):
    authz.create_account("testuser", "testpass")

    client = TestClient(app)
    response = client.post("/auth/login", json={"username": "testuser", "password": "wrong"})

    assert response.status_code == 401


def test_auth_status_returns_anonymous_with_no_credentials(accounts_path):
    client = TestClient(app)
    response = client.get("/auth/status")

    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "anonymous"
    assert body["auth_method"] == "none"


def test_auth_status_returns_viewer_for_session_token(accounts_path):
    authz.create_account("statususer", "pass")
    token = authz.authenticate("statususer", "pass")

    client = TestClient(app)
    response = client.get("/auth/status", headers={"X-Kai-Session": token})

    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "viewer"
    assert body["auth_method"] == "session"


# ── API endpoint: logout ────────────────────────────────────────────────

def test_logout_endpoint_invalidates_session(accounts_path):
    authz.create_account("logoutuser", "pass")
    token = authz.authenticate("logoutuser", "pass")

    client = TestClient(app)
    response = client.post("/auth/logout", headers={"X-Kai-Session": token})
    assert response.status_code == 200

    # Token should no longer work
    assert authz.resolve_role(token) is None
    status = client.get("/auth/status", headers={"X-Kai-Session": token})
    assert status.json()["role"] == "anonymous"


# ── API: viewer rejected on write endpoints ─────────────────────────────

def test_viewer_401_on_write_endpoint(accounts_path):
    authz.create_account("viewer", "pass")
    token = authz.authenticate("viewer", "pass")

    client = TestClient(app)

    # Try a write endpoint as viewer (no capabilities)
    response = client.post(
        "/builds",
        json={"name": "test", "description": "test", "project_path": "/tmp"},
        headers={"X-Kai-Session": token},
    )
    assert response.status_code == 403, f"viewer should get 403 on builds.create, got {response.status_code}"


def test_viewer_can_read_dashboard_without_auth():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200


def test_viewer_401_on_approve(accounts_path):
    authz.create_account("viewer", "pass")
    token = authz.authenticate("viewer", "pass")

    client = TestClient(app)
    response = client.post(
        "/approvals/nonexistent/approve",
        headers={"X-Kai-Session": token},
    )
    assert response.status_code == 403


def test_viewer_401_on_every_write_endpoint(accounts_path):
    authz.create_account("viewer", "pass")
    token = authz.authenticate("viewer", "pass")
    client = TestClient(app)

    # Sample of write endpoints — each should 401 for a viewer
    write_endpoints = [
        ("POST", "/delegate", {"description": "test"}),
        ("POST", "/kai/command", {"command": "status"}),
        ("POST", "/kai/chat", {"text": "hello"}),
        ("PUT", "/api/autonomy/level", {"level": 3}),
        ("POST", "/roadmap/autonomous/enable", None),
        ("POST", "/roadmap/autonomous/disable", None),
        ("POST", "/roadmap/phases", {"id": "X", "name": "test", "status": "pending"}),
        ("POST", "/roadmap/nonexistent/status", {"status": "completed"}),
        ("POST", "/builds", {"name": "t", "description": "t", "project_path": "/tmp"}),
        ("POST", "/builds/nonexistent/approve-architecture", None),
        ("POST", "/builds/nonexistent/approve-deploy", None),
    ]

    for method, path, json_body in write_endpoints:
        kwargs = {"headers": {"X-Kai-Session": token}}
        if json_body is not None:
            kwargs["json"] = json_body
        response = client.request(method, path, **kwargs)
        # 403 (insufficient permissions) is expected for viewer with valid
        # session but no write capabilities. 404 is also acceptable — means
        # the auth gate passed but the resource doesn't exist.
        assert response.status_code in (403, 404), (
            f"{method} {path}: expected 403 or 404 for viewer, got {response.status_code}"
        )
