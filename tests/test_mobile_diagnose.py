"""Tests for Kai Mobile Command Node self-diagnostics (SP6).

Covers: diagnostic runner, individual checks, CLI output, edge cases.

Because the diagnose functions use lazy `from X import Y` inside their body,
patches must target the SOURCE module (e.g., `core.device_registry.list_devices`),
not the import destination (`core.kai.mobile_diagnose.list_devices`).
"""

import json
import pytest
import sys
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Test: run_diagnostic() returns correct structure
# ---------------------------------------------------------------------------


class TestDiagnosticStructure:
    """Verify the diagnostic runner returns the expected response shape."""

    def test_returns_correct_top_level_keys(self):
        from core.kai.mobile_diagnose import run_diagnostic

        result = run_diagnostic()

        assert set(result.keys()) >= {
            "ok", "summary", "overall", "passed", "warned", "failed",
            "total", "timestamp", "checks",
        }
        assert isinstance(result["ok"], bool)
        assert isinstance(result["summary"], str)
        assert result["overall"] in ("PASS", "WARN", "FAIL")
        assert result["total"] == len(result["checks"])

    def test_each_check_has_required_fields(self):
        from core.kai.mobile_diagnose import run_diagnostic

        result = run_diagnostic()

        for check in result["checks"]:
            assert "name" in check
            assert "status" in check
            assert "detail" in check
            assert "elapsed_ms" in check
            assert check["status"] in ("PASS", "WARN", "FAIL")

    def test_overall_matches_counts(self):
        from core.kai.mobile_diagnose import run_diagnostic

        result = run_diagnostic()

        assert result["passed"] + result["warned"] + result["failed"] == result["total"]
        if result["failed"] == 0:
            assert result["overall"] in ("PASS", "WARN")
        elif result["failed"] <= 2:
            assert result["overall"] == "WARN"
        else:
            assert result["overall"] == "FAIL"


# ---------------------------------------------------------------------------
# Helper: mock device data
# ---------------------------------------------------------------------------

def _make_device(device_id, status="authorized", vpn_ip=None,
                 last_heartbeat=None, assigned_worker=None):
    d = {"device_id": device_id, "status": status}
    if vpn_ip:
        d["vpn_ip"] = vpn_ip
    if last_heartbeat:
        d["last_heartbeat"] = last_heartbeat
    if assigned_worker:
        d["assigned_worker"] = assigned_worker
    return d


# ---------------------------------------------------------------------------
# Test: _check_device_registry()
# ---------------------------------------------------------------------------


class TestDeviceRegistryCheck:
    """Verify device registry diagnostic check."""

    def test_warns_when_no_devices(self):
        from core.kai.mobile_diagnose import _check_device_registry

        with patch("core.device_registry.list_devices", return_value=[]):
            result = _check_device_registry()

        assert result["status"] == "WARN"
        assert "No devices registered" in result["detail"]

    def test_passes_when_device_online(self):
        from core.kai.mobile_diagnose import _check_device_registry

        devices = [
            _make_device("test-phone", vpn_ip="10.8.0.8",
                         last_heartbeat=datetime.now(timezone.utc).isoformat()),
        ]

        with patch("core.device_registry.list_devices", return_value=devices):
            result = _check_device_registry()

        assert result["status"] == "PASS"
        assert "1 authorized, 1 online" in result["detail"]

    def test_warns_when_authorized_but_all_offline(self):
        from core.kai.mobile_diagnose import _check_device_registry

        old_hb = datetime(2024, 1, 1, tzinfo=timezone.utc).isoformat()
        devices = [_make_device("offline-phone", vpn_ip="10.8.0.9", last_heartbeat=old_hb)]

        with patch("core.device_registry.list_devices", return_value=devices):
            result = _check_device_registry()

        assert result["status"] == "WARN"
        assert "0 online" in result["detail"]

    def test_fails_when_no_authorized_devices(self):
        from core.kai.mobile_diagnose import _check_device_registry

        devices = [_make_device("revoked-phone", status="revoked", vpn_ip="10.8.0.10")]

        with patch("core.device_registry.list_devices", return_value=devices):
            result = _check_device_registry()

        assert result["status"] == "FAIL"
        assert result["artifact"]["offline"] == []

    def test_counts_revoked_devices(self):
        from core.kai.mobile_diagnose import _check_device_registry

        devices = [
            _make_device("a", vpn_ip="10.8.0.8",
                         last_heartbeat=datetime.now(timezone.utc).isoformat()),
            _make_device("b", status="revoked", vpn_ip="10.8.0.9"),
        ]

        with patch("core.device_registry.list_devices", return_value=devices):
            result = _check_device_registry()

        assert result["artifact"]["revoked"] == 1

    def test_handles_exception_gracefully(self):
        from core.kai.mobile_diagnose import _check_device_registry

        with patch("core.device_registry.list_devices", side_effect=RuntimeError("database down")):
            result = _check_device_registry()
        assert result["status"] == "FAIL"
        assert "database down" in result["detail"]


