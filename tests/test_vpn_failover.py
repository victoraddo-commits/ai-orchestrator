"""Tests for VPN failover module (TK-176d6efe).

Covers: tunnel health checks, WG state detection, recovery event generation,
and Proxmox monitor fallback/retry logic.
"""

import pytest
from unittest.mock import patch, MagicMock


class TestTunnelHealth:
    """VPN tunnel health evaluation."""

    def test_health_reports_ok_when_proxmox_reachable(self, monkeypatch):
        from core.vpn_failover import check_tunnel_health

        monkeypatch.setattr(
            "core.vpn_failover._proxmox_b_is_reachable",
            lambda: True,
        )
        monkeypatch.setattr(
            "core.vpn_failover._wg_interface_exists",
            lambda: False,
        )

        health = check_tunnel_health()
        assert health["ok"] is True
        assert health["proxmox_reachable"] is True
        assert health["recovery_needed"] is False

    def test_health_detects_unreachable_proxmox(self, monkeypatch):
        from core.vpn_failover import check_tunnel_health

        monkeypatch.setattr(
            "core.vpn_failover._proxmox_b_is_reachable",
            lambda: False,
        )
        monkeypatch.setattr(
            "core.vpn_failover._wg_interface_exists",
            lambda: False,
        )

        health = check_tunnel_health()
        assert health["ok"] is False
        assert health["proxmox_reachable"] is False
        assert health["recovery_needed"] is False  # no local WG to recover

    def test_health_recovery_needed_when_wg_down_but_configured(self, monkeypatch):
        from core.vpn_failover import check_tunnel_health

        monkeypatch.setattr(
            "core.vpn_failover._proxmox_b_is_reachable",
            lambda: False,
        )
        monkeypatch.setattr(
            "core.vpn_failover._wg_interface_exists",
            lambda: True,
        )
        monkeypatch.setattr(
            "core.vpn_failover._wg_is_up",
            lambda: False,
        )

        health = check_tunnel_health()
        assert health["ok"] is False
        assert health["recovery_needed"] is True
        assert health["interface"] is not None

    def test_health_has_timestamp(self):
        from core.vpn_failover import check_tunnel_health

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(
            "core.vpn_failover._proxmox_b_is_reachable",
            lambda: True,
        )
        monkeypatch.setattr(
            "core.vpn_failover._wg_interface_exists",
            lambda: False,
        )

        health = check_tunnel_health()
        assert "checked_at" in health
        monkeypatch.undo()


class TestWGDetection:
    """WireGuard interface detection."""

    def test_wg_interface_exists_returns_true(self, monkeypatch):
        from core.vpn_failover import _wg_interface_exists

        def fake_run(args, **kwargs):
            m = MagicMock()
            m.returncode = 0
            return m

        monkeypatch.setattr("subprocess.run", fake_run)
        assert _wg_interface_exists() is True

    def test_wg_interface_exists_returns_false(self, monkeypatch):
        from core.vpn_failover import _wg_interface_exists

        def fake_run(args, **kwargs):
            m = MagicMock()
            m.returncode = 1
            return m

        monkeypatch.setattr("subprocess.run", fake_run)
        assert _wg_interface_exists() is False

    def test_wg_is_up_with_recent_handshake(self, monkeypatch):
        from core.vpn_failover import _wg_is_up
        import time

        now = int(time.time())
        # Handshake 60 seconds ago — still fresh
        output = f"peer1\t{now - 60}"

        def fake_run(args, **kwargs):
            m = MagicMock()
            m.returncode = 0
            m.stdout = output
            return m

        monkeypatch.setattr("subprocess.run", fake_run)
        assert _wg_is_up() is True

    def test_wg_is_up_with_stale_handshake(self, monkeypatch):
        from core.vpn_failover import _wg_is_up
        import time

        now = int(time.time())
        # Handshake 10 minutes ago — stale
        output = f"peer1\t{now - 600}"

        def fake_run(args, **kwargs):
            m = MagicMock()
            m.returncode = 0
            m.stdout = output
            return m

        monkeypatch.setattr("subprocess.run", fake_run)
        assert _wg_is_up() is False

    def test_wg_is_up_with_no_output(self, monkeypatch):
        from core.vpn_failover import _wg_is_up

        def fake_run(args, **kwargs):
            m = MagicMock()
            m.returncode = 1
            m.stdout = ""
            return m

        monkeypatch.setattr("subprocess.run", fake_run)
        assert _wg_is_up() is False


