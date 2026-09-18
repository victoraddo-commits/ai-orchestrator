"""§53 acceptance — a mission started through the Telegram interface.

The live Telegram send is exercised by the production poller; these tests
drive the exact inbound handler (``telegram_bridge.route_inbound_reply``)
end-to-end with the model mocked, proving:

  * a ``/mission`` message is routed through the Command Bus (AgentGuard
    authorization + audit) into the live WorkforceEngine, and
  * a bot denied by the Telegram Module identity gate cannot start a mission.
"""
from __future__ import annotations

import time

import pytest

import core.telegram_bridge as tb


@pytest.fixture(autouse=True)
def _reset_singletons():
    from core.teammate.runtime import reset_runtime
    from core.teammate.routes import reset_engine
    reset_runtime()
    reset_engine()
    yield
    reset_runtime()
    reset_engine()


def _fake_delegate(monkeypatch):
    import core.ai.ai_router as ai_router

    def _default(description, **kw):
        return {"provider": kw.get("provider") or "kai_brain",
                "response": f"[mock] {str(description)[:60]}",
                "duration_ms": 1, "attempts": []}

    monkeypatch.setattr(ai_router, "delegate", _default)


def _message(text):
    return {
        "text": text,
        "from": {"id": "612786480", "first_name": "Operator",
                 "username": "operator"},
        "chat": {"id": 612786480, "type": "private"},
    }


def test_telegram_mission_flow_is_audited(monkeypatch):
    from core.teammate.engine import WorkforceEngine

    monkeypatch.setattr(tb, "send_typing", lambda **kw: None)
    _fake_delegate(monkeypatch)
    engine = WorkforceEngine(model_verify=False)

    result = tb.route_inbound_reply(
        _message("/mission Build a tiny feature module"), pending_builds=[])

    assert result["action"] == "control_command", result
    assert "mission `mis-" in result["reply"] and "started" in result["reply"]
    mid = result["reply"].split("`")[1]

    deadline = time.time() + 15
    mission = None
    while time.time() < deadline:
        mission = engine.get_mission(mid)
        if mission and mission["status"] not in ("CREATED", "RUNNING"):
            break
        time.sleep(0.05)
    assert mission is not None
    assert mission["status"] == "COMPLETED", mission

    # policy layer: the command was authorized and audited by the Command Bus
    from core.memory import load
    audit = load("command_bus_audit") or {}
    rows = audit.get("records") or []
    mine = [r for r in rows if r.get("command") == "/mission"]
    assert mine, rows
    assert mine[-1].get("source") == "telegram"
    assert mine[-1].get("decision") == "allow"
    assert mine[-1].get("status") == "success"


def test_telegram_denied_bot_cannot_start_mission(monkeypatch):
    from core.teammate.engine import WorkforceEngine

    monkeypatch.setattr(tb, "send_typing", lambda **kw: None)
    import core.telegram.guard as tgg
    monkeypatch.setattr(tgg, "verify",
                        lambda *a, **k: {"allowed": False,
                                         "reason": "bot disabled"})
    engine = WorkforceEngine(model_verify=False)
    before = len(engine.list_missions())

    result = tb.route_inbound_reply(
        _message("/mission should never run"), pending_builds=[])

    assert result["action"] == "telegram_module_denied", result
    assert len(engine.list_missions()) == before
