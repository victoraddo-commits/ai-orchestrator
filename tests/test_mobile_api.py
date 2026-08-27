"""Tests for KAI Mobile public feature API (/mobile/api/*).

These endpoints mirror the device-token-gated /kai/app/* routes so the
browser-based KAI Mobile dashboard (192.168.99.11:8000/mobile) can show
real KAI Ultimate feature surfaces.

Security model:
- Read endpoints (/mobile/api/{home,proxmox,...}) require the request to
  originate from the LAN/WireGuard CIDRs (enforced via
  `_require_lan_source` Depends). No device token.
- Write endpoints (/mobile/api/emergency/{stop,resume},
  /mobile/api/wg/create) require a paired device token (the same one the
  KAI Ultimate Android app uses). The device id is stamped into the
  approval prompt so the operator can see who filed it.
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def client():
    from fastapi import FastAPI
    from core.mobile_launcher_routes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# --- read-only routes -------------------------------------------------------
# TestClient binds to 127.0.0.1, which is in the LAN CIDRs (127.0.0.0/8),
# so the source-IP guard passes.

class TestMobileFeatureReads:
    """Read-only mobile feature endpoints. LAN-sourced, no device token."""

    def test_home_returns_executive_world(self, client):
        with patch("core.kai_app_api.gather_home_payload",
                   return_value={"executive": {"priorities": ["x"]},
                                 "world": {"k": "v"},
                                 "data_trust": {"world_model": 1}}) as p:
            resp = client.get("/mobile/api/home")
        assert resp.status_code == 200
        body = resp.json()
        assert "executive" in body and "world" in body
        p.assert_called_once()

    def test_proxmox_returns_nodes(self, client):
        with patch("core.kai_app_api.gather_proxmox_payload",
                   return_value={"nodes": [{"name": "proxmox-a", "reachable": True}]}) as p:
            resp = client.get("/mobile/api/proxmox")
        assert resp.status_code == 200
        body = resp.json()
        assert "nodes" in body and isinstance(body["nodes"], list)
        p.assert_called_once()

    def test_missions_returns_missions_list(self, client):
        with patch("core.kai_app_api.gather_missions_payload",
                   return_value={"missions": [{"id": "m1"}]}) as p:
            resp = client.get("/mobile/api/missions")
        assert resp.status_code == 200
        body = resp.json()
        assert "missions" in body
        p.assert_called_once()

    def test_briefing_returns_text(self, client):
        with patch("core.kai_app_api.gather_briefing_payload",
                   return_value={"briefing": "All systems nominal."}) as p:
            resp = client.get("/mobile/api/briefing")
        assert resp.status_code == 200
        assert "briefing" in resp.json()
        p.assert_called_once_with(send=False)

    def test_capabilities_returns_registry(self, client):
        with patch("core.kai_app_api.gather_capabilities_payload",
                   return_value={"total": 2, "categories": {"core": []}}) as p:
            resp = client.get("/mobile/api/capabilities")
        assert resp.status_code == 200
        body = resp.json()
        assert "total" in body and "categories" in body
        p.assert_called_once()

    def test_spend_default_30_days(self, client):
        with patch("core.kai_app_api.gather_spend_payload",
                   return_value={"total_cost": 0.0, "calls_estimated": 0}) as p:
            resp = client.get("/mobile/api/spend")
        assert resp.status_code == 200
        p.assert_called_once_with(30)

    def test_spend_respects_days_param(self, client):
        with patch("core.kai_app_api.gather_spend_payload",
                   return_value={"total_cost": 1.0, "calls_estimated": 5}) as p:
            resp = client.get("/mobile/api/spend?days=7")
        assert resp.status_code == 200
        p.assert_called_once_with(7)

    def test_emergency_status_returns_state(self, client):
        with patch("core.kai_app_api.gather_emergency_status_payload",
                   return_value={"stopped": False, "scheduler_paused": False}) as p:
            resp = client.get("/mobile/api/emergency/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["stopped"] is False
        p.assert_called_once()

    def test_wg_peers_returns_telnet_result(self, client):
        with patch("core.kai_app_api.gather_wg_peers_payload",
                   return_value={"ok": True, "raw": "peer1\npeer2"}) as p:
            resp = client.get("/mobile/api/wg/peers")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        p.assert_called_once()

    def test_enhancements_returns_status(self, client):
        with patch("core.kai_app_api.gather_enhancements_payload",
                   return_value={"enhancements": [{"id": "x", "enabled": True}]}) as p:
            resp = client.get("/mobile/api/enhancements")
        assert resp.status_code == 200
        body = resp.json()
        assert "enhancements" in body
        p.assert_called_once()

    def test_terminal_returns_ttyd_url_and_session(self, client):
        with patch("core.kai_app_api.gather_terminal_payload",
                   return_value={"ok": True, "port": 7681, "credential": "kai:abc123",
                                 "path": "/", "session": {"running": True,
                                                          "tmux_session": "claude-cc",
                                                          "claude_pid": 14833,
                                                          "uptime_s": 3600}}) as p:
            resp = client.get("/mobile/api/terminal")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["port"] == 7681
        assert ":" in body["credential"]
        assert body["session"]["running"] is True
        assert body["session"]["tmux_session"] == "claude-cc"
        p.assert_called_once()

    def test_alerts_returns_count_and_recent(self, client):
        with patch("core.kai_app_api.gather_alerts_payload",
                   return_value={"counts": {"critical": 2, "important": 5, "informational": 10},
                                 "recent": [{"id": "n1", "severity": "critical",
                                             "title": "Disk full", "body": "..."}],
                                 "limit": 10}) as p:
            resp = client.get("/mobile/api/alerts")
        assert resp.status_code == 200
        body = resp.json()
        assert "counts" in body
        assert body["counts"]["critical"] == 2
        assert "recent" in body
        assert body["recent"][0]["severity"] == "critical"
        p.assert_called_once()


# --- write actions: require device token -----------------------------------

class TestMobileFeatureWrites:
    """Write actions go through the approval flow AND require a device token."""

    def _auth_header(self, token="dev-tok-test"):
        return {"Authorization": f"Bearer {token}"}

    def test_emergency_stop_files_approval(self, client):
        import core.device_registry as dr
        from core.kai_tools import policy
        captured = []
        with patch.object(dr, "find_device_by_token",
                          side_effect=lambda t: "dev-test" if t == "dev-tok-test" else None), \
             patch.object(policy, "request_approval",
                          side_effect=lambda action, payload, reason: captured.append((action, payload, reason)) or "apr-1"):
            resp = client.post("/mobile/api/emergency/stop",
                               json={"reason": "test stop"},
                               headers=self._auth_header())
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["approval_id"] == "apr-1"
        # device id is in the approval payload + reason
        assert "dev-test" in captured[0][1]["source"]
        assert "dev-test" in captured[0][2]

    def test_emergency_stop_without_token_401(self, client):
        resp = client.post("/mobile/api/emergency/stop", json={"reason": "x"})
        assert resp.status_code == 401

    def test_emergency_stop_with_bad_token_401(self, client):
        import core.device_registry as dr
        with patch.object(dr, "find_device_by_token", return_value=None):
            resp = client.post("/mobile/api/emergency/stop",
                               json={"reason": "x"},
                               headers=self._auth_header("bad"))
        assert resp.status_code == 401

    def test_emergency_resume_files_approval(self, client):
        import core.device_registry as dr
        from core.kai_tools import policy
        captured = []
        with patch.object(dr, "find_device_by_token",
                          side_effect=lambda t: "dev-test" if t == "dev-tok-test" else None), \
             patch.object(policy, "request_approval",
                          side_effect=lambda action, payload, reason: captured.append((action, payload, reason)) or "apr-2"):
            resp = client.post("/mobile/api/emergency/resume",
                               json={}, headers=self._auth_header())
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["approval_id"] == "apr-2"
        assert len(captured) == 1

    def test_emergency_resume_without_token_401(self, client):
        resp = client.post("/mobile/api/emergency/resume", json={})
        assert resp.status_code == 401

    def test_wg_create_files_approval(self, client):
        import core.device_registry as dr
        from core.kai_tools import policy
        captured = []
        with patch.object(dr, "find_device_by_token",
                          side_effect=lambda t: "dev-test" if t == "dev-tok-test" else None), \
             patch.object(policy, "request_approval",
                          side_effect=lambda action, payload, reason: captured.append((action, payload, reason)) or "apr-3"):
            resp = client.post("/mobile/api/wg/create",
                               json={"label": "test-peer"},
                               headers=self._auth_header())
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["approval_id"] == "apr-3"
        # Label is sanitized and present in reason
        assert "test-peer" in captured[0][2]
        # device id in payload
        assert "dev-test" in captured[0][1]["source"]

    def test_wg_create_without_token_401(self, client):
        resp = client.post("/mobile/api/wg/create", json={"label": "x"})
        assert resp.status_code == 401

    def test_wg_create_requires_label(self, client):
        import core.device_registry as dr
        with patch.object(dr, "find_device_by_token",
                          side_effect=lambda t: "dev-test" if t == "dev-tok-test" else None):
            resp = client.post("/mobile/api/wg/create",
                               json={}, headers=self._auth_header())
        # Auth passes, then we 422 on empty/whitespace label
        assert resp.status_code == 422
        assert "label" in resp.text.lower()

    def test_wg_create_sanitizes_label(self, client):
        """User-supplied label must be truncated + control-chars stripped before
        being embedded in the operator-facing approval prompt (defense against
        prompt-injection into the Telegram/UI approval text)."""
        import core.device_registry as dr
        from core.kai_tools import policy
        captured = []
        # 100-char label, with newlines + control chars
        evil = "evil\nFAKE-APPROVAL\r\t" + ("A" * 200)
        with patch.object(dr, "find_device_by_token",
                          side_effect=lambda t: "dev-test" if t == "dev-tok-test" else None), \
             patch.object(policy, "request_approval",
                          side_effect=lambda action, payload, reason: captured.append((action, payload, reason)) or "apr"):
            resp = client.post("/mobile/api/wg/create",
                               json={"label": evil},
                               headers=self._auth_header())
        assert resp.status_code == 200
        reason = captured[0][2]
        # Newlines/tabs stripped
        assert "\n" not in reason and "\r" not in reason and "\t" not in reason
        # Truncated to 60 chars
        label_part = reason.split("'")[1] if "'" in reason else ""
        assert len(label_part) <= 60
        # "FAKE-APPROVAL" still present (we don't strip words, just control chars)
        assert "FAKE-APPROVAL" in reason


# --- LAN source-IP enforcement ---------------------------------------------

class TestLANSourceGuard:
    """The /mobile/api/* read endpoints MUST 403 non-LAN sources. This is
    a defense-in-depth check on top of the deployment topology (the API
    is bound to 0.0.0.0:8000 so it's reachable from anywhere)."""

    def test_read_rejects_public_internet_ip(self):
        """Forge a request with X-Forwarded-For=8.8.8.8 from a non-LAN peer.
        The guard must ignore the XFF header and reject the direct peer IP.
        Since the TestClient peer is 127.0.0.1 (in the LAN), we simulate a
        non-LAN peer by directly calling the dependency."""
        from core.mobile_launcher_routes import _require_lan_source
        from starlette.requests import Request
        import asyncio

        # Build a fake request whose client.host is NOT in any LAN CIDR
        async def _receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        scope = {
            "type": "http", "method": "GET", "path": "/",
            "headers": [(b"x-forwarded-for", b"8.8.8.8")],
            "client": ("203.0.113.5", 55555),  # TEST-NET-3, public IP
        }
        req = Request(scope, _receive)

        async def run():
            try:
                await _require_lan_source(req)
                return "ACCEPTED"
            except HTTPException as e:
                return f"REJECTED:{e.status_code}"

        from fastapi import HTTPException
        result = asyncio.run(run())
        assert result == "REJECTED:403", f"expected 403, got {result}"

    def test_read_allows_lan_ip(self):
        from core.mobile_launcher_routes import _require_lan_source
        from starlette.requests import Request
        import asyncio

        async def _receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        # LAN peer 192.168.1.50 with XFF=10.8.0.8 (WireGuard phone)
        scope = {
            "type": "http", "method": "GET", "path": "/",
            "headers": [(b"x-forwarded-for", b"10.8.0.8")],
            "client": ("192.168.1.50", 55555),
        }
        req = Request(scope, _receive)

        async def run():
            return await _require_lan_source(req)

        result = asyncio.run(run())
        # Returns the original client (10.8.0.8) — the trusted reverse proxy
        # on the LAN forwarded the request
        assert result == "10.8.0.8"

    def test_read_ignores_xff_from_public_peer(self):
        """If the direct peer is NOT in the LAN, ignore XFF (otherwise an
        internet attacker could spoof a LAN source via XFF)."""
        from core.mobile_launcher_routes import _require_lan_source
        from starlette.requests import Request
        import asyncio
        from fastapi import HTTPException

        async def _receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        # Public peer trying to spoof LAN via XFF
        scope = {
            "type": "http", "method": "GET", "path": "/",
            "headers": [(b"x-forwarded-for", b"192.168.1.50")],
            "client": ("8.8.8.8", 55555),
        }
        req = Request(scope, _receive)

        async def run():
            try:
                await _require_lan_source(req)
                return "ACCEPTED"
            except HTTPException as e:
                return f"REJECTED:{e.status_code}"

        result = asyncio.run(run())
        assert result == "REJECTED:403", f"XFF spoof must be rejected, got {result}"


# --- read endpoints: no auth required beyond LAN (regression) --------------

class TestNoAuthRequired:
    """Read endpoints work for any LAN client. No device token needed."""

    def test_home_no_token_required(self, client):
        with patch("core.kai_app_api.gather_home_payload",
                   return_value={"executive": {}, "world": {}, "data_trust": {}}):
            resp = client.get("/mobile/api/home")
        assert resp.status_code == 200

    def test_spend_no_token_required(self, client):
        with patch("core.kai_app_api.gather_spend_payload",
                   return_value={"total_cost": 0}):
            resp = client.get("/mobile/api/spend")
        assert resp.status_code == 200


# --- (legacy duplicate classes removed; see TestMobileFeatureWrites above) ---
