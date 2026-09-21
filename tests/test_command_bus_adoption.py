"""Build 23A — Command Bus adoption across interfaces.

Before this build only Telegram/CLI (``core.kai_control_commands``) and the
doctor routed through ``core.command_bus``. The Command Center/web actions,
voice/text intents, the Android factory's mutating path and the worker pool now
dispatch through the bus too, so AgentGuard authorizes them and every routed
action lands in ``command_bus_audit.json`` (merged into the CC audit feed).

Reads stay direct on purpose; see ``core.command_bus._register_control_commands``
for the still-direct list and why.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

AUTH = {"Authorization": "Bearer test-bridge"}

CONTROL_COMMANDS = (
    "control.emergency.stop",
    "control.emergency.resume",
    "control.scheduler.pause",
    "control.scheduler.resume",
    "control.mission.steer",
    "control.mission.stop",
    "control.factory.build",
    "control.factory.scaffold",
    "control.voice.intent",
    "control.worker.submit",
)


def _records():
    from core.memory import load
    data = load("command_bus_audit.json") or {}
    while isinstance(data, dict) and isinstance(data.get("records"), dict):
        data = data["records"]
    return (data.get("records") if isinstance(data, dict) else data) or []


@pytest.fixture()
def client():
    from core.api import app
    return TestClient(app)


@pytest.fixture()
def isolated_control(tmp_path, monkeypatch):
    """Point emergency/scheduler state and the bridge token at tmp."""
    import core.api as api
    import core.kai_emergency as ke
    import core.scheduler as sched

    monkeypatch.setattr(ke, "KILL_SWITCH_PATH", tmp_path / "stopped.json")
    monkeypatch.setattr(ke, "AUDIT_PATH", tmp_path / "tool_audit.jsonl")
    monkeypatch.setattr(sched, "SCHEDULER_PAUSE_FILE", tmp_path / "paused.json")
    monkeypatch.setattr(api, "SCHEDULER_PAUSE_FILE", tmp_path / "paused.json")
    monkeypatch.setattr(api, "_load_api_token", lambda: "test-bridge")
    return tmp_path


# ── registry + audit ────────────────────────────────────────────────────────

def test_control_commands_are_registered():
    from core.command_bus import get_bus
    bus = get_bus()
    for cmd in CONTROL_COMMANDS:
        assert bus.has(cmd), cmd


def test_emergency_roundtrip_dispatches_and_audits(isolated_control):
    from core.command_bus import get_bus
    bus = get_bus()

    stopped = bus.dispatch("control.emergency.stop", {"reason": "unit"},
                           source="test", user="tester")
    assert stopped["status"] == "success" and stopped["data"]["stopped"] is True
    resumed = bus.dispatch("control.emergency.resume", {}, source="test", user="tester")
    assert resumed["status"] == "success"

    cmds = [r["command"] for r in _records()]
    assert "control.emergency.stop" in cmds and "control.emergency.resume" in cmds
    last = _records()[-1]
    assert last["command"] == "control.emergency.resume"
    assert last["source"] == "test" and last["user"] == "tester"
    assert last["status"] == "success" and last["risk"] == "medium"


def test_guard_refusal_is_surfaced_and_never_executes(isolated_control):
    """A CRITICAL classification must refuse before the handler — no bypass."""
    from core.command_bus import get_bus
    r = get_bus().dispatch("control.scheduler.pause",
                           {"reason": "please rm -rf / now"},
                           source="test", user="tester")
    assert r["status"] == "error"
    assert r["decision"] == "require_approval"
    rec = _records()[-1]
    assert rec["command"] == "control.scheduler.pause"
    assert rec["status"] == "approval_required"
    # and nothing was paused
    from core.kai_emergency import stopped_info
    assert (isolated_control / "paused.json").exists() is False


# ── CLI (already bus-backed) ────────────────────────────────────────────────

def test_cli_control_commands_route_through_bus(monkeypatch):
    from core import kai_control_commands as kcc
    from core.command_bus import get_bus
    monkeypatch.setattr(kcc, "handle_control_command", lambda text: f"reply:{text}")
    r = get_bus().dispatch("/status", params={"text": "/status"},
                           source="cli", user="operator")
    assert r["status"] == "success" and r["data"] == "reply:/status"
    assert _records()[-1]["command"] == "/status"


# ── Command Center / web ────────────────────────────────────────────────────

def test_cc_emergency_endpoints_dispatch_through_bus(client, isolated_control):
    stop = client.post("/api/emergency/stop", json={"reason": "cc"}, headers=AUTH)
    assert stop.status_code == 200 and stop.json()["stopped"] is True
    resume = client.post("/api/emergency/resume", headers=AUTH)
    assert resume.status_code == 200

    cmds = [r["command"] for r in _records()]
    assert "control.emergency.stop" in cmds and "control.emergency.resume" in cmds
    assert all(r["source"] == "cc_web" for r in _records()[-2:])


def test_cc_scheduler_admin_endpoints_dispatch_through_bus(client, isolated_control):
    paused = client.post("/api/admin/pause-scheduler", json={"reason": "cc"}, headers=AUTH)
    assert paused.status_code == 200 and paused.json()["scheduler"]["paused"] is True
    resumed = client.post("/api/admin/resume-scheduler", headers=AUTH)
    assert resumed.status_code == 200

    cmds = [r["command"] for r in _records()]
    assert "control.scheduler.pause" in cmds and "control.scheduler.resume" in cmds


def test_mission_steer_route_dispatches_and_keeps_domain_status(client):
    from core.kai import mission_store as ms

    mission = ms.new_mission(objective="steer me")
    ms.transition_mission(mission["id"], "approved")
    ms.transition_mission(mission["id"], "running")

    ok = client.post(f"/kai/missions/{mission['id']}/steer",
                     json={"action": "pause"}, headers=AUTH)
    assert ok.status_code == 200 and ok.json()["mission"]["status"] == "paused"
    assert _records()[-1]["command"] == "control.mission.steer"

    # domain error mapping preserved: unknown mission → 404 (not 403/500)
    missing = client.post("/kai/missions/nope/steer",
                          json={"action": "pause"}, headers=AUTH)
    assert missing.status_code == 404
    # redirect without objective → 422
    bad = client.post(f"/kai/missions/{mission['id']}/steer",
                      json={"action": "redirect"}, headers=AUTH)
    assert bad.status_code == 422


# ── voice / text intents ────────────────────────────────────────────────────

def test_voice_mutating_intent_routes_through_bus(monkeypatch):
    import re
    from core.kai import commands

    calls = []

    def _mutating_handler():
        calls.append(1)
        return {"reply": "done"}

    pattern = re.compile(r"^do the thing$", re.IGNORECASE)
    monkeypatch.setattr(commands, "COMMAND_PATTERNS",
                        ((pattern, _mutating_handler, "test mutating", True),))

    out = commands.dispatch("do the thing", via_bus=True, source="voice", user="operator")
    assert calls == [1]
    assert out["matched"] is True and out["result"] == {"reply": "done"}
    assert _records()[-1]["command"] == "control.voice.intent"
    assert _records()[-1]["source"] == "voice"


def test_voice_read_only_intent_stays_direct(monkeypatch):
    import re
    from core.kai import commands

    before = len(_records())
    pattern = re.compile(r"^just read$", re.IGNORECASE)
    monkeypatch.setattr(commands, "COMMAND_PATTERNS",
                        ((pattern, lambda: {"reply": "read"}, "test read", False),))
    out = commands.dispatch("just read", via_bus=True, source="voice", user="operator")
    assert out["result"] == {"reply": "read"}
    assert len(_records()) == before  # no bus event for a read


# ── Android factory mutating path ───────────────────────────────────────────

def test_factory_build_dispatches_through_bus(monkeypatch, tmp_path):
    import urllib.request

    import core.kai_tools.builtin as builtin

    token = tmp_path / "tok"
    token.write_text("factory-secret\n")
    monkeypatch.setattr(builtin, "FACTORY_TOKEN_FILE", str(token))

    class _Resp:
        def read(self):
            return json.dumps({"status": "success", "returncode": 0}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: _Resp())

    out = builtin.factory_build("abc123def4")
    assert out["ok"] is True and out["project"] == "abc123def4"
    assert _records()[-1]["command"] == "control.factory.build"
    assert _records()[-1]["source"] == "android_factory"
    assert "factory-secret" not in json.dumps(out)


# ── workers ─────────────────────────────────────────────────────────────────

def test_pool_submit_dispatches_through_bus(monkeypatch):
    from core.workers import deepseek_pool as dp

    pool = dp.DeepSeekWorkerPool(workers=1)
    monkeypatch.setattr(dp, "_pool", pool)

    task_id = pool.submit("planning", "design a thing")
    assert task_id.startswith("ds-")
    assert pool.status()["queued"] == 1
    assert _records()[-1]["command"] == "control.worker.submit"
    assert _records()[-1]["source"] == "worker_pool"


def test_pool_submit_escape_hatch_for_non_singleton(monkeypatch):
    """A directly-constructed pool the bus cannot address enqueues directly."""
    from core.workers import deepseek_pool as dp

    monkeypatch.setattr(dp, "_pool", None)
    pool = dp.DeepSeekWorkerPool(workers=1)
    before = len(_records())

    task_id = pool.submit("planning", "design another thing")
    assert task_id.startswith("ds-")
    assert pool.status()["queued"] == 1
    assert len(_records()) == before
