"""Tests for the VPN failover module (TK-176d6efe — simplified LAN probe).

The module was rewritten to a direct Proxmox B LAN reachability probe (no
WireGuard).  These tests cover:
- tunnel health check
- recovery attempt event generation
- repeated "still down" alert cooldown (anti-spam)
"""

import time

import pytest


@pytest.fixture(autouse=True)
def reset_cooldown():
    """Reset the module-level down-alert cooldown before each test."""
    import core.vpn_failover as vf

    vf._last_down_alert_at = 0.0
    yield
    vf._last_down_alert_at = 0.0


class TestTunnelHealth:
    """Proxmox B LAN reachability evaluation."""

    def test_health_reports_ok_when_reachable(self, monkeypatch):
        from core.vpn_failover import check_tunnel_health

        monkeypatch.setattr("core.vpn_failover._proxmox_b_is_reachable", lambda: True)

        health = check_tunnel_health()
        assert health["ok"] is True
        assert health["reachable"] is True
        assert health["host"] == "localhost"
        assert health["port"] == 8007

    def test_health_reports_down_when_unreachable(self, monkeypatch):
        from core.vpn_failover import check_tunnel_health

        monkeypatch.setattr("core.vpn_failover._proxmox_b_is_reachable", lambda: False)

        health = check_tunnel_health()
        assert health["ok"] is False
        assert health["reachable"] is False

    def test_health_has_timestamp(self, monkeypatch):
        from core.vpn_failover import check_tunnel_health

        monkeypatch.setattr("core.vpn_failover._proxmox_b_is_reachable", lambda: True)

        health = check_tunnel_health()
        assert "checked_at" in health


class TestRecovery:
    """VPN recovery attempt logic."""

    def test_no_recovery_when_reachable(self, monkeypatch):
        from core.vpn_failover import attempt_recovery

        monkeypatch.setattr("core.vpn_failover._proxmox_b_is_reachable", lambda: True)
        assert attempt_recovery() == []

    def test_alerts_when_unreachable(self, monkeypatch):
        from core.vpn_failover import attempt_recovery

        monkeypatch.setattr("core.vpn_failover._proxmox_b_is_reachable", lambda: False)
        monkeypatch.setattr("time.sleep", lambda s: None)

        events = attempt_recovery()
        assert len(events) == 1
        assert events[0]["type"] == "vpn_down"
        assert events[0]["severity"] == "critical"

    def test_recovers_on_retry(self, monkeypatch):
        from core.vpn_failover import attempt_recovery

        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            return calls["n"] > 2  # down twice (probe + attempt 1), then up

        monkeypatch.setattr("core.vpn_failover._proxmox_b_is_reachable", flaky)
        monkeypatch.setattr("time.sleep", lambda s: None)

        events = attempt_recovery()
        assert len(events) == 1
        assert events[0]["type"] == "vpn_recovered"
        assert events[0]["attempt"] == 2

    def test_cooldown_suppresses_repeat_alert(self, monkeypatch):
        from core.vpn_failover import attempt_recovery

        monkeypatch.setattr("core.vpn_failover._proxmox_b_is_reachable", lambda: False)
        monkeypatch.setattr("time.sleep", lambda s: None)
        monkeypatch.setattr("core.vpn_failover.ALERT_COOLDOWN", 3600)

        # First call: down, emits a critical and records the alert time.
        first = attempt_recovery()
        assert len(first) == 1

        # Second call within the cooldown window: suppressed entirely.
        second = attempt_recovery()
        assert second == []

    def test_cooldown_resets_on_recovery(self, monkeypatch):
        import core.vpn_failover as vf
        from core.vpn_failover import attempt_recovery

        # Simulate a down alert emitted 10s ago.
        vf._last_down_alert_at = time.time() - 10
        monkeypatch.setattr("core.vpn_failover.ALERT_COOLDOWN", 3600)
        monkeypatch.setattr("core.vpn_failover._proxmox_b_is_reachable", lambda: True)

        events = attempt_recovery()
        assert events == []
        # Cooldown cleared once Proxmox B is reachable again.
        assert vf._last_down_alert_at == 0.0