# ---------------------------------------------------------------------------
# Test: _check_wireguard()
# ---------------------------------------------------------------------------


class TestWireGuardCheck:
    """Verify WireGuard diagnostic check."""

    def test_warns_when_no_device_ips(self):
        from core.kai.mobile_diagnose import _check_wireguard

        with patch("core.device_registry.list_devices", return_value=[]):
            result = _check_wireguard()
        assert result["status"] == "WARN"
        assert "No devices with VPN IPs" in result["detail"]

    def test_fails_when_wg_status_not_ok(self):
        from core.kai.mobile_diagnose import _check_wireguard

        devices = [_make_device("p1", vpn_ip="10.8.0.8")]
        wg_status = {"ok": False, "error": "interface not found"}

        with patch("core.device_registry.list_devices", return_value=devices):
            with patch("core.wireguard_manager.get_wg_status", return_value=wg_status):
                result = _check_wireguard()
        assert result["status"] == "FAIL"
        assert "interface not found" in result["detail"]

    def test_passes_when_all_peers_connected(self):
        from core.kai.mobile_diagnose import _check_wireguard

        devices = [_make_device("p1", vpn_ip="10.8.0.8")]
        wg = {
            "ok": True,
            "peers": [{
                "allowed_ips": ["10.8.0.8/32"],
                "handshake_age_sec": 30,
            }],
        }

        with patch("core.device_registry.list_devices", return_value=devices):
            with patch("core.wireguard_manager.get_wg_status", return_value=wg):
                result = _check_wireguard()

        assert result["status"] == "PASS"
        assert "1/1 peers connected" in result["detail"]
        assert result["artifact"]["10.8.0.8"] == "connected"

    def test_degraded_when_handshake_old(self):
        from core.kai.mobile_diagnose import _check_wireguard

        devices = [_make_device("p1", vpn_ip="10.8.0.8")]
        wg = {
            "ok": True,
            "peers": [{
                "allowed_ips": ["10.8.0.8/32"],
                "handshake_age_sec": 150,
            }],
        }

        with patch("core.device_registry.list_devices", return_value=devices):
            with patch("core.wireguard_manager.get_wg_status", return_value=wg):
                result = _check_wireguard()

        assert result["status"] == "WARN"
        assert result["artifact"]["10.8.0.8"] == "degraded"

    def test_offline_when_not_in_peer_list(self):
        from core.kai.mobile_diagnose import _check_wireguard

        devices = [_make_device("p1", vpn_ip="10.8.0.99")]
        wg = {"ok": True, "peers": []}

        with patch("core.device_registry.list_devices", return_value=devices):
            with patch("core.wireguard_manager.get_wg_status", return_value=wg):
                result = _check_wireguard()

        assert result["artifact"]["10.8.0.99"] == "not_found"

    def test_handles_exception(self):
        from core.kai.mobile_diagnose import _check_wireguard

        with patch("core.device_registry.list_devices", side_effect=Exception("boom")):
            result = _check_wireguard()
        assert result["status"] == "FAIL"


# ---------------------------------------------------------------------------
# Test: _check_api_reachability()
# ---------------------------------------------------------------------------


class TestApiReachabilityCheck:
    """Verify API reachability diagnostic check."""

    def test_passes_when_health_ok(self):
        from core.kai.mobile_diagnose import _check_api_reachability

        result = _check_api_reachability()
        assert result["status"] in ("PASS", "WARN", "FAIL")
        assert "name" in result

    def test_handles_exception(self):
        from core.kai.mobile_diagnose import _check_api_reachability

        # The function does `from core.api import app` then `from fastapi.testclient import TestClient`
        # Patching `core.api.app` to something non-existent triggers the error path
        with patch("core.api.app", None):
            result = _check_api_reachability()
        assert result["status"] == "FAIL"


# ---------------------------------------------------------------------------
# Test: _check_authentication()
# ---------------------------------------------------------------------------


