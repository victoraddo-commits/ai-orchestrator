#!/usr/bin/env python3
"""§53 live acceptance: drive the Telegram inbound handler end-to-end.

The production Telegram poller owns getUpdates; a bot cannot receive its own
message, so a true operator-to-bot round trip is performed by the live poller
service. This drill instead drives the exact inbound handler the poller calls
(``core.telegram_bridge.route_inbound_reply``) with one operator-chat message,
through the Command Bus into the live WorkforceEngine, and reports the full
flow + policy audit. Only the outbound ``send_typing`` Telegram API call is
stubbed; everything else — identity gate, Command Bus/AgentGuard, mission
engine, Model Fabric — is the deployed code. Set ``KAI_TG_E2E_MEMORY`` to
isolate the run (defaults to /tmp/kai_tg_e2e_memory).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

MEM = os.environ.get("KAI_TG_E2E_MEMORY", "/tmp/kai_tg_e2e_memory")
Path(MEM).mkdir(parents=True, exist_ok=True)
os.environ["AI_ORCHESTRATOR_MEMORY_DIR"] = MEM

import core.memory as memory  # noqa: E402

memory.MEMORY_DIR = Path(MEM)

import core.telegram_bridge as tb  # noqa: E402

# Keep the identity gate + policy path real; only skip the outbound API call.
tb.send_typing = lambda **kw: None  # type: ignore[assignment]

from core.teammate.engine import WorkforceEngine  # noqa: E402

GOAL = "/mission Build a tiny feature module: a Python function sub(a,b)"


def main() -> int:
    message = {
        "text": GOAL,
        "from": {"id": "612786480", "first_name": "Operator",
                 "username": "operator"},
        "chat": {"id": 612786480, "type": "private"},
    }
    result = tb.route_inbound_reply(message, pending_builds=[])
    print("handler action:", result.get("action"))
    print("handler reply :", (result.get("reply") or "")[:200])
    if result.get("action") != "control_command":
        print("RESULT: FAIL (not routed as a control command)")
        return 1

    mid = result["reply"].split("`")[1]
    print("mission id    :", mid)

    engine = WorkforceEngine()
    deadline = time.time() + 900
    status = None
    while time.time() < deadline:
        mission = engine.get_mission(mid)
        if mission and mission["status"] not in ("CREATED", "RUNNING"):
            status = mission["status"]
            break
        time.sleep(2)
    print("mission status:", status)

    audit = memory.load("command_bus_audit") or {}
    rows = [r for r in (audit.get("records") or []) if r.get("command") == "/mission"]
    print("audit entries :", len(rows))
    if rows:
        r = rows[-1]
        print("audit last    :", json.dumps(
            {k: r.get(k) for k in ("command", "source", "decision", "status", "risk")}))
    ok = (status == "COMPLETED" and rows and rows[-1].get("decision") == "allow")
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