class TestProxmoxMonitorFailover:
    """TK-176d6efe: retry + fallback in _api_get."""

    def test_tries_fallback_when_primary_unreachable(self, monkeypatch):
        from core import proxmox_monitor as pm

        attempts = []

        def fake_request(host, headers, path, timeout=15):
            attempts.append(host)
            if host == "10.8.0.102":
                return None, "connection"  # primary fails
            return {"status": "ok"}, None  # fallback succeeds

        monkeypatch.setattr(pm, "_do_request", fake_request)
        monkeypatch.setattr(pm, "_MAX_RETRIES", 1)

        node = {
            "name": "pve-b",
            "host": "10.8.0.102",
            "fallback_host": "192.168.99.200",
            "token_id": "kai@pve!kai",
            "token_secret": "secret",
        }

        result = pm._api_get(node, "nodes")
        assert result == {"status": "ok"}
        assert "192.168.99.200" in attempts

    def test_retries_before_fallback(self, monkeypatch):
        from core import proxmox_monitor as pm

        attempts = []

        def fake_request(host, headers, path, timeout=15):
            attempts.append(host)
            return None, "connection"

        monkeypatch.setattr(pm, "_do_request", fake_request)
        monkeypatch.setattr(pm, "_MAX_RETRIES", 2)
        monkeypatch.setattr(pm, "_RETRY_BASE_DELAY", 0.0)
        monkeypatch.setattr("time.sleep", lambda s: None)

        node = {
            "name": "pve-b",
            "host": "10.8.0.102",
            "fallback_host": "192.168.99.200",
            "token_id": "kai@pve!kai",
            "token_secret": "secret",
        }

        result = pm._api_get(node, "nodes")
        assert result is None
        # 2 retries primary + 2 retries fallback = 4 total
        assert len(attempts) == 4
        assert attempts[:2] == ["10.8.0.102", "10.8.0.102"]
        assert attempts[2:] == ["192.168.99.200", "192.168.99.200"]

    def test_success_on_first_attempt_skips_retries(self, monkeypatch):
        from core import proxmox_monitor as pm

        attempts = []

        def fake_request(host, headers, path, timeout=15):
            attempts.append(host)
            return {"data": "ok"}, None

        monkeypatch.setattr(pm, "_do_request", fake_request)
        monkeypatch.setattr(pm, "_MAX_RETRIES", 5)

        node = {"name": "pve", "host": "192.168.99.2", "token": "root@pam!kai"}

        result = pm._api_get(node, "nodes")
        assert result == {"data": "ok"}
        assert len(attempts) == 1

    def test_vpn_status_cache_updated(self, monkeypatch):
        from core import proxmox_monitor as pm

        monkeypatch.setattr(pm, "_do_request", lambda h, hdrs, p, timeout=15: ({"ok": True}, None))
        monkeypatch.setattr(pm, "_MAX_RETRIES", 1)

        node = {"name": "pve", "host": "192.168.99.2", "token": "root@pam!kai"}
        pm._api_get(node, "nodes")

        status = pm.get_vpn_status("pve")
        assert "pve" in status
        assert status["pve"]["reachable"] is True

    def test_do_request_does_not_double_append_port(self, monkeypatch):
        """A tunnel host already carrying a port must not get :8006 appended."""
        from unittest.mock import Mock
        from core import proxmox_monitor as pm

        captured = {}

        def fake_get(url, **kwargs):
            captured["url"] = url
            resp = Mock()
            resp.status_code = 200
            resp.json.return_value = {"data": {"ok": True}}
            return resp

        monkeypatch.setattr(pm.requests, "get", fake_get)
        pm._do_request("localhost:8008", {}, "nodes")
        assert captured["url"] == "https://localhost:8008/api2/json/nodes"

    def test_do_request_appends_port_for_bare_host(self, monkeypatch):
        """A bare-IP host still gets :8006 appended."""
        from unittest.mock import Mock
        from core import proxmox_monitor as pm

        captured = {}

        def fake_get(url, **kwargs):
            captured["url"] = url
            resp = Mock()
            resp.status_code = 200
            resp.json.return_value = {"data": {"ok": True}}
            return resp

        monkeypatch.setattr(pm.requests, "get", fake_get)
        pm._do_request("192.168.99.2", {}, "nodes")
        assert captured["url"] == "https://192.168.99.2:8006/api2/json/nodes"

    def test_collect_node_health_includes_fallback_host(self):
        node = {
            "name": "pve-b",
            "host": "10.8.0.102",
            "fallback_host": "192.168.99.200",
            "token_id": "kai@pve!kai",
            "token_secret": "secret",
        }

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(
            "core.proxmox_monitor._api_get",
            lambda n, p: None,
        )

        from core.proxmox_monitor import collect_node_health
        h = collect_node_health(node)
        assert h["fallback_host"] == "192.168.99.200"
        assert h["reachable"] is False
        monkeypatch.undo()
