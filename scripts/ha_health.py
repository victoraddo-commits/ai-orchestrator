#!/usr/bin/env python3
"""HA health probe for KAI — verifies HA + its integrations, honestly.

Reports reachable/unreachable and the loaded integration set; never invents
health. Exit non-zero when HA is down so a timer/monitor can alert.
"""
from __future__ import annotations

import json
import sys
import urllib.request

sys.path.insert(0, "/opt/ai-orchestrator")

BASE = "http://192.168.1.115:8123"


def main() -> int:
    try:
        from core.ai.credential_vault import retrieve_api_key
        tok = retrieve_api_key("homeassistant") or ""
    except Exception as e:  # noqa: BLE001
        print("no HA token:", e)
        return 2
    try:
        req = urllib.request.Request(BASE + "/api/config",
                                     headers={"Authorization": "Bearer " + tok})
        cfg = json.load(urllib.request.urlopen(req, timeout=12))
        req2 = urllib.request.Request(BASE + "/api/states",
                                      headers={"Authorization": "Bearer " + tok})
        states = json.load(urllib.request.urlopen(req2, timeout=20))
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"reachable": False, "error": type(e).__name__}))
        return 1
    comps = cfg.get("components", [])
    print(json.dumps({
        "reachable": True,
        "version": cfg.get("version"),
        "entities": len(states),
        "integrations": [c for c in ("tuya", "sonoff") if c in comps],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
