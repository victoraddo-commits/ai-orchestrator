"""Tests for /auth/kai/* OIDC callback routes."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from core.api import app
from core.oidc_client import OIDCError


@pytest.fixture
def client():
    # follow_redirects=False because routes redirect to vault external URL
    # which is not available in the test environment
    return TestClient(app, follow_redirects=False)


# ---------------------------------------------------------------------------
# Helper mocks
# ---------------------------------------------------------------------------

def _mock_get_auth_url():
    return "https://vault.sso.deerude.com/sso/authorize?...", "secretstate123"


# ---------------------------------------------------------------------------
# 1. GET /auth/kai/start
# ---------------------------------------------------------------------------

def test_auth_kai_start_redirects_to_vault(client):
    with patch("core.auth_kai_routes._oidc.get_authorization_url", return_value=_mock_get_auth_url()):
        response = client.get("/auth/kai/start")

    assert response.status_code == 302
    assert "Location" in response.headers
    assert response.headers["Location"].startswith("https://vault.sso.deerude.com")
    assert response.headers.get("X-OIDC-State") == "secretstate123"


# ---------------------------------------------------------------------------
# 2. GET /auth/kai/callback — success
# ---------------------------------------------------------------------------

def test_auth_kai_callback_success_mints_jwt(client):
    vault_response = {
        "user": {"id": "u1", "username": "alice", "role": "operator"},
        "step_up_fresh": False,
    }
    with patch("core.auth_kai_routes._oidc.validate_state", return_value=True), \
         patch("core.auth_kai_routes._oidc.exchange_code", return_value=vault_response), \
         patch("core.auth_kai_routes._oidc.send_audit_event"), \
         patch("core.auth_kai_routes.jwt_auth.create_jwt", return_value="fake.jwt.token"):
        response = client.get("/auth/kai/callback?code=authcode&state=secretstate123")

    assert response.status_code == 302
    assert response.headers["Location"] == "/dashboard"
    set_cookie = response.headers.get("set-cookie", "")
    assert "kai_session=fake.jwt.token" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "Max-Age=86400" in set_cookie  # 24h


# ---------------------------------------------------------------------------
# 3. GET /auth/kai/callback — invalid state
# ---------------------------------------------------------------------------

def test_auth_kai_callback_invalid_state_returns_400(client):
    with patch("core.auth_kai_routes._oidc.validate_state", return_value=False):
        response = client.get("/auth/kai/callback?code=authcode&state=badstate")

    assert response.status_code == 400
    assert "Invalid or expired state" in response.json()["detail"]


# ---------------------------------------------------------------------------
# 4. GET /auth/kai/callback — vault unreachable
# ---------------------------------------------------------------------------

def test_auth_kai_callback_vault_unreachable_returns_503(client):
    with patch("core.auth_kai_routes._oidc.validate_state", return_value=True), \
         patch("core.auth_kai_routes._oidc.exchange_code",
               side_effect=OIDCError("vault_unreachable: connection refused")):
        response = client.get("/auth/kai/callback?code=authcode&state=secretstate123")

    assert response.status_code == 503
    body = response.json()
    assert "vault_unreachable" in str(body)


# ---------------------------------------------------------------------------
# 5. GET /auth/kai/userinfo — success
# ---------------------------------------------------------------------------

def test_auth_kai_userinfo_returns_user_data(client):
    jwt_claims = {
        "sub": "u1",
        "username": "alice",
        "role": "operator",
        "vault_role": "admin",
        "step_up_fresh": False,
    }
    with patch("core.auth_kai_routes.jwt_auth.verify_jwt", return_value=jwt_claims):
        client.cookies.set("kai_session", "validtoken")
        response = client.get("/auth/kai/userinfo")

    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "alice"
    assert body["role"] == "operator"
    assert body["vault_role"] == "admin"
    assert body["step_up_fresh"] is False


# ---------------------------------------------------------------------------
# 6. POST /auth/kai/step-up — no cookie → 401
# ---------------------------------------------------------------------------

def test_auth_kai_stepup_requires_session(client):
    response = client.post("/auth/kai/step-up")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# 7. POST /auth/kai/logout — clears cookie
# ---------------------------------------------------------------------------

def test_auth_kai_logout_clears_cookie(client):
    with patch("core.auth_kai_routes.jwt_auth.verify_jwt", return_value={"sub": "u1"}), \
         patch("core.auth_kai_routes.authz.invalidate_session"), \
         patch("core.auth_kai_routes._oidc.send_audit_event"):
        response = client.post("/auth/kai/logout", cookies={"kai_session": "tokept"})

    assert response.status_code == 200
    set_cookie = response.headers.get("set-cookie", "")
    assert "kai_session=" in set_cookie
    assert "Max-Age=0" in set_cookie
