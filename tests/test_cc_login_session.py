"""P0 Command Center login/session regression tests.

Covers the four root causes fixed together:

1. ``authz.create_session_for`` exists and mints a *verifiable JWT* (so Duo
   login can hand the SPA a usable Command Center session instead of the
   non-JWT Duo HMAC token).
2. Capability gates return **403** for a valid session that lacks the
   capability and **401** only for missing/invalid credentials — a viewer
   hitting ``/network/topology`` must not be logged out.
3. ``/auth/refresh`` mints a fresh JWT from a valid session (and 401s
   otherwise).
4. The persisted-session SPA logic (remember checkbox, refresh, resilient
   boot) is present and correct where statically unit-testable.
"""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core import authz
from core.api import app
from core.jwt_auth import verify_jwt

HTML = Path(__file__).parent.parent / "core" / "kai" / "command_center.html"


@pytest.fixture
def accounts_path(monkeypatch, tmp_path):
    accounts_file = tmp_path / "accounts.json"
    monkeypatch.setattr(authz, "ACCOUNTS_FILE", str(accounts_file))
    return accounts_file


@pytest.fixture
def client():
    return TestClient(app)


# ── 1. create_session_for ────────────────────────────────────────────────

def test_create_session_for_exists():
    assert hasattr(authz, "create_session_for")
    assert callable(authz.create_session_for)


def test_create_session_for_mints_verifiable_operator_jwt():
    token = authz.create_session_for("duo-operator", role="operator")

    claims = verify_jwt(token)
    assert claims is not None, "create_session_for must mint a signed JWT"
    assert claims["sub"] == "duo-operator"
    assert claims["role"] == "operator"
    assert authz.resolve_role(token) == "operator"
    # Operator JWT grants an operator capability route gate.
    assert authz.check_capability(token, "kai.command") is True


def test_create_session_for_viewer_gets_no_capabilities():
    token = authz.create_session_for("duo-viewer", role="viewer")
    assert verify_jwt(token) is not None
    assert authz.resolve_role(token) == "viewer"
    assert authz.check_capability(token, "kai.command") is False


def test_create_session_for_defaults_to_viewer():
    token = authz.create_session_for("someone")
    assert authz.resolve_role(token) == "viewer"


# ── 2. 401 vs 403 semantics ──────────────────────────────────────────────

def test_network_topology_operator_session_ok(client):
    token = authz.create_session_for("op", role="operator")
    r = client.get("/network/topology", headers={"X-Kai-Session": token})
    assert r.status_code == 200


def test_network_topology_viewer_session_is_403_not_401(client):
    token = authz.create_session_for("viewer", role="viewer")
    r = client.get("/network/topology", headers={"X-Kai-Session": token})
    assert r.status_code == 403, (
        "a valid session lacking the capability must get 403 so the SPA "
        "shows a permission error instead of logging the user out"
    )


def test_network_topology_invalid_token_is_401(client):
    r = client.get("/network/topology", headers={"X-Kai-Session": "not-a-jwt"})
    assert r.status_code == 401


def test_network_topology_no_credentials_is_401(client):
    r = client.get("/network/topology")
    assert r.status_code == 401


def test_module_proxy_viewer_write_is_403(client):
    token = authz.create_session_for("viewer", role="viewer")
    r = client.put("/cc/legal/anything", headers={"X-Kai-Session": token}, json={})
    assert r.status_code == 403


def test_module_proxy_invalid_token_write_is_401(client):
    r = client.put("/cc/legal/anything", headers={"X-Kai-Session": "bogus"}, json={})
    assert r.status_code == 401


def test_cc_extra_viewer_write_is_403(client):
    token = authz.create_session_for("viewer", role="viewer")
    r = client.put("/hubtel/config", headers={"X-Kai-Session": token},
                   json={"client_id": "x"})
    assert r.status_code == 403


# ── 3. /auth/refresh ─────────────────────────────────────────────────────

def test_refresh_mints_new_valid_session(client):
    old = authz.create_session_for("op", role="operator")
    r = client.post("/auth/refresh", headers={"X-Kai-Session": old})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token"] and body["token"] != old
    assert body["role"] == "operator"
    assert verify_jwt(body["token"]) is not None
    assert authz.resolve_role(body["token"]) == "operator"
    # The refreshed token must gate an operator route.
    topo = client.get("/network/topology", headers={"X-Kai-Session": body["token"]})
    assert topo.status_code == 200


def test_refresh_requires_valid_session(client):
    assert client.post("/auth/refresh").status_code == 401
    assert client.post("/auth/refresh",
                       headers={"X-Kai-Session": "garbage"}).status_code == 401


def test_refresh_session_helper_returns_none_for_invalid():
    assert authz.refresh_session("garbage") is None
    assert authz.refresh_session("") is None


# ── Duo SSO hands back a real Command Center session ─────────────────────

def test_duo_login_returns_verifiable_cc_session(monkeypatch):
    from core.auth import duo_sso

    monkeypatch.setattr(duo_sso, "BREAKGLASS", True)
    duo_sso._approvals.clear()
    out = duo_sso.login("duo-tester")

    assert out["ok"] is True
    cc = out.get("cc_token")
    assert cc, "Duo login must return a cc_token"
    claims = verify_jwt(cc)
    assert claims is not None, "cc_token must be a verifiable JWT"
    assert authz.resolve_role(cc) == duo_sso.DEFAULT_ROLE
    assert authz.check_capability(cc, "kai.command") is True


# ── 4. SPA session logic (static, where unit-testable) ───────────────────

@pytest.fixture(scope="module")
def html():
    return HTML.read_text()


def test_remember_checkbox_defaults_checked(html):
    m = re.search(r'<input type="checkbox" id="login-remember"([^>]*)>', html)
    assert m, "login-remember checkbox not found"
    assert "checked" in m.group(1), "remember checkbox must default to checked"


def test_save_token_persists_remember_choice(html):
    assert "REMEMBER_KEY" in html
    assert "localStorage.setItem(REMEMBER_KEY" in html
    # remembered sessions must survive a tab close → localStorage
    assert re.search(r"localStorage:sessionStorage\)\.setItem\(SK,d\)", html)


def test_boot_does_not_clear_token_on_transient_error(html):
    assert ".catch(()=>clearToken())" not in html, (
        "boot must not wipe the stored token on a transient /auth/status error"
    )
    assert "softLogin" in html


def test_spa_has_proactive_refresh(html):
    assert "'/auth/refresh'" in html
    assert "function refreshSession" in html
    assert "scheduleRefresh" in html


def test_api_does_not_clear_on_403(html):
    # An explicit 403 branch must exist and must not call clearToken.
    m = re.search(r"if\(r\.status===403\)\{(.*?)\n  \}", html, re.S)
    assert m, "api() should special-case 403"
    assert "clearToken" not in m.group(1)


def test_duo_enter_requires_cc_token(html):
    m = re.search(r"function _duoEnter\(d\)\{(.*?)\}", html, re.S)
    assert m
    assert "d.cc_token" in m.group(1), (
        "_duoEnter must require the orchestrator JWT, not fall back to the "
        "Duo HMAC token"
    )
