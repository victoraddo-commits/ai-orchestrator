"""Tests for /mobile/api/wallet (the new wallet inspection endpoint)."""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def client():
    """TestClient with the mobile launcher routes mounted."""
    from fastapi import FastAPI
    from core.mobile_launcher_routes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_wallet_returns_minimum_shape(client):
    """Even with no monitor + no MC reachable, the response should
    have the top-level keys (no crash)."""
    with patch("os.path.exists", return_value=False):
        resp = client.get("/mobile/api/wallet")
    assert resp.status_code == 200
    body = resp.json()
    # Required top-level keys
    for k in ("address", "checked_at", "monitor", "master_treasury",
              "pending_capital_requests", "auto_sweep"):
        assert k in body, f"missing {k!r}"
    assert body["address"] == "0xa854EdEd5e1211Cb42bD28Ea53e4424Fa27ebaDd"
    # monitor + auto_sweep indicate not-running when no state file
    assert body["monitor"]["running"] is False
    assert body["auto_sweep"]["enabled"] in (True, False)


def test_wallet_includes_monitor_state_when_present(client):
    """If the monitor state file exists, it's parsed and returned."""
    with tempfile.TemporaryDirectory() as d:
        # Write a state file the endpoint will pick up
        state_path = Path(d) / "wallet_monitor_state.json"
        state_path.write_text(json.dumps({
            "address": "0xa854EdEd5e1211Cb42bD28Ea53e4424Fa27ebaDd",
            "last_balance": {"eth": 0.0, "usdt": 34.9, "usdc": 0.0, "tx_count": 0},
            "last_alert_at": "2026-08-27T02:00:00Z",
            "watch_list": [{"tx_hash": "0xabc", "status": "seen"}],
            "alerts_history": [{"ts": "x", "kind": "deposit", "delta_usd": 4.7}],
        }))
        # Patch the env var to point to our temp dir
        with patch.dict(os.environ, {"KAI_WALLET_STATE_DIR": d}):
            # Also mock the on-chain read so it doesn't hit the network
            with patch("urllib.request.urlopen") as mock_u:
                mock_resp = MagicMock()
                mock_resp.read.side_effect = [
                    json.dumps({"result": "0"}).encode(),  # ETH
                    json.dumps({"result": "34900000"}).encode(),  # USDT
                ]
                mock_resp.__enter__ = MagicMock(return_value=mock_resp)
                mock_resp.__exit__ = MagicMock(return_value=False)
                mock_u.return_value = mock_resp
                resp = client.get("/mobile/api/wallet")
        assert resp.status_code == 200
        body = resp.json()
        assert body["monitor"]["running"] is True
        assert body["monitor"]["watch_list_count"] == 1
        assert body["monitor"]["alerts_history_count"] == 1
        assert body["monitor"]["baseline_balance"]["usdt"] == 34.9
        # delta should be 0 (current = baseline)
        assert body["monitor"]["delta_from_baseline"]["usdt"] == 0


def test_wallet_sweeper_state_loaded(client):
    """If the sweeper state file exists, it's reported."""
    with tempfile.TemporaryDirectory() as d:
        sweeper_path = Path(d) / "wallet_sweeper_state.json"
        sweeper_path.write_text(json.dumps({
            "last_on_chain_usdt": 4700000,
            "last_sweep_ts": "2026-08-27T02:30:00Z",
            "total_swept": 4.7,
            "last_sweep_tx": "0x20ae5eae...",
        }))
        with patch.dict(os.environ, {"KAI_WALLET_STATE_DIR": d}):
            with patch("os.path.exists", return_value=False):  # no monitor state
                resp = client.get("/mobile/api/wallet")
        body = resp.json()
        assert body["auto_sweep"]["last_sweep_ts"] == "2026-08-27T02:30:00Z"
        assert body["auto_sweep"]["total_swept"] == 4.7


def test_wallet_no_auth_token_still_works_via_lan_guard(client):
    """The endpoint is LAN-guarded; from the TestClient's loopback peer
    it's allowed without any token (loopback is in the LAN CIDR)."""
    # No Authorization header at all
    resp = client.get("/mobile/api/wallet")
    assert resp.status_code == 200
