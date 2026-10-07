"""Tests for the CT111 WireGuard controller (CT105 chain).

Covers the pure state classifier (connected/idle/disconnected/paused at the
180 s / 900 s boundaries) and the agent command builder, with a stubbed
transport so no SSH is attempted.
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from core import wg_peer_service as S  # noqa: E402


class FakeTransport:
    """New-style transport: ``call(op, args, write=, actor=)``."""

    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}

    def call(self, op, args=(), *, write=False, actor=None):
        self.calls.append((op, list(args), write, actor))
        r = self.responses.get(op)
        if isinstance(r, Exception):
            raise r
        if r is None:
            return {"peers": [], "server": {}, "count": 0}
        return r


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    S.set_transport(None)
    monkeypatch.setenv("WG_AGENT_TOKEN", "test-token")
    yield
    S.set_transport(None)


# ── state classification boundaries ──────────────────────────────────────

@pytest.mark.parametrize("age,expected", [
    (0, "connected"),
    (179, "connected"),
    (180, "idle"),
    (181, "idle"),
    (899, "idle"),
    (900, "idle"),
    (901, "disconnected"),
    (10_000, "disconnected"),
])
def test_classify_state_boundaries(age, expected):
    now = 1_000_000
    assert S.classify_state(now - age, False, now=now) == expected


def test_classify_never_handshake_is_disconnected():
    assert S.classify_state(0, False, now=1_000_000) == "disconnected"


def test_classify_paused_wins_over_connected():
    now = 1_000_000
    assert S.classify_state(now, True, now=now) == "paused"
    assert S.classify_state(0, True, now=now) == "paused"


# ── enrichment ───────────────────────────────────────────────────────────

def test_enrich_peers_merges_state_health_and_deltas():
    now = 1_000_000
    data = {"server": {"up": True, "listen_port": 51820, "peer_count": 2},
            "peers": [
                {"pubkey": "A", "latest_handshake": now - 30, "paused": False,
                 "rx_bytes": 100, "tx_bytes": 200, "endpoint": "1.2.3.4:5"},
                {"pubkey": "B", "latest_handshake": now - 5000, "paused": True,
                 "rx_bytes": 0, "tx_bytes": 0, "endpoint": None},
            ]}
    usage = {"peers": {"A": {"rx_delta_24h": 10, "tx_delta_24h": 20,
                             "rx_delta_7d": 30, "tx_delta_7d": 40}}}
    out = S.enrich_peers(data, usage, now=now)
    a, b = out["peers"]
    assert a["state"] == "connected" and a["handshake_age_s"] == 30
    assert a["rx_delta_24h"] == 10 and a["tx_delta_7d"] == 40
    assert b["state"] == "paused"
    assert out["states"] == {"connected": 1, "idle": 0,
                             "disconnected": 0, "paused": 1}


# ── command construction ─────────────────────────────────────────────────

def test_build_agent_command_read_chain():
    cmd = S.build_agent_command("list")
    assert cmd[0] == "ssh"
    joined = " ".join(cmd)
    assert cmd[-2] == "root@192.168.1.110"
    assert "kai_pve_usage" in joined
    assert "root@192.168.99.2" in joined
    assert "/opt/kai-wg-agent/wg_agent.py list" in joined
    assert "pct exec 105 --" in joined
    # read ops carry no write gate
    assert "WG_AGENT_ALLOW_WRITE" not in joined


def test_build_agent_command_write_has_env_and_token():
    cmd = S.build_agent_command("pause", ["A/B+C=", "My Peer"],
                                write=True, actor="owner")
    joined = " ".join(cmd)
    assert "WG_AGENT_ALLOW_WRITE=1" in joined
    assert "test-token" in joined
    assert "WG_AGENT_BY=owner" in joined
    # base64 pubkey chars are shell-safe; names with spaces must be quoted
    assert "A/B+C=" in joined
    assert "'My Peer'" in joined


def test_build_agent_command_rejects_unknown_op():
    with pytest.raises(S.WgServiceError):
        S.build_agent_command("rm -rf")


def test_parse_agent_stdout_uses_last_json_line():
    out = "junk\n" + json.dumps({"ok": True, "data": {"n": 1}})
    assert S._parse_agent_stdout(out) == {"ok": True, "data": {"n": 1}}


def test_parse_agent_stdout_errors_on_garbage():
    with pytest.raises(S.WgServiceError):
        S._parse_agent_stdout("not json")


# ── delegation through the (stubbed) transport ───────────────────────────

def test_list_peers_calls_list_and_usage():
    t = FakeTransport({"list": {"server": {"up": True},
                                "peers": [{"pubkey": "A", "latest_handshake": 0,
                                           "paused": False}]},
                       "usage": {"peers": {}}})
    S.set_transport(t)
    out = S.list_peers()
    assert out["count"] == 1 and out["peers"][0]["state"] == "disconnected"
    assert [c[0] for c in t.calls] == ["list", "usage"]


def test_add_peer_attaches_qr_and_passes_options():
    t = FakeTransport({"add_peer": {"pubkey": "P", "ip": "10.6.0.8",
                                    "config": "[Interface]\nPrivateKey = x\n"}})
    S.set_transport(t)
    out = S.add_peer("Phone", dns="1.1.1.1", mode="client")
    assert out["ip"] == "10.6.0.8"
    assert base64.b64decode(out["qr_png_base64"])[:4] == b"\x89PNG"
    op, args, write, actor = t.calls[-1]
    assert op == "add_peer" and write is True
    assert "Phone" in args and "--dns" in args and "1.1.1.1" in args


def test_mutations_are_write_calls():
    t = FakeTransport({"del_peer": {"ok": True}, "pause": {"ok": True},
                       "resume": {"ok": True}, "rename": {"ok": True},
                       "set_allowed": {"ok": True}, "restart": {"ok": True}})
    S.set_transport(t)
    S.pause_peer("A")
    S.resume_peer("A")
    S.delete_peer("A")
    S.rename_peer("A", "new")
    S.set_allowed("A", "10.6.0.9/32")
    S.restart_server()
    assert [c[0] for c in t.calls] == ["pause", "resume", "del_peer", "rename",
                                       "set_allowed", "restart"]
    assert all(c[2] is True for c in t.calls)


def test_server_status_counts_states():
    t = FakeTransport({"list": {"server": {"up": True, "listen_port": 51820},
                                "peers": [
                                    {"pubkey": "A", "latest_handshake": 0,
                                     "paused": False},
                                    {"pubkey": "B", "latest_handshake": 0,
                                     "paused": True}]}})
    S.set_transport(t)
    s = S.server_status()
    assert s["peer_count"] == 2
    assert s["states"]["paused"] == 1 and s["states"]["disconnected"] == 1


def test_peer_config_is_one_time_only():
    S.set_transport(FakeTransport())
    with pytest.raises(S.WgServiceError) as ei:
        S.peer_config("A")
    assert ei.value.status == 410


def test_error_from_agent_propagates_with_status():
    S.set_transport(FakeTransport({"add_peer": S.WgServiceError("no free", 409)}))
    with pytest.raises(S.WgServiceError) as ei:
        S.add_peer("x")
    assert ei.value.status == 409


def test_qr_png_is_valid_png():
    raw = base64.b64decode(S.qr_png_base64("hello"))
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