class TestRecovery:
    """VPN recovery attempt logic."""

    def test_no_recovery_when_proxmox_reachable(self, monkeypatch):
        from core.vpn_failover import attempt_recovery

        monkeypatch.setattr(
            "core.vpn_failover._proxmox_b_is_reachable",
            lambda: True,
        )

        events = attempt_recovery()
        assert len(events) == 0

    def test_alerts_when_no_local_wg_and_unreachable(self, monkeypatch):
        from core.vpn_failover import attempt_recovery

        monkeypatch.setattr(
            "core.vpn_failover._proxmox_b_is_reachable",
            lambda: False,
        )
        monkeypatch.setattr(
            "core.vpn_failover._wg_interface_exists",
            lambda: False,
        )

        events = attempt_recovery()
        assert len(events) == 1
        assert events[0]["type"] == "vpn_down"
        assert events[0]["severity"] == "warning"

    def test_attempts_recovery_when_wg_configured(self, monkeypatch):
        from core.vpn_failover import attempt_recovery

        monkeypatch.setattr(
            "core.vpn_failover._proxmox_b_is_reachable",
            lambda: False,
        )
        monkeypatch.setattr(
            "core.vpn_failover._wg_interface_exists",
            lambda: True,
        )
        monkeypatch.setattr(
            "core.vpn_failover._wg_is_up",
            lambda: False,
        )
        # Mock wg-quick restart to succeed
        monkeypatch.setattr(
            "core.vpn_failover._restart_wg_interface",
            lambda: True,
        )

        events = attempt_recovery()
        assert len(events) == 2  # restart event + recovered event
        assert events[0]["type"] == "wg_restart"
        assert events[0]["success"] is True
        assert events[1]["type"] == "vpn_recovered"

    def test_recovery_failure_escalates_to_critical(self, monkeypatch):
        from core.vpn_failover import attempt_recovery, MAX_RECOVERY_ATTEMPTS

        monkeypatch.setattr(
            "core.vpn_failover._proxmox_b_is_reachable",
            lambda: False,
        )
        monkeypatch.setattr(
            "core.vpn_failover._wg_interface_exists",
            lambda: True,
        )
        monkeypatch.setattr(
            "core.vpn_failover._wg_is_up",
            lambda: False,
        )
        monkeypatch.setattr(
            "core.vpn_failover._restart_wg_interface",
            lambda: False,
        )
        # Don't sleep during tests
        monkeypatch.setattr("time.sleep", lambda s: None)

        events = attempt_recovery()
        # MAX_RECOVERY_ATTEMPTS restart events + 1 final failure event
        assert len(events) == MAX_RECOVERY_ATTEMPTS + 1
        final = events[-1]
        assert final["type"] == "vpn_recovery_failed"
        assert final["severity"] == "critical"


class TestProxmoxMonitorFailover:
    """TK-176d6efe: retry + fallback in _api_get."""

    def test_tries_fallback_when_primary_unreachable(self, monkeypatch):
        from core import proxmox_monitor as pm

        attempts = []

        def fake_request(host, headers, path, timeout=15):
            attempts.append(host)
            if host == "10.8.0.102":
                return None  # primary fails
            return {"status": "ok"}  # fallback succeeds

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
            return None

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
            return {"data": "ok"}

        monkeypatch.setattr(pm, "_do_request", fake_request)
        monkeypatch.setattr(pm, "_MAX_RETRIES", 5)

        node = {"name": "pve", "host": "192.168.99.2", "token": "root@pam!kai"}

        result = pm._api_get(node, "nodes")
        assert result == {"data": "ok"}
        assert len(attempts) == 1

    def test_vpn_status_cache_updated(self, monkeypatch):
        from core import proxmox_monitor as pm

        monkeypatch.setattr(pm, "_do_request", lambda h, hdrs, p, timeout=15: {"ok": True})
        monkeypatch.setattr(pm, "_MAX_RETRIES", 1)

        node = {"name": "pve", "host": "192.168.99.2", "token": "root@pam!kai"}
        pm._api_get(node, "nodes")

        status = pm.get_vpn_status("pve")
        assert "pve" in status
        assert status["pve"]["reachable"] is True

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


class TestConfigTemplate:
    """WireGuard config template generation."""

    def test_template_contains_expected_placeholders(self):
        from core.vpn_failover import generate_config_template

        tmpl = generate_config_template("wg-test", "10.8.0.3/32", "10.8.0.102:51820")
        assert "wg-test" in tmpl
        assert "10.8.0.3/32" in tmpl
        assert "10.8.0.102:51820" in tmpl
        assert "PrivateKey" in tmpl
        assert "PublicKey" in tmpl
        assert "PersistentKeepalive = 25" in tmpl
