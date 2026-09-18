#!/usr/bin/env python
"""§37 MODULE INTEGRATION — live cross-module end-to-end evidence.

A KAI module (juris-kai) requests a capability from KAI. The unified workforce
reuses/creates a teammate, runs a mission through the Model Fabric, and the
outcome is written back into the module's Second Brain record. This script is
the live counterpart to tests/test_module_integration.py (which mocks the model).

Run on LXC 111:
    cd /opt/ai-orchestrator && .venv/bin/python scripts/kai_module_integration_e2e.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _capture(bus, pattern):
    events = []
    if bus is not None:
        bus.subscribe(pattern, lambda topic, envelope: events.append(envelope))
    return events


def main() -> int:
    from core.integration.module_bridge import get_bridge

    bridge = get_bridge()
    bus = bridge.bus
    module_events = _capture(bus, "module.*")
    teammate_events = _capture(bus, "teammate.*")
    mission_events = _capture(bus, "mission.*")

    result = bridge.request_capability(
        "juris-kai", "legal_research",
        objective=(
            "In two sentences, explain the doctrine of consideration in Ghana "
            "contract law and name the statute that governs contracts in Ghana."),
        execute=True)

    mission = bridge.engine.get_mission(result["mission_id"])
    entry = bridge.get_request(result["mission_id"])

    summary = {
        "module": result["module"],
        "capability": result["capability"],
        "specialization": result["specialization"],
        "skills": result["skills"],
        "mission_id": result["mission_id"],
        "mission_status": mission["status"],
        "teammate_id": result["teammate_id"],
        "teammate_created": result["created"],
        "tasks": [
            {"skill_id": t["skill_id"], "status": t["status"],
             "teammate_id": t.get("teammate_id"),
             "output": (t.get("output") or "")[:400]}
            for t in (mission.get("tasks") or [])
        ],
        "verification": mission.get("verification"),
        "second_brain_record_id": (entry or {}).get("second_brain_record_id"),
        "module_journal_status": (entry or {}).get("status"),
        "events": [
            {"topic": e["topic"], "source": e.get("source"),
             "payload": e.get("payload")}
            for e in (module_events + teammate_events + mission_events)
        ],
    }

    print(json.dumps(summary, indent=2))
    out = Path("reports") / "kai_module_integration_evidence.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2))

    ok = mission["status"] == "COMPLETED"
    print(f"\nEVIDENCE WRITTEN: {out}")
    print(f"CROSS-MODULE PATH: {'COMPLETED' if ok else mission['status']}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
