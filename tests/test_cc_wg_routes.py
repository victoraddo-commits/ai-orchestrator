"""Tests for the Command Center WireGuard device-management proxy routes.

Mounted on a bare FastAPI app so ``core.api`` (and its scheduler) is never
imported; the CT102 controller is monkeypatched, so no SSH is attempted.
"""
from __future__ import annotations

import base64
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import cc_extra_routes as cc
from core.wg_peer_service import WgServiceError

IDENT = {"X-Kai-User": "owner@kai", "X-Kai-User-Id": "owner"}
PUBKEY = "A" * 43 + "="
PUBKEY_SLASH = "Ab/" + "C" * 40 + "="  # standard base64 can contain '/'

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nrest").decode()


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(cc.cc_extra_router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _no_token(monkeypatch):
    monkeypatch.delenv("WG_CTL_TOKEN", raising=False)


# ── auth gates ────────────────────────────────────────────────────────────

def test_list_requires_operator(client):
    assert client.get("/api/wg/peers").status_code == 401


def test_add_requires_operator(client):
    assert client.post("/api/wg/peers", json={"name": "x"}).status_code == 401


def test_pause_resume_delete_require_operator(client):
    assert client.post(f"/api/wg/peers/{PUBKEY}/pause").status_code == 401
    assert client.post(f"/api/wg/peers/{PUBKEY}/resume").status_code == 401
    assert client.delete(f"/api/wg/peers/{PUBKEY}").status_code == 401


def test_config_and_qr_require_operator(client):
    assert client.get(f"/api/wg/peers/{PUBKEY}/config").status_code == 401
    assert client.get(f"/api/wg/peers/{PUBKEY}/qr").status_code == 401


def test_service_token_accepted(client, monkeypatch):
    monkeypatch.setenv("WG_CTL_TOKEN", "s3cret-token")
    monkeypatch.setattr("core.wg_peer_service.list_peers",
                        lambda: {"count": 1, "peers": []})
    ok = client.get("/api/wg/peers", headers={"X-Kai-WG-Token": "s3cret-token"})
    assert ok.status_code == 200
    bad = client.get("/api/wg/peers", headers={"X-Kai-WG-Token": "wrong"})
    assert bad.status_code == 401


# ── delegation ────────────────────────────────────────────────────────────

def test_list_delegates(client, monkeypatch):
    monkeypatch.setattr("core.wg_peer_service.list_peers",
                        lambda: {"count": 4, "peers": [{"pubkey": PUBKEY}],
                                 "interface_public_key": "S="})
    body = client.get("/api/wg/peers", headers=IDENT).json()
    assert body["count"] == 4 and body["peers"][0]["pubkey"] == PUBKEY


def test_add_delegates_body(client, monkeypatch):
    seen = {}

    def fake_add(name, **opts):
        seen["name"] = name
        seen.update(opts)
        return {"pubkey": PUBKEY, "ip": "10.6.0.8", "config": "x",
                "qr_png_base64": PNG}

    monkeypatch.setattr("core.wg_peer_service.add_peer", fake_add)
    r = client.post("/api/wg/peers", headers=IDENT,
                    json={"name": "Phone", "dns": "1.1.1.1"})
    assert r.status_code == 200
    assert seen == {"name": "Phone", "dns": "1.1.1.1"}
    assert r.json()["ip"] == "10.6.0.8"


def test_pause_resume_delete_delegate(client, monkeypatch):
    calls = []
    for op in ("pause_peer", "resume_peer", "delete_peer"):
        monkeypatch.setattr(f"core.wg_peer_service.{op}",
                            lambda pubkey, _op=op: calls.append((_op, pubkey)) or {"ok": True})
    assert client.post(f"/api/wg/peers/{PUBKEY}/pause", headers=IDENT).status_code == 200
    assert client.post(f"/api/wg/peers/{PUBKEY}/resume", headers=IDENT).status_code == 200
    assert client.delete(f"/api/wg/peers/{PUBKEY}", headers=IDENT).status_code == 200
    assert calls == [("pause_peer", PUBKEY), ("resume_peer", PUBKEY),
                     ("delete_peer", PUBKEY)]


def test_config_delegates_type(client, monkeypatch):
    seen = {}
    monkeypatch.setattr("core.wg_peer_service.peer_config",
                        lambda pk, fmt: seen.update(pk=pk, fmt=fmt) or
                        {"config": "cfg", "type": fmt, "name": "n", "address": "a"})
    r = client.get(f"/api/wg/peers/{PUBKEY}/config",
                   headers=IDENT, params={"type": "openwrt"})
    assert r.status_code == 200 and r.json()["config"] == "cfg"
    assert seen == {"pk": PUBKEY, "fmt": "openwrt"}


def test_qr_json_and_raw_png(client, monkeypatch):
    monkeypatch.setattr("core.wg_peer_service.peer_qr",
                        lambda pk, fmt: {"qr_png_base64": PNG, "type": fmt, "name": "n"})
    j = client.get(f"/api/wg/peers/{PUBKEY}/qr", headers=IDENT).json()
    assert j["qr_png_base64"] == PNG
    raw = client.get(f"/api/wg/peers/{PUBKEY}/qr",
                     headers=IDENT, params={"raw": 1})
    assert raw.status_code == 200
    assert raw.headers["content-type"] == "image/png"
    assert raw.content[:4] == b"\x89PNG"


def test_pubkey_with_slash_routes_correctly(client, monkeypatch):
    seen = {}
    monkeypatch.setattr("core.wg_peer_service.peer_config",
                        lambda pk, fmt: seen.update(pk=pk) or {"config": "c"})
    path = f"/api/wg/peers/{cc.encode_pubkey_param(PUBKEY_SLASH)}/config"
    r = client.get(path, headers=IDENT)
    assert r.status_code == 200
    assert seen["pk"] == PUBKEY_SLASH


def test_raw_pubkey_path_still_works(client, monkeypatch):
    seen = {}
    monkeypatch.setattr("core.wg_peer_service.peer_config",
                        lambda pk, fmt: seen.update(pk=pk) or {"config": "c"})
    r = client.get(f"/api/wg/peers/{PUBKEY}/config", headers=IDENT)
    assert r.status_code == 200 and seen["pk"] == PUBKEY


# ── error mapping ─────────────────────────────────────────────────────────

def test_agent_error_maps_to_status(client, monkeypatch):
    def boom(name, **opts):
        raise WgServiceError("no free address left in pool", status=400)
    monkeypatch.setattr("core.wg_peer_service.add_peer", boom)
    r = client.post("/api/wg/peers", headers=IDENT, json={"name": "x"})
    assert r.status_code == 400
    assert "no free address" in r.json()["error"]


def test_upstream_error_maps_to_502(client, monkeypatch):
    def boom():
        raise WgServiceError("agent unreachable", status=502)
    monkeypatch.setattr("core.wg_peer_service.list_peers", boom)
    assert client.get("/api/wg/peers", headers=IDENT).status_code == 502
