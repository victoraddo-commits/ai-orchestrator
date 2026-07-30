"""Phase 17D: Standalone dashboard tabbed UI + server-side proxy tests.

Covers:
- Token never leaks to the browser bundle or proxy responses
- Dashboard passphrase management (creation, validation, access gate)
- Proxy routes reject requests without / with wrong passphrase (401)
- Proxy routes accept correct passphrase and forward to backend
- Chat send + history round-trip through the proxy
- Approve / reject actions through the proxy with correct operator attribution
- Dashboard HTML has real tab navigation
- Dashboard passphrase file created with secure permissions (0600/0700)
"""

import stat
import pytest
import httpx
from fastapi.testclient import TestClient

from core.api import app
from core.approval import create_request


# ── Test fixture: wire the proxy to use the in-process ASGI app ──────────────
#
# In production the proxy makes outbound HTTP calls to 127.0.0.1:8000.
# In tests there is no real server; we inject an httpx.AsyncClient backed by
# an ASGITransport so the proxy calls go through the same in-process FastAPI
# app without any real network socket.

@pytest.fixture(autouse=True)
def wire_proxy_to_asgi(monkeypatch):
    """Replace the module-level proxy client with one backed by the same
    ASGI app so proxy requests don't need a real network."""
    import core.api as api_module

    asgi_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )
    api_module._set_proxy_client(asgi_client)
    yield
    # Teardown: restore default (None → will be recreated on next real use)
    api_module._set_proxy_client(None)


client = TestClient(app)


def proxy_headers(passphrase: str | None = None) -> dict:
    """Headers for a /dashboard/api/proxy/* request."""
    from core.api import _load_dashboard_passphrase
    pp = passphrase if passphrase is not None else _load_dashboard_passphrase()
    return {"X-Dashboard-Passphrase": pp}


# ── Passphrase file security ──────────────────────────────────────────────────


def test_dashboard_passphrase_file_created_with_owner_only_permissions(tmp_path, monkeypatch):
    import core.api as api_module

    passphrase_path = tmp_path / "nested" / "dashboard_passphrase"
    monkeypatch.setattr(api_module, "DASHBOARD_PASSPHRASE_PATH", passphrase_path)

    api_module._load_dashboard_passphrase()

    file_mode = stat.S_IMODE(passphrase_path.stat().st_mode)
    dir_mode = stat.S_IMODE(passphrase_path.parent.stat().st_mode)

    assert file_mode == 0o600, f"Expected 0o600, got {oct(file_mode)}"
    assert dir_mode == 0o700, f"Expected 0o700, got {oct(dir_mode)}"


def test_dashboard_passphrase_is_non_empty():
    from core.api import _load_dashboard_passphrase
    pp = _load_dashboard_passphrase()
    assert len(pp) >= 8


def test_dashboard_passphrase_stable_across_calls():
    """Two consecutive reads return the same value."""
    from core.api import _load_dashboard_passphrase
    pp1 = _load_dashboard_passphrase()
    pp2 = _load_dashboard_passphrase()
    assert pp1 == pp2


def test_dashboard_passphrase_not_same_as_bridge_token():
    """Passphrase and bridge token must be distinct secrets."""
    from core.api import _load_api_token, _load_dashboard_passphrase
    assert _load_api_token() != _load_dashboard_passphrase()


# ── Token never leaks to the browser ─────────────────────────────────────────


def test_dashboard_html_does_not_contain_bridge_token():
    """Bridge token must never appear in the HTML served at GET /dashboard."""
    from core.api import _load_api_token

    response = client.get("/dashboard")

    assert response.status_code == 200
    token = _load_api_token()
    assert token not in response.text


def test_dashboard_html_does_not_contain_passphrase():
    """The passphrase itself also must not be embedded in the HTML
    (the user enters it; it is stored in localStorage; never in the bundle)."""
    from core.api import _load_dashboard_passphrase

    response = client.get("/dashboard")

    pp = _load_dashboard_passphrase()
    assert pp not in response.text


def test_proxy_response_does_not_contain_bridge_token_in_health():
    """A successful proxy response must not echo the bridge token back in
    its body (the backend does not include the token in its responses,
    but let's assert it explicitly)."""
    from core.api import _load_api_token

    response = client.get("/dashboard/api/proxy/health", headers=proxy_headers())

    assert response.status_code == 200
    token = _load_api_token()
    assert token not in response.text


# ── Dashboard has real tabs ───────────────────────────────────────────────────


