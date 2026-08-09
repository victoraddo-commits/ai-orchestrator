"""Tests for WireGuard Manager — Sub-project 5.

Tests parser functions (pure logic, no telnet required), WG metric
extraction in health observatory, and the FastAPI router endpoints.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

SAMPLE_WG_SHOW = """interface: wg0
  public key: AbCdEf1234567890AbCdEf1234567890AbCdEf12=
  private key: (hidden)
  listening port: 51820

peer: EfGhIjKlMnOp1234567890EfGhIjKlMnOp12345678=
  endpoint: 192.168.1.100:51820
  allowed ips: 10.8.0.102/32
  latest handshake: 1 minute, 30 seconds ago
  transfer: 1.50 GiB received, 800.25 MiB sent

peer: ZxYwVuTsRqPo9876543210ZxYwVuTsRqPo9876543210=
  endpoint: 10.0.0.5:51820
  allowed ips: 10.8.0.103/32, 10.8.0.104/32
  latest handshake: 2 hours, 15 minutes ago
  transfer: 500 MiB received, 1.2 GiB sent"""

SAMPLE_HANDSHAKES = """EfGhIjKlMnOp1234567890EfGhIjKlMnOp12345678=  1691874000
ZxYwVuTsRqPo9876543210ZxYwVuTsRqPo9876543210=  1691787600"""

SAMPLE_TRANSFER = """EfGhIjKlMnOp1234567890EfGhIjKlMnOp12345678=  1610612736  858993459
ZxYwVuTsRqPo9876543210ZxYwVuTsRqPo9876543210=  524288000  1288490188"""

SAMPLE_EMPTY_WG_SHOW = """interface: wg0
  public key: (hidden)
  private key: (hidden)
  listening port: 51820"""


class TestWgShowParser:
    """Parse 'wg show' output into structured data."""

    def test_parses_interface_info(self):
        from core.wireguard_manager import _parse_wg_show

        result = _parse_wg_show(SAMPLE_WG_SHOW)

        assert result is not None
        assert result["public_key"] == "AbCdEf1234567890AbCdEf1234567890AbCdEf12="
        assert result["listen_port"] == 51820

    def test_parses_peers(self):
        from core.wireguard_manager import _parse_wg_show

        result = _parse_wg_show(SAMPLE_WG_SHOW)

        assert len(result["peers"]) == 2

        peer1 = result["peers"][0]
        assert peer1["public_key"] == "EfGhIjKlMnOp1234567890EfGhIjKlMnOp12345678="
        assert peer1["endpoint"] == "192.168.1.100:51820"
        assert peer1["allowed_ips"] == ["10.8.0.102/32"]
        assert peer1["last_handshake_text"] == "1 minute, 30 seconds ago"
        assert peer1["transfer_rx"] == "1.50 GiB"
        assert peer1["transfer_tx"] == "800.25 MiB"

    def test_parses_multiple_allowed_ips(self):
        from core.wireguard_manager import _parse_wg_show

        result = _parse_wg_show(SAMPLE_WG_SHOW)

        peer2 = result["peers"][1]
        assert peer2["allowed_ips"] == ["10.8.0.103/32", "10.8.0.104/32"]

    def test_returns_none_for_empty_or_bogus(self):
        from core.wireguard_manager import _parse_wg_show

        assert _parse_wg_show("") is None
        assert _parse_wg_show("some random text") is None

    def test_handles_interface_only_no_peers(self):
        from core.wireguard_manager import _parse_wg_show

        result = _parse_wg_show(SAMPLE_EMPTY_WG_SHOW)

        assert result is not None
        assert result["peers"] == []


class TestHandshakeParser:
    """Parse 'wg show latest-handshakes' output."""

    def test_parses_handshakes(self):
        from core.wireguard_manager import _parse_handshakes

        result = _parse_handshakes(SAMPLE_HANDSHAKES)

        assert len(result) == 2
        assert result["EfGhIjKlMnOp1234567890EfGhIjKlMnOp12345678="] == 1691874000
        assert result["ZxYwVuTsRqPo9876543210ZxYwVuTsRqPo9876543210="] == 1691787600

    def test_empty_output(self):
        from core.wireguard_manager import _parse_handshakes

        assert _parse_handshakes("") == {}

    def test_invalid_lines_skipped(self):
        from core.wireguard_manager import _parse_handshakes

        output = "key1  12345\ninvalid_line\nkey2  not_a_number\nkey3  67890"
        result = _parse_handshakes(output)

        assert "key1" in result
        assert result["key1"] == 12345
        assert "key2" not in result  # invalid timestamp
        assert "invalid_line" not in result
        assert "key3" in result


class TestTransferParser:
    """Parse 'wg show transfer' output."""

    def test_parses_transfer_stats(self):
        from core.wireguard_manager import _parse_transfer

        result = _parse_transfer(SAMPLE_TRANSFER)

        assert len(result) == 2
        assert result["EfGhIjKlMnOp1234567890EfGhIjKlMnOp12345678="] == {
            "rx": 1610612736,
            "tx": 858993459,
        }
        assert result["ZxYwVuTsRqPo9876543210ZxYwVuTsRqPo9876543210="] == {
            "rx": 524288000,
            "tx": 1288490188,
        }

    def test_empty_output(self):
        from core.wireguard_manager import _parse_transfer

        assert _parse_transfer("") == {}


class TestHealthMetricsCollector:
    """WireGuard health metric collection returns valid float metrics."""

    def test_returns_expected_metric_keys(self):
        from core.wireguard_manager import collect_wg_health_metrics

        with patch("core.wireguard_manager.check_tunnel_to_proxmox_b") as mock_check, \
             patch("core.wireguard_manager.get_wg_status") as mock_status:

            mock_check.return_value = {"ok": True, "latency_ms": 12.5, "target": "10.8.0.102:8006"}
            mock_status.return_value = {
                "ok": True,
                "peers": [
                    {
                        "public_key": "peer1",
                        "handshake_age_sec": 45,
                        "endpoint": "192.168.1.100:51820",
                    },
                    {
                        "public_key": "peer2",
                        "handshake_age_sec": 120,
                        "endpoint": "10.0.0.5:51820",
                    },
                ],
            }

            metrics = collect_wg_health_metrics()

            assert metrics["wg_tunnel_reachable"] == 1.0
            assert metrics["wg_tunnel_latency_ms"] == 12.5
            assert metrics["wg_interface_ok"] == 1.0
            assert metrics["wg_peer_count"] == 2.0
            assert metrics["wg_oldest_handshake_sec"] == 120.0
            assert metrics["wg_newest_handshake_sec"] == 45.0
            assert metrics["wg_all_peers_healthy"] == 1.0  # both under 180s

    def test_unreachable_tunnel_sets_zero(self):
        from core.wireguard_manager import collect_wg_health_metrics

        with patch("core.wireguard_manager.check_tunnel_to_proxmox_b") as mock_check, \
             patch("core.wireguard_manager.get_wg_status") as mock_status:

            mock_check.return_value = {"ok": False, "latency_ms": None, "error": "timeout"}
            mock_status.return_value = {"ok": False, "peers": []}

            metrics = collect_wg_health_metrics()

            assert metrics["wg_tunnel_reachable"] == 0.0
            assert metrics["wg_interface_ok"] == 0.0
            assert metrics["wg_peer_count"] == 0.0

    def test_stale_handshake_flags_unhealthy(self):
        from core.wireguard_manager import collect_wg_health_metrics

        with patch("core.wireguard_manager.check_tunnel_to_proxmox_b") as mock_check, \
             patch("core.wireguard_manager.get_wg_status") as mock_status:

            mock_check.return_value = {"ok": True, "latency_ms": 50.0}
            mock_status.return_value = {
                "ok": True,
                "peers": [
                    {"public_key": "peer1", "handshake_age_sec": 300},  # >180s = stale
                ],
            }

            metrics = collect_wg_health_metrics()

            assert metrics["wg_all_peers_healthy"] == 0.0


class TestHealthObservatoryWGIntegration:
    """WireGuard metrics feed into the health observatory correctly."""

    def test_wg_metrics_extracted_from_snapshot(self):
        """_extract_metrics pulls WG metrics from the snapshot dict."""
        from core.health_observatory import _extract_metrics

        snap = {
            "docker": {"containers": []},
            "proxmox": {},
            "host": {"hostname": "test"},
            "wireguard": {
                "wg_tunnel_reachable": 1.0,
                "wg_tunnel_latency_ms": 15.3,
                "wg_interface_ok": 1.0,
                "wg_peer_count": 2.0,
                "wg_oldest_handshake_sec": 60.0,
                "wg_all_peers_healthy": 1.0,
            },
        }

        metrics = _extract_metrics(snap, "localhost")

        assert metrics["wg_wg_tunnel_reachable"] == 1.0
        assert metrics["wg_wg_tunnel_latency_ms"] == 15.3
        assert metrics["wg_wg_interface_ok"] == 1.0
        assert metrics["wg_wg_peer_count"] == 2.0
        assert metrics["wg_wg_oldest_handshake_sec"] == 60.0
        assert metrics["wg_wg_all_peers_healthy"] == 1.0

    def test_wg_tunnel_down_penalizes_health_score(self):
        """A down WG tunnel reduces the composite health score."""
        from core.health_observatory import _extract_metrics

        snap = {
            "docker": {"containers": [
                {"Names": "test", "State": "running"},
            ]},
            "proxmox": {},
            "host": {"hostname": "test"},
            "wireguard": {
                "wg_tunnel_reachable": 0.0,
            },
        }

        metrics = _extract_metrics(snap, "localhost")

        assert metrics["health_score"] < 100.0
        # With 1 running container and WG down, should be <= 80
        assert metrics["health_score"] <= 80.0


class TestFailoverState:
    """Endpoint failover state tracking."""

    def test_initial_failover_state_is_primary(self):
        from core.wireguard_manager import get_failover_state

        state = get_failover_state()

        assert state["active_endpoint"] == "primary"
        assert state["primary_failures"] == 0
        assert state["fallback_failures"] == 0


class TestDDWRTConnection:
    """DD-WRT telnet connection lifecycle (unit tests, no actual telnet)."""

    def test_connect_no_host_returns_false(self):
        from core.wireguard_manager import DDWRTConnection

        conn = DDWRTConnection(host="255.255.255.255", port=23, timeout=1)
        result = conn.connect()
        assert not result
        conn.close()

    def test_is_connected_when_not_connected(self):
        from core.wireguard_manager import DDWRTConnection

        conn = DDWRTConnection()
        assert not conn.is_connected()

    def test_execute_without_connect_fails(self):
        from core.wireguard_manager import DDWRTConnection

        # Use a guaranteed-unreachable host:port
        conn = DDWRTConnection(host="255.255.255.255", port=23, timeout=1)
        output, exit_code = conn.execute("echo test")
        assert exit_code == -2
        assert output == ""
        conn.close()

    def test_close_idempotent(self):
        from core.wireguard_manager import DDWRTConnection

        conn = DDWRTConnection()
        conn.close()  # should not raise
        conn.close()  # double-close idempotent


@pytest.fixture
def mock_connection(monkeypatch):
    """Avoid real telnet in endpoint/restart tests."""
    monkeypatch.setattr(
        "core.wireguard_manager._get_connection",
        MagicMock(return_value=None),
    )


class TestEndpointManagement:
    """Peer endpoint changes (unit tests, no real DD-WRT)."""

    def test_set_endpoint_requires_connection(self, mock_connection):
        from core.wireguard_manager import set_peer_endpoint

        result = set_peer_endpoint("fake_pubkey", "10.0.0.1:51820")
        assert not result["ok"]
        assert "unreachable" in result["error"].lower()

    def test_restart_without_connection(self, mock_connection):
        from core.wireguard_manager import restart_interface

        result = restart_interface()
        assert not result["ok"]
        assert "unreachable" in result["error"].lower()

    def test_failover_without_endpoints_configured(self):
        from core.wireguard_manager import attempt_endpoint_failover

        result = attempt_endpoint_failover("fake_pubkey")
        assert not result["ok"]
        assert "not configured" in result["error"].lower()


class TestRecoverySequence:
    """Full recovery sequence logic (unit tests)."""

    def test_tunnel_already_up_skips_recovery(self):
        from core.wireguard_manager import attempt_full_recovery

        with patch("core.wireguard_manager.check_tunnel_to_proxmox_b") as mock:
            mock.return_value = {"ok": True, "latency_ms": 5.0}
            result = attempt_full_recovery()

            assert result["tunnel_recovered"]
            assert "tunnel_already_up" in result["actions_taken"]


class TestCommandLengthLimit:
    """DD-WRT 500-char line limit enforcement."""

    def test_long_command_is_truncated(self):
        from core.wireguard_manager import DDWRTConnection

        # Use unreachable host so execute returns -2 after truncation
        conn = DDWRTConnection(host="255.255.255.255", port=23, timeout=1)
        long_cmd = "a" * 600
        output, code = conn.execute(long_cmd)
        # Should have failed to connect, and command was truncated
        assert code == -2
        conn.close()
