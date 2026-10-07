"""Tests for the CT111 WireGuard controller (thin transport + enrichment).

The SSH transport is injected, so nothing here touches the network. The
stale CT102 assumptions were replaced by the CT105 chain; see
``tests/test_jk_wg_service.py`` for the state-classifier + builder suite.
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

    def call(self, op, args=(), *, write=False, actor=None):
        self.calls.append((op, list(args), write, actor))
        r = self.responses.get(op)
        if isinstance(r, Exception):
            raise r
        return r if r is not None else {"ok": True}


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    S.set_transport(None)
    monkeypatch.setenv("WG_AGENT_TOKEN", "test-token")
    yield
    S.set_transport(None)


def test_build_agent_command_chain_points_at_ct105():
    cmd = S.build_agent_command("list")
    joined = " ".join(cmd)
    assert cmd[0] == "ssh"
    assert "kai_pve_usage" in joined
    assert "root@192.168.1.110" in joined
    assert "root@192.168.99.2" in joined
    assert "pct exec 105 -- python3 /opt/kai-wg-agent/wg_agent.py list" in joined


def test_parse_agent_stdout_uses_last_json_line():
    out = "Warning: something\n" + json.dumps({"ok": True, "data": {"n": 1}})
    assert S._parse_agent_stdout(out) == {"ok": True, "data": {"n": 1}}


def test_parse_agent_stdout_errors_on_garbage():
    with pytest.raises(S.WgServiceError):
        S._parse_agent_stdout("not json at all")


def test_list_peers_delegates():
    t = FakeTransport({"list": {"count": 3, "server": {},
                                "peers": [{"pubkey": "P", "latest_handshake": 0,
                                           "paused": False}]},
                       "usage": {"peers": {}}})
    S.set_transport(t)
    out = S.list_peers()
    assert out["count"] == 1
    assert out["peers"][0]["state"] == "disconnected"
    assert {c[0] for c in t.calls} == {"list", "usage"}


def test_add_peer_attaches_qr_and_passes_options():
    t = FakeTransport({"add_peer": {"pubkey": "P", "ip": "10.6.0.8",
                                    "config": "[Interface]\nPrivateKey = x\n"}})
    S.set_transport(t)
    out = S.add_peer("Phone", dns="1.1.1.1")
    assert out["ip"] == "10.6.0.8"
    assert out["qr_png_base64"]
    assert base64.b64decode(out["qr_png_base64"])[:4] == b"\x89PNG"
    op, args, write, _ = t.calls[-1]
    assert op == "add_peer" and write is True
    assert "Phone" in args and "--dns" in args and "1.1.1.1" in args


def test_pause_resume_delete_delegate_and_validate():
    t = FakeTransport({"pause": {"ok": True}, "resume": {"ok": True},
                       "del_peer": {"ok": True}})
    S.set_transport(t)
    S.pause_peer("P")
    S.resume_peer("P")
    S.delete_peer("P")
    assert [c[0] for c in t.calls] == ["pause", "resume", "del_peer"]
    assert all(c[2] is True for c in t.calls)


def test_error_from_agent_propagates_with_status():
    S.set_transport(FakeTransport({"add_peer": S.WgServiceError("no free", 409)}))
    with pytest.raises(S.WgServiceError) as ei:
        S.add_peer("x")
    assert ei.value.status == 409


def test_qr_png_is_valid_png():
    raw = base64.b64decode(S.qr_png_base64("hello"))
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
