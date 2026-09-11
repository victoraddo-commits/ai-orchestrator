"""Tests for core.vpn_health_monitor — TK-176d6efe."""
from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import patch, MagicMock

import pytest

# Point memory at an isolated dir before importing anything that touches it.
os.environ.setdefault("AI_ORCHESTRATOR_MEMORY_DIR", tempfile.mkdtemp())

from core import vpn_health_monitor as vhm


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _status_healthy() -> dict:
    """`tailscale status --json` shape where both expected peers advertise
    AND we accept the routes AND both are online."""
    return {
        "Peer": {
            "abc": {
                "HostName": "pve-1",
                "Online": True,
                "AdvertisedRoutes": ["192.168.99.0/24"],
                "PrimaryRoutes": ["192.168.99.0/24"],
            },
            "def": {
                "HostName": "pve-2",
                "Online": True,
                "AdvertisedRoutes": ["192.168.1.0/24"],
                "PrimaryRoutes": ["192.168.1.0/24"],
            },
        },
    }


@pytest.fixture(autouse=True)
def _isolate_memory(tmp_path, monkeypatch):
    """Every test gets its own memory dir so history doesn't leak."""
    monkeypatch.setenv("AI_ORCHESTRATOR_MEMORY_DIR", str(tmp_path))
    # Also patch core.memory._default_memory_dir if it caches at import time.
    from core import memory as _mem
    monkeypatch.setattr(_mem, "_default_memory_dir", lambda: str(tmp_path))


# ---------------------------------------------------------------------------
# _verdict pure-function tests
# ---------------------------------------------------------------------------

def test_verdict_healthy_when_all_routes_accepted_and_ping_ok():
    routes = vhm._peer_routes_from_status(_status_healthy())
    verdict, detail = vhm._verdict(_status_healthy(), True, routes)
    assert verdict == "healthy"
    assert "routes accepted" in detail


def test_verdict_degraded_when_route_missing():
    st = _status_healthy()
    # pve-2 no longer offers 192.168.1.0/24
    st["Peer"]["def"]["PrimaryRoutes"] = []
    routes = vhm._peer_routes_from_status(st)
    verdict, detail = vhm._verdict(st, True, routes)
    assert verdict == "degraded"
    assert "192.168.1.0/24" in detail


def test_verdict_down_when_peer_offline():
    st = _status_healthy()
    st["Peer"]["def"]["Online"] = False
    routes = vhm._peer_routes_from_status(st)
    verdict, _ = vhm._verdict(st, True, routes)
    assert verdict == "down"


def test_verdict_degraded_when_routes_ok_but_ping_fails():
    routes = vhm._peer_routes_from_status(_status_healthy())
    verdict, detail = vhm._verdict(_status_healthy(), False, routes)
    assert verdict == "degraded"
    assert "ping" in detail.lower()


def test_verdict_down_when_status_unavailable():
    verdict, detail = vhm._verdict(None, False, {})
    assert verdict == "down"
    assert "tailscale" in detail.lower()


# ---------------------------------------------------------------------------
# run_once integration tests (subprocess mocked)
# ---------------------------------------------------------------------------

def _mock_completed(stdout: str = "", returncode: int = 0):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    return m


def test_run_once_healthy_and_persists():
    ts_json = json.dumps(_status_healthy())
    ping_output = "PING 192.168.1.1 (192.168.1.1) 56(84) bytes\n64 bytes from 192.168.1.1: time=42.1 ms\n"

    def fake_run(cmd, **kwargs):
        if cmd[0] == "tailscale":
            return _mock_completed(ts_json)
        if cmd[0] == "ping":
            return _mock_completed(ping_output)
        raise AssertionError(f"unexpected cmd {cmd}")

    with patch("subprocess.run", side_effect=fake_run):
        rec = vhm.run_once()

    assert rec["verdict"] == "healthy"
    assert rec["ping"]["ok"] is True
    assert rec["ping"]["rtt_ms"] == pytest.approx(42.1)
    assert rec["peers"]["192.168.99.0/24"]["accepted"] is True

    # Persisted to history.
    latest = vhm.latest()
    assert latest is not None
    assert latest["verdict"] == "healthy"
    assert len(vhm.history(50)) == 1


def test_run_once_ping_failure_marks_degraded():
    ts_json = json.dumps(_status_healthy())

    def fake_run(cmd, **kwargs):
        if cmd[0] == "tailscale":
            return _mock_completed(ts_json)
        if cmd[0] == "ping":
            return _mock_completed("", returncode=1)
        raise AssertionError

    with patch("subprocess.run", side_effect=fake_run):
        rec = vhm.run_once()

    assert rec["verdict"] == "degraded"
    assert rec["ping"]["ok"] is False


def test_run_once_appends_and_bounds_history():
    ts_json = json.dumps(_status_healthy())

    def fake_run(cmd, **kwargs):
        if cmd[0] == "tailscale":
            return _mock_completed(ts_json)
        return _mock_completed("time=1.0 ms")

    with patch("subprocess.run", side_effect=fake_run):
        for _ in range(3):
            vhm.run_once()

    hist = vhm.history(10)
    assert len(hist) == 3
    # history() returns newest first
    for i in range(len(hist) - 1):
        assert hist[i]["ts"] >= hist[i + 1]["ts"]