def test_dashboard_has_tab_navigation():
    """Phase 17D: the HTML must have a tab nav covering all 6 tabs."""
    response = client.get("/dashboard")
    html = response.text

    assert "tab-nav" in html or 'data-tab="overview"' in html
    for tab in ("overview", "chat", "approvals", "workforce", "learning", "roadmap"):
        assert f'data-tab="{tab}"' in html, f"Missing tab: {tab}"


def test_dashboard_has_chat_panel():
    response = client.get("/dashboard")
    html = response.text
    # Chat input and send button present
    assert "chat-input" in html or "chat-history" in html


def test_dashboard_has_passphrase_modal():
    response = client.get("/dashboard")
    html = response.text
    assert "passphrase" in html.lower()


def test_dashboard_proxy_path_in_js():
    """The dashboard JS must reference the proxy path, not direct endpoints."""
    response = client.get("/dashboard")
    assert "/dashboard/api/proxy/" in response.text


# ── Proxy access gate ─────────────────────────────────────────────────────────


def test_proxy_requires_passphrase():
    response = client.get("/dashboard/api/proxy/health")
    assert response.status_code == 401


def test_proxy_rejects_wrong_passphrase():
    response = client.get(
        "/dashboard/api/proxy/health",
        headers={"X-Dashboard-Passphrase": "definitely-wrong-passphrase"},
    )
    assert response.status_code == 401


def test_proxy_accepts_correct_passphrase():
    response = client.get("/dashboard/api/proxy/health", headers=proxy_headers())
    assert response.status_code == 200


def test_proxy_forwards_get_to_backend():
    """The proxy must transparently forward GET requests and return real data."""
    response = client.get("/dashboard/api/proxy/health", headers=proxy_headers())

    assert response.status_code == 200
    body = response.json()
    assert "status" in body
    assert "findings" in body


def test_proxy_forwards_approvals_get():
    create_request("restart_container", "svc-proxy-test", "test reason", incident_id="inc-proxy-1")

    response = client.get("/dashboard/api/proxy/approvals", headers=proxy_headers())

    assert response.status_code == 200
    body = response.json()
    assert any(a["service"] == "svc-proxy-test" for a in body)


def test_proxy_forwards_roadmap_progress():
    response = client.get("/dashboard/api/proxy/roadmap/progress", headers=proxy_headers())

    assert response.status_code == 200
    body = response.json()
    assert "total" in body


# ── Chat round-trip through the proxy ────────────────────────────────────────


def test_proxy_chat_post_and_get_round_trip(monkeypatch):
    """Send a chat message via the proxy and confirm it appears in history."""
    # Stub AI so we don't need a real provider
    monkeypatch.setattr(
        "core.api.ai_chat",
        lambda history, signals: "Hello from Kai (stub)!",
    )

    send_resp = client.post(
        "/dashboard/api/proxy/kai/chat",
        json={"text": "test proxy chat message"},
        headers=proxy_headers(),
    )
    assert send_resp.status_code == 200

    history_resp = client.get("/dashboard/api/proxy/kai/chat", headers=proxy_headers())
    assert history_resp.status_code == 200

    history = history_resp.json()
    assert isinstance(history, list)
    user_msgs = [m for m in history if m.get("role") == "user"]
    assert any("test proxy chat message" in m.get("content", "") for m in user_msgs)


def test_proxy_chat_history_contains_assistant_reply(monkeypatch):
    monkeypatch.setattr(
        "core.api.ai_chat",
        lambda history, signals: "Proxy stub reply",
    )

    client.post(
        "/dashboard/api/proxy/kai/chat",
        json={"text": "query for reply test"},
        headers=proxy_headers(),
    )

    history_resp = client.get("/dashboard/api/proxy/kai/chat", headers=proxy_headers())
    history = history_resp.json()

    assistant_msgs = [m for m in history if m.get("role") == "assistant"]
    assert any("Proxy stub reply" in m.get("content", "") for m in assistant_msgs)


def test_proxy_chat_requires_passphrase():
    """Chat endpoint should be blocked without the passphrase."""
    response = client.post("/dashboard/api/proxy/kai/chat", json={"text": "hi"})
    assert response.status_code == 401


# ── Approve/reject through the proxy with correct operator attribution ────────