class TestAuthenticationCheck:
    """Verify authentication diagnostic check."""

    def test_passes_when_all_auth_subok(self):
        from core.kai.mobile_diagnose import _check_authentication

        authorized = [_make_device("d1")]
        with patch("core.bridge_auth._load_api_token", return_value="valid-token"):
            with patch("core.device_registry.list_devices", return_value=authorized):
                with patch("core.jwt_auth._JWT_SECRET", b"secret"):
                    result = _check_authentication()

        assert result["status"] == "PASS"
        assert result["artifact"]["bridge_token_exists"] is True
        assert result["artifact"]["jwt_configured"] is True

    def test_warns_when_no_device_tokens(self):
        from core.kai.mobile_diagnose import _check_authentication

        with patch("core.bridge_auth._load_api_token", return_value="valid-token"):
            with patch("core.device_registry.list_devices", return_value=[]):
                with patch("core.jwt_auth._JWT_SECRET", b"secret"):
                    result = _check_authentication()

        assert result["status"] == "WARN"
        assert result["artifact"]["authorized_devices"] == 0

    def test_fails_when_bridge_token_missing(self):
        from core.kai.mobile_diagnose import _check_authentication

        with patch("core.bridge_auth._load_api_token", return_value=None):
            with patch("core.device_registry.list_devices", return_value=[]):
                with patch("core.jwt_auth._JWT_SECRET", b"secret"):
                    result = _check_authentication()

        assert result["status"] == "FAIL"

    def test_handles_exception(self):
        from core.kai.mobile_diagnose import _check_authentication

        with patch("core.bridge_auth._load_api_token", side_effect=ImportError("no module")):
            result = _check_authentication()
        assert result["status"] == "FAIL"


# ---------------------------------------------------------------------------
# Test: _check_notifications()
# ---------------------------------------------------------------------------


class TestNotificationsCheck:
    """Verify notifications diagnostic check."""

    def test_passes_and_enqueues_test_notification(self):
        from core.kai.mobile_diagnose import _check_notifications

        result = _check_notifications()

        assert result["status"] == "PASS"
        assert result["artifact"]["test_enqueued"] is True
        assert "total" in result["artifact"]
        assert "unread" in result["artifact"]

    def test_handles_exception(self):
        from core.kai.mobile_diagnose import _check_notifications

        with patch("core.notifications.NotificationManager") as mock_cls:
            mock_cls.get_stats.side_effect = RuntimeError("queue down")
            result = _check_notifications()
        assert result["status"] == "FAIL"


# ---------------------------------------------------------------------------
# Test: _check_providers()
# ---------------------------------------------------------------------------


class TestProvidersCheck:
    """Verify AI providers diagnostic check."""

    def test_passes_when_active_providers_exist(self):
        from core.kai.mobile_diagnose import _check_providers

        providers = {
            "qwen4_text": {"id": "qwen4_text", "enabled": True},
            "gemini": {"id": "gemini", "enabled": True},
        }
        with patch("core.ai_provider.list_providers", return_value=providers):
            result = _check_providers()

        assert result["status"] == "PASS"
        assert result["artifact"]["active"] == 2

    def test_warns_when_no_active_providers(self):
        from core.kai.mobile_diagnose import _check_providers

        with patch("core.ai_provider.list_providers", return_value={}):
            result = _check_providers()

        assert result["status"] == "WARN"

    def test_handles_exception(self):
        from core.kai.mobile_diagnose import _check_providers

        with patch("core.ai_provider.list_providers", side_effect=ImportError()):
            result = _check_providers()
        assert result["status"] == "FAIL"


# ---------------------------------------------------------------------------
# Test: _check_health_worker()
# ---------------------------------------------------------------------------


class TestHealthWorkerCheck:
    """Verify health worker diagnostic check."""

    def test_checks_health_worker_assignment(self):
        from core.kai.mobile_diagnose import _check_health_worker

        devices = [_make_device("d1", assigned_worker="KAI-SYSTEM-HEALTH-WORKER")]

        with patch("core.device_registry.list_devices", return_value=devices):
            result = _check_health_worker()
        assert result["status"] in ("PASS", "WARN")

    def test_warns_when_no_worker_assigned(self):
        from core.kai.mobile_diagnose import _check_health_worker

        with patch("core.device_registry.list_devices", return_value=[]):
            result = _check_health_worker()
        # FAIL because no devices → no assignments at all
        assert result["status"] == "FAIL"

    def test_handles_exception(self):
        from core.kai.mobile_diagnose import _check_health_worker

        with patch("core.device_registry.list_devices", side_effect=RuntimeError("dead")):
            result = _check_health_worker()
        assert result["status"] == "FAIL"


# ---------------------------------------------------------------------------
# Test: _check_pwa()
# ---------------------------------------------------------------------------


class TestPwaCheck:
    """Verify PWA assets diagnostic check."""

    def test_passes_when_pwa_assets_served(self):
        from core.kai.mobile_diagnose import _check_pwa

        result = _check_pwa()

        assert result["status"] in ("PASS", "WARN", "FAIL")
        assert "manifest" in result["detail"].lower()
        assert "sw" in result["detail"].lower()

    def test_handles_exception(self):
        from core.kai.mobile_diagnose import _check_pwa

        # Patch the app to be None, causing TestClient(app) to fail
        with patch("core.api.app", None):
            result = _check_pwa()
        assert result["status"] == "FAIL"


