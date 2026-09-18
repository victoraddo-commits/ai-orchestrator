#!/usr/bin/env python3
"""Live proof for the §21/§52 store race: API and scheduler writers together.

Starts a scheduler-side writer process (``WorkforceEngine._put_mission`` on the
production memory dir) and, at the same instant, creates missions through the
live ``POST /api/missions`` endpoint. Then it verifies that **every** mission
from both writers is present in the store and removes the demo rows again.

Intended for the runner (LXC 111) where the API and its token live:

    PYTHONPATH=/opt/ai-orchestrator ./.venv/bin/python \
        scripts/kai_persistence_race_e2e.py

Demo missions are prefixed ``mis-race-sched-`` / the API's own ids and are
deleted at the end; no production mission is touched.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path

DEFAULT_MEMORY = Path(__file__).resolve().parent.parent / "memory"
DEFAULT_TOKEN = Path("/root/.ai-orchestrator/api_token")


def _scheduler_writer(memory_dir: str, count: int, start: "mp.synchronize.Event"):
    os.environ["AI_ORCHESTRATOR_MEMORY_DIR"] = memory_dir
    import core.memory as memory

    memory.MEMORY_DIR = Path(memory_dir)
    from core.teammate.engine import WorkforceEngine

    eng = WorkforceEngine()
    start.wait()
    for i in range(count):
        eng._put_mission({"id": f"mis-race-sched-{i}", "status": "CREATED",
                          "goal": "live persistence race (scheduler side)",
                          "tasks": [], "created_at": f"2026-01-01T00:00:{i % 60:02d}"})


def _api_writer(url: str, token: str, count: int) -> list[dict]:
    import requests
    import urllib3

    urllib3.disable_warnings()
    rows = []
    for i in range(count):
        r = requests.post(
            f"{url}/api/missions",
            headers={"Authorization": f"Bearer {token}"},
            json={"goal": f"Live persistence race API mission {i}",
                  "execute": False},
            verify=False, timeout=60)
        m = r.json()["mission"]
        rows.append({"mission_id": m["id"], "team_id": m["team_id"]})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--memory-dir", default=str(DEFAULT_MEMORY))
    ap.add_argument("--api-url", default="https://127.0.0.1:8000")
    ap.add_argument("--token-file", default=str(DEFAULT_TOKEN))
    ap.add_argument("--scheduler-count", type=int, default=30)
    ap.add_argument("--api-count", type=int, default=10)
    args = ap.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import core.memory as memory

    memory.MEMORY_DIR = Path(args.memory_dir)
    os.environ["AI_ORCHESTRATOR_MEMORY_DIR"] = args.memory_dir

    token = Path(args.token_file).read_text().strip()
    ctx = mp.get_context("fork")
    start = ctx.Event()
    proc = ctx.Process(target=_scheduler_writer,
                       args=(args.memory_dir, args.scheduler_count, start))
    proc.start()
    time.sleep(0.3)
    start.set()
    api_rows = _api_writer(args.api_url, token, args.api_count)
    proc.join(120)

    stored = memory.load("factory_missions.json").get("missions", {})
    sched = [f"mis-race-sched-{i}" for i in range(args.scheduler_count)]
    sp = sum(1 for s in sched if s in stored)
    ap = sum(1 for r in api_rows if r["mission_id"] in stored)
    print(f"scheduler-side missions persisted: {sp}/{args.scheduler_count}")
    print(f"API-side missions persisted:       {ap}/{args.api_count}")
    ok = sp == args.scheduler_count and ap == args.api_count
    print("result:", "PASS - none lost" if ok else "FAIL - loss detected")

    api_ids = {r["mission_id"] for r in api_rows}
    team_ids = {r["team_id"] for r in api_rows}

    def _clean_missions(data):
        ms = data.setdefault("missions", {})
        for k in list(ms):
            if k in api_ids or k in sched:
                ms.pop(k, None)
        return data

    def _clean_teams(data):
        ts = data.setdefault("teams", {})
        for k in list(ts):
            if k in team_ids:
                ts.pop(k, None)
        return data

    memory.update("factory_missions.json", _clean_missions)
    memory.update("workforce_teams.json", _clean_teams)
    print("cleanup: removed demo missions + teams")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
