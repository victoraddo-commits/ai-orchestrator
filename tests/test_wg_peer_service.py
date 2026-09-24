"""Tests for the CT111 WireGuard peer controller.

The SSH transport is injected, so nothing here touches the network.
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
    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}

    def call(self, payload):
        self.calls.append(payload)
        op = payload.get("op")
        if op in self.responses:
            r = self.responses[op]
            if isinstance(r, Exception):
                raise r
            return r
        return {"ok": True}


@pytest.fixture(autouse=True)
def _reset_transport():
    S.set_transport(None)
    yield
    S.set_transport(None)


# ── command construction ──────────────────────────────────────────────────

def test_build_agent_command_roundtrips_payload_through_chain():
    cmd = S.build_agent_command({"op": "list", "x": "a b"})
    assert cmd[0] == "ssh"
    assert "kai_pve_usage" in " ".join(cmd)
    assert "root@192.168.1.110" in cmd
    joined = " ".join(cmd)
    assert "root@100.83.4.27" in joined
    assert "pct exec 102 -- python3 /opt/kai-wg-agent/wg_agent.py" in joined
    # the base64 payload must decode back to the exact dict
    b64 = cmd[-1].split()[-1].strip("'")
    assert json.loads(base64.b64decode(b64)) == {"op": "list", "x": "a b"}


def test_parse_agent_stdout_uses_last_json_line():
    out = "Warning: something\n" + json.dumps({"ok": True, "data": {"n": 1}})
    assert S._parse_agent_stdout(out) == {"ok": True, "data": {"n": 1}}


def test_parse_agent_stdout_errors_on_garbage():
    with pytest.raises(S.WgServiceError):
        S._parse_agent_stdout("not json at all")


# ── delegation ────────────────────────────────────────────────────────────

def test_list_peers_delegates():
    t = FakeTransport({"list": {"count": 3, "peers": [{"pubkey": "P"}]}})
    S.set_transport(t)
    assert S.list_peers()["count"] == 3
    assert t.calls[-1] == {"op": "list"}


def test_add_peer_attaches_qr_and_passes_options():
    t = FakeTransport({"add": {"pubkey": "P", "ip": "10.6.0.8",
                               "config": "[Interface]\nPrivateKey = x\n"}})
    S.set_transport(t)
    out = S.add_peer("Phone", dns="1.1.1.1")
    assert out["ip"] == "10.6.0.8"
    assert out["qr_png_base64"]
    base64.b64decode(out["qr_png_base64"])[:4] == b"\x89PNG"
    assert t.calls[-1]["name"] == "Phone" and t.calls[-1]["dns"] == "1.1.1.1"


def test_pause_resume_delete_delegate_and_validate():
    t = FakeTransport({"pause": {"ok": True}, "resume": {"ok": True},
                       "delete": {"ok": True}})
    S.set_transport(t)
    S.pause_peer("P")
    S.resume_peer("P")
    S.delete_peer("P")
    assert [c["op"] for c in t.calls] == ["pause", "resume", "delete"]


def test_peer_config_and_qr():
    t = FakeTransport({"config": {"config": "[Interface]\nPrivateKey = x\n",
                                  "name": "Phone"}})
    S.set_transport(t)
    assert "PrivateKey" in S.peer_config("P", "openwrt")["config"]
    qr = S.peer_qr("P", "ddwrt")
    assert qr["qr_png_base64"] and qr["name"] == "Phone"


# ── error propagation ─────────────────────────────────────────────────────

def test_error_from_agent_propagates_with_status():
    S.set_transport(FakeTransport({"add": S.WgServiceError("no free address", 400)}))
    with pytest.raises(S.WgServiceError) as ei:
        S.add_peer("x")
    assert ei.value.status == 400


def test_qr_png_is_valid_png():
    raw = base64.b64decode(S.qr_png_base64("hello"))
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