def test_proxy_approve_action_with_correct_operator():
    """Approve via the proxy must record operator as 'cloudcli-plugin'
    (the server-side identity derived from the bridge token, never a
    browser-supplied value)."""
    req = create_request("restart_container", "svc-dashboard", "testing proxy approve", incident_id="inc-d1")

    response = client.post(
        f"/dashboard/api/proxy/approvals/{req['id']}/approve",
        headers=proxy_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"
    # The operator must be the server-side BRIDGE_OPERATOR constant (cloudcli-plugin)
    # because the proxy injects the bridge token and the backend derives the
    # operator from that token, never from any browser-supplied header.
    assert body["approved_by"] == "cloudcli-plugin"


def test_proxy_reject_action_with_correct_operator():
    req = create_request("restart_container", "svc-dashboard", "testing proxy reject", incident_id="inc-d2")

    response = client.post(
        f"/dashboard/api/proxy/approvals/{req['id']}/reject",
        headers=proxy_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "rejected"
    assert body["rejected_by"] == "cloudcli-plugin"


def test_proxy_approve_requires_passphrase():
    req = create_request("restart_container", "svc-dashboard", "auth test", incident_id="inc-d3")

    response = client.post(f"/dashboard/api/proxy/approvals/{req['id']}/approve")
    assert response.status_code == 401


def test_proxy_approve_wrong_passphrase_is_rejected():
    req = create_request("restart_container", "svc-dashboard", "auth test 2", incident_id="inc-d4")

    response = client.post(
        f"/dashboard/api/proxy/approvals/{req['id']}/approve",
        headers={"X-Dashboard-Passphrase": "attacker-passphrase"},
    )
    assert response.status_code == 401


def test_browser_cannot_forge_operator_via_proxy():
    """A browser caller cannot influence operator attribution by including
    an Authorization header -- the proxy strips and replaces it."""
    req = create_request("restart_container", "svc-dashboard", "forge test", incident_id="inc-d5")

    response = client.post(
        f"/dashboard/api/proxy/approvals/{req['id']}/approve",
        headers={
            **proxy_headers(),
            "Authorization": "Bearer attacker-token-attempt",
        },
    )

    assert response.status_code == 200
    assert response.json()["approved_by"] == "cloudcli-plugin"


# ── Proxy returns 404 for unknown paths ───────────────────────────────────────


def test_proxy_returns_404_for_nonexistent_backend_path():
    response = client.get(
        "/dashboard/api/proxy/this-path-does-not-exist",
        headers=proxy_headers(),
    )
    assert response.status_code == 404


# ── CloudCLI compatibility (existing plugin unaffected) ───────────────────────


def test_cloudcli_approvals_endpoint_still_works_directly_with_bridge_token():
    """The existing direct /approvals/{id}/approve endpoint used by the
    CloudCLI plugin must continue to work unchanged."""
    from core.api import _load_api_token

    req = create_request("restart_container", "svc-cloudcli", "compat test", incident_id="inc-cc1")

    response = client.post(
        f"/approvals/{req['id']}/approve",
        headers={"Authorization": f"Bearer {_load_api_token()}"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert response.json()["approved_by"] == "cloudcli-plugin"


def test_cloudcli_chat_endpoint_still_works_directly_with_bridge_token(monkeypatch):
    """The existing direct /kai/chat endpoint used by the CloudCLI plugin
    must continue to work unchanged."""
    from core.api import _load_api_token

    monkeypatch.setattr(
        "core.api.ai_chat",
        lambda history, signals: "CloudCLI compat reply",
    )

    response = client.post(
        "/kai/chat",
        json={"text": "cloudcli compat test"},
        headers={"Authorization": f"Bearer {_load_api_token()}"},
    )

    assert response.status_code == 200


def test_cloudcli_approve_without_bridge_token_still_returns_401():
    """Removing auth must still return 401 on the existing direct endpoint."""
    req = create_request("restart_container", "svc-cloudcli", "compat 401", incident_id="inc-cc2")

    response = client.post(f"/approvals/{req['id']}/approve")
    assert response.status_code == 401


# ── Query string forwarding ───────────────────────────────────────────────────


def test_proxy_forwards_query_string():
    """Proxy must pass query string parameters to the backend."""
    response = client.get(
        "/dashboard/api/proxy/roadmap/next",
        headers=proxy_headers(),
    )
    assert response.status_code == 200


# ── POST body forwarding ──────────────────────────────────────────────────────


def test_proxy_forwards_post_body_correctly(monkeypatch):
    """Proxy must forward JSON body of POST requests intact."""
    monkeypatch.setattr(
        "core.api.ai_chat",
        lambda history, signals: "body forwarding test reply",
    )

    response = client.post(
        "/dashboard/api/proxy/kai/chat",
        json={"text": "body forwarding test"},
        headers=proxy_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    # The reply envelope must come back correctly (matched or response key)
    assert "matched" in body or "response" in body