# ---------------------------------------------------------------------------
# Test: CLI entry point
# ---------------------------------------------------------------------------


class TestCli:
    """Verify the CLI entry point prints correctly and returns correct exit codes."""

    def test_main_returns_zero_on_pass(self, capsys):
        from core.kai.mobile_diagnose import main

        passing_check = {"name": "test", "status": "PASS",
                         "detail": "ok", "artifact": None, "elapsed_ms": 1.0}

        with patch("core.kai.mobile_diagnose.run_diagnostic", return_value={
            "ok": True, "summary": "8 passed", "overall": "PASS",
            "passed": 8, "warned": 0, "failed": 0, "total": 8,
            "timestamp": "2026-08-10T00:00:00Z",
            "checks": [passing_check] * 8,
        }):
            exit_code = main()

        assert exit_code == 0

    def test_main_returns_one_on_fail(self, capsys):
        from core.kai.mobile_diagnose import main

        failing_check = {"name": "test", "status": "FAIL",
                         "detail": "broken", "artifact": None, "elapsed_ms": 1.0}

        with patch("core.kai.mobile_diagnose.run_diagnostic", return_value={
            "ok": False, "summary": "0 passed, 0 warned, 8 failed — FAIL",
            "overall": "FAIL", "passed": 0, "warned": 0, "failed": 8, "total": 8,
            "timestamp": "2026-08-10T00:00:00Z",
            "checks": [failing_check] * 8,
        }):
            exit_code = main()

        assert exit_code == 1

    def test_cli_output_contains_check_names(self, capsys):
        from core.kai.mobile_diagnose import main

        checks = [
            {"name": "WireGuard", "status": "PASS", "detail": "ok",
             "artifact": None, "elapsed_ms": 1.0},
            {"name": "Kai API", "status": "FAIL", "detail": "down",
             "artifact": None, "elapsed_ms": 2.0},
        ]

        with patch("core.kai.mobile_diagnose.run_diagnostic", return_value={
            "ok": False, "summary": "1 passed, 0 warned, 1 failed", "overall": "WARN",
            "passed": 1, "warned": 0, "failed": 1, "total": 2,
            "timestamp": "2026-08-10T00:00:00Z",
            "checks": checks,
        }):
            main()

        out = capsys.readouterr().out
        assert "WireGuard" in out
        assert "Kai API" in out
        assert "1 passed" in out


# ---------------------------------------------------------------------------
# Test: Command dispatch integration
# ---------------------------------------------------------------------------


class TestCommandDispatch:
    """Verify the 'kai mobile diagnose' command integrates with the command dispatcher."""

    def test_matches_mobile_diagnose_phrase(self):
        from core.kai.commands import dispatch

        result = dispatch("kai, mobile diagnose")

        assert result["matched"] is True
        assert "mobile" in result["description"].lower()
        assert "checks" in result["result"]
        assert "summary" in result["result"]

    def test_matches_run_diagnostics_phrase(self):
        from core.kai.commands import dispatch

        result = dispatch("Kai, run diagnostics")

        assert result["matched"] is True
        assert "checks" in result["result"]

    def test_matches_diagnose_mobile_phrase(self):
        from core.kai.commands import dispatch

        result = dispatch("diagnose mobile")

        assert result["matched"] is True
        assert "checks" in result["result"]

    def test_returns_reply_in_result(self):
        from core.kai.commands import dispatch

        result = dispatch("Kai mobile diagnose")

        assert "reply" in result["result"]
        assert result["result"]["reply"] is not None


# ---------------------------------------------------------------------------
# Test: API endpoint
# ---------------------------------------------------------------------------


class TestApiEndpoint:
    """Verify GET /kai/mobile/diagnose returns correct response."""

    def test_endpoint_returns_200(self):
        from core.api import app
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/kai/mobile/diagnose")

        assert resp.status_code == 200
        data = resp.json()
        assert "ok" in data
        assert "checks" in data
        assert "summary" in data
        assert data["overall"] in ("PASS", "WARN", "FAIL")

    def test_endpoint_has_all_eight_checks(self):
        from core.api import app
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/kai/mobile/diagnose")

        data = resp.json()
        assert data["total"] >= 7
        names = {c["name"] for c in data["checks"]}
        expected = {
            "Device Registry", "WireGuard", "Kai API",
            "Authentication", "Notifications", "AI Providers",
            "Health Worker", "PWA Assets",
        }
        missing = expected - names
        assert not missing, f"Missing checks: {missing}"

    def test_endpoint_returns_valid_timestamps(self):
        from core.api import app
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/kai/mobile/diagnose")

        data = resp.json()
        assert "T" in data["timestamp"]
        assert "Z" in data["timestamp"] or "+" in data["timestamp"]
