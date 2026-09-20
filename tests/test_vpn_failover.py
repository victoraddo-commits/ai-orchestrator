"""Tests for VPN failover module (TK-176d6efe).

The module was rewritten from a WireGuard-based design to a direct LAN TCP
probe (commit 046c994) but its tests still referenced the removed WG API.
This suite covers the real LAN contract plus the 2026-09-20 hardening:
correct PVE-B address (192.168.1.110, not the dead .109), a
DISABLE_VPN_MONITORING short-circuit, and a bounded/fast recovery so a
scheduler cycle can never stall.
"""

import time

import pytest

from core import vpn_failover as vf


class TestConfig:
    """Probe configuration."""

    def test_default_probe_host_is_proxmox_b(self, monkeypatch):
        monkeypatch.delenv("VPN_FAILOVER_PROBE_HOST", raising=False)
        assert vf.DEFAULT_PROBE_HOST == "192.168.1.110"
        assert vf._probe_host() == "192.168.1.110"

    def test_probe_host_env_overrides_default(self, monkeypatch):
        monkeypatch.setenv("VPN_FAILOVER_PROBE_HOST", "10.9.9.9")
        assert vf._probe_host() == "10.9.9.9"

    def test_probe_is_fast_and_bounded(self):
        # A dead host must not tie up the scheduler for tens of seconds.
        assert vf.PROBE_TIMEOUT <= 3
        assert vf.MAX_RECOVERY_ATTEMPTS <= 3


class TestTunnelHealth:
    """Health evaluation."""

    def test_health_reports_reachable(self, monkeypatch):
        monkeypatch.setattr(vf, "_proxmox_b_is_reachable", lambda **kw: True)
        health = vf.check_tunnel_health()
        assert health["ok"] is True
        assert health["reachable"] is True
        assert health["host"] == vf._probe_host()
        assert "checked_at" in health

    def test_health_reports_unreachable(self, monkeypatch):
        monkeypatch.setattr(vf, "_proxmox_b_is_reachable", lambda **kw: False)
        health = vf.check_tunnel_health()
        assert health["ok"] is False
        assert health["reachable"] is False


class TestRecovery:
    """Recovery is non-blocking when disabled and bounded otherwise."""

    def test_disabled_monitoring_does_no_probes(self, monkeypatch):
        monkeypatch.setenv("DISABLE_VPN_MONITORING", "true")

        def boom(**kwargs):
            raise AssertionError("probe must not run when monitoring is disabled")

        monkeypatch.setattr(vf, "_proxmox_b_is_reachable", boom)
        assert vf.attempt_recovery() == []

    def test_disabled_monitoring_returns_immediately(self, monkeypatch):
        monkeypatch.setenv("DISABLE_VPN_MONITORING", "true")
        start = time.monotonic()
        assert vf.attempt_recovery() == []
        assert time.monotonic() - start < 0.5

    def test_reachable_host_needs_no_recovery(self, monkeypatch):
        monkeypatch.delenv("DISABLE_VPN_MONITORING", raising=False)
        monkeypatch.setattr(vf, "_proxmox_b_is_reachable", lambda **kw: True)
        assert vf.attempt_recovery() == []

    def test_unreachable_host_is_bounded_and_fast(self, monkeypatch):
        monkeypatch.delenv("DISABLE_VPN_MONITORING", raising=False)
        monkeypatch.setattr(vf, "RETRY_DELAY", 0)
        calls = []

        def probe(**kwargs):
            calls.append(kwargs)
            return False

        monkeypatch.setattr(vf, "_proxmox_b_is_reachable", probe)

        start = time.monotonic()
        events = vf.attempt_recovery()
        elapsed = time.monotonic() - start

        # one health probe + MAX_RECOVERY_ATTEMPTS retries, never more
        assert len(calls) == vf.MAX_RECOVERY_ATTEMPTS + 1
        assert elapsed < 1.0
        assert events and events[-1]["type"] == "vpn_down"

    def test_recovers_on_later_attempt(self, monkeypatch):
        monkeypatch.delenv("DISABLE_VPN_MONITORING", raising=False)
        monkeypatch.setattr(vf, "RETRY_DELAY", 0)
        results = iter([False, False, True])  # health, attempt 1, attempt 2
        monkeypatch.setattr(vf, "_proxmox_b_is_reachable", lambda **kw: next(results))

        events = vf.attempt_recovery()
        assert events[-1]["type"] == "vpn_recovered"
        assert events[-1]["attempt"] == 2


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
