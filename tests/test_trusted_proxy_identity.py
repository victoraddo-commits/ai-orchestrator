"""Task 2: trusted-peer gate for the X-Kai-User identity headers.

Only a direct peer inside ``KAI_TRUSTED_PROXIES`` may assert an identity via
``X-Kai-User`` / ``X-Kai-User-Id``. An untrusted source that forges those
headers (or a spoofed X-Forwarded-For) must get 401, while the bridge token and
authz session paths keep working and a trusted peer (the real CC proxy) is
unaffected.

TDD: written before the trusted-proxy gate.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.auth import trusted_proxy
from core.juris_kai import cc_routes

BRIDGE = {"Authorization": "Bearer test-bridge-token"}
IDENTITY = {"X-Kai-User": "owner@kai", "X-Kai-User-Id": "owner"}

UNTRUSTED = ("203.0.113.9", 54321)  # TEST-NET-3, never in the allowlist


class _Req:
    """Minimal stand-in for a Starlette Request for helper-level tests."""

    def __init__(self, peer, headers=None):
        self.client = type("C", (), {"host": peer})()
        self.headers = headers or {}


def _app():
    app = FastAPI()
    app.include_router(cc_routes.router)
    return app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("core.bridge_auth._load_api_token",
                        lambda: "test-bridge-token")
    cc_routes._rate_state.clear()
    return TestClient(_app())


@pytest.fixture
def untrusted_client(monkeypatch):
    monkeypatch.setattr("core.bridge_auth._load_api_token",
                        lambda: "test-bridge-token")
    cc_routes._rate_state.clear()
    return TestClient(_app(), client=UNTRUSTED)


@pytest.fixture(autouse=True)
def _cache_stats(monkeypatch):
    monkeypatch.setattr(cc_routes, "_cache_stats", lambda: {"generation": {}})


# ── helper level ──────────────────────────────────────────────────────────

def test_loopback_is_trusted():
    assert trusted_proxy.is_trusted_peer(_Req("127.0.0.1")) is True
    assert trusted_proxy.proxy_identity(_Req("127.0.0.1"), "u", "1") == \
        "auth-proxy:1"


def test_untrusted_peer_is_not_trusted_and_identity_is_none():
    req = _Req(UNTRUSTED[0])
    assert trusted_proxy.is_trusted_peer(req) is False
    assert trusted_proxy.proxy_identity(req, "u", "1") is None


def test_spoofed_xff_from_untrusted_source_does_not_grant_trust():
    req = _Req(UNTRUSTED[0], {"x-forwarded-for": "127.0.0.1"})
    assert trusted_proxy.is_trusted_peer(req) is False
    assert trusted_proxy.client_ip(req) == UNTRUSTED[0]
    assert trusted_proxy.proxy_identity(req, "u", "1") is None


def test_xff_from_trusted_peer_is_used_for_client_ip():
    req = _Req("127.0.0.1", {"x-forwarded-for": "198.51.100.7, 127.0.0.1"})
    assert trusted_proxy.client_ip(req) == "198.51.100.7"


def test_allowlist_is_env_configurable(monkeypatch):
    monkeypatch.setenv("KAI_TRUSTED_PROXIES", "10.9.8.7, 172.16.0.0/12")
    assert trusted_proxy.is_trusted_peer(_Req("10.9.8.7")) is True
    assert trusted_proxy.is_trusted_peer(_Req("172.16.5.4")) is True
    # loopback is no longer trusted once the env override replaces the default
    assert trusted_proxy.is_trusted_peer(_Req("127.0.0.1")) is False


# ── read gate integration ─────────────────────────────────────────────────

def test_trusted_peer_with_headers_accepted(client):
    r = client.get("/api/juris-kai/cc/cache", headers=IDENTITY)
    assert r.status_code == 200


def test_untrusted_peer_with_forged_headers_is_401(untrusted_client):
    r = untrusted_client.get("/api/juris-kai/cc/cache", headers=IDENTITY)
    assert r.status_code == 401


def test_untrusted_peer_spoofed_xff_is_still_401(untrusted_client):
    headers = {**IDENTITY, "X-Forwarded-For": "127.0.0.1"}
    r = untrusted_client.get("/api/juris-kai/cc/cache", headers=headers)
    assert r.status_code == 401


def test_bridge_token_still_accepted(client):
    r = client.get("/api/juris-kai/cc/cache", headers=BRIDGE)
    assert r.status_code == 200


def test_session_still_accepted(client, monkeypatch):
    monkeypatch.setattr("core.authz.check_capability", lambda *a, **k: True)
    r = client.get("/api/juris-kai/cc/cache", headers={"X-Kai-Session": "sess"})
    assert r.status_code == 200


# ── write gate integration (cc_test_query) ────────────────────────────────

def test_write_untrusted_peer_forged_headers_is_401(untrusted_client):
    r = untrusted_client.post("/api/juris-kai/cc/test-query",
                              headers=IDENTITY, json={"query": "x"})
    assert r.status_code == 401


def test_write_trusted_peer_headers_accepted(client, monkeypatch):
    monkeypatch.setattr("core.juris_kai.legal_context.query_knowledge_base",
                        lambda q: [])
    monkeypatch.setattr("core.juris_kai.legal_context.build_context_preamble",
                        lambda docs: "")
    monkeypatch.setattr("core.juris_kai.streaming.generate",
                        lambda prompt, task_type="legal_research", **k: "ok")
    r = client.post("/api/juris-kai/cc/test-query", headers=IDENTITY,
                    json={"query": "contract law", "stream": False})
    assert r.status_code == 200
    assert r.json()["text"] == "ok"


# ── same class of bug in the sibling CC routers ───────────────────────────

def _sibling_app():
    from core.cc_extra_routes import cc_extra_router
    from core.cc_modules import cc_router
    app = FastAPI()
    app.include_router(cc_extra_router)
    app.include_router(cc_router)
    return app


def test_cc_extra_untrusted_headers_is_401(monkeypatch):
    client = TestClient(_sibling_app(), client=UNTRUSTED)
    r = client.get("/api/diagnostics", headers=IDENTITY)
    assert r.status_code == 401


def test_cc_extra_trusted_headers_is_200():
    client = TestClient(_sibling_app())
    r = client.get("/api/diagnostics", headers=IDENTITY)
    assert r.status_code == 200


def test_cc_modules_untrusted_headers_is_401(monkeypatch):
    client = TestClient(_sibling_app(), client=UNTRUSTED)
    r = client.post("/cc/telegram/anything", headers=IDENTITY, json={})
    assert r.status_code == 401
