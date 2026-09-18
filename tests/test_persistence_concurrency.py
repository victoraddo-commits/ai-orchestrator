"""Cross-process persistence safety (directive §21/§52).

Live incident (2026-09-18): a concurrently running ``kai-scheduler`` and the
API process both load-modify-save ``memory/factory_missions.json``; the slower
writer clobbers the other's missions, so missions vanish from the engine store.

These tests simulate concurrent writers in SEPARATE PROCESSES against one
isolated memory dir and assert that no writer's records are lost.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import threading


# ── helpers ─────────────────────────────────────────────────────────────────
def _unwrap(raw: dict) -> dict:
    """memory_manager.write wraps payloads as {schema_version, records}."""
    if isinstance(raw, dict) and "records" in raw and "schema_version" in raw:
        return raw["records"]
    return raw


def _read_store(mem_dir: str, name: str) -> dict:
    with open(os.path.join(mem_dir, name)) as f:
        return _unwrap(json.load(f))


def _mission_writer(mem_dir, prefix, count, barrier):
    os.environ["AI_ORCHESTRATOR_MEMORY_DIR"] = mem_dir
    from pathlib import Path
    import core.memory as memory
    memory.MEMORY_DIR = Path(mem_dir)
    from core.teammate.engine import WorkforceEngine

    eng = WorkforceEngine.__new__(WorkforceEngine)
    eng._lock = threading.RLock()
    barrier.wait()
    for i in range(count):
        eng._put_mission({
            "id": f"{prefix}-{i}", "status": "CREATED", "goal": prefix,
            "tasks": [], "created_at": f"2026-01-01T00:00:{i % 60:02d}",
        })


def _team_writer(mem_dir, prefix, count, barrier):
    os.environ["AI_ORCHESTRATOR_MEMORY_DIR"] = mem_dir
    from pathlib import Path
    from types import SimpleNamespace
    import core.memory as memory
    memory.MEMORY_DIR = Path(mem_dir)
    from core.teammate.engine import WorkforceEngine

    eng = WorkforceEngine.__new__(WorkforceEngine)
    eng._lock = threading.RLock()
    eng.runtime = SimpleNamespace(bus=None)
    barrier.wait()
    for i in range(count):
        eng._persist_team(f"{prefix}-{i}", "requirement", None,
                          SimpleNamespace(specializations=["coder"],
                                          to_dict=lambda: {}),
                          [])


def _teammate_writer(mem_dir, prefix, count, barrier):
    os.environ["AI_ORCHESTRATOR_MEMORY_DIR"] = mem_dir
    from pathlib import Path
    import core.memory as memory
    memory.MEMORY_DIR = Path(mem_dir)
    from core.teammate.registry import TeammateRegistry

    registry = TeammateRegistry()
    barrier.wait()
    for i in range(count):
        registry.create({"name": f"{prefix}-{i}", "specialization": "coder"})


def _run(procs, timeout=60):
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout)
        assert p.exitcode == 0, f"writer exited {p.exitcode}"


# ── tests ───────────────────────────────────────────────────────────────────
def test_concurrent_mission_writers_none_lost(tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir(exist_ok=True)
    ctx = mp.get_context("fork")
    n_proc, n_each = 4, 60
    barrier = ctx.Barrier(n_proc)
    procs = [ctx.Process(target=_mission_writer,
                         args=(str(mem), f"p{i}", n_each, barrier))
             for i in range(n_proc)]
    _run(procs)

    missions = _read_store(str(mem), "factory_missions.json")["missions"]
    assert len(missions) == n_proc * n_each
    for i in range(n_proc):
        assert all(f"p{i}-{j}" in missions for j in range(n_each))


def test_concurrent_team_writers_none_lost(tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir(exist_ok=True)
    ctx = mp.get_context("fork")
    n_proc, n_each = 4, 25
    barrier = ctx.Barrier(n_proc)
    procs = [ctx.Process(target=_team_writer,
                         args=(str(mem), f"t{i}", n_each, barrier))
             for i in range(n_proc)]
    _run(procs)

    teams = _read_store(str(mem), "workforce_teams.json")["teams"]
    assert len(teams) == n_proc * n_each
    for i in range(n_proc):
        assert all(f"t{i}-{j}" in teams for j in range(n_each))


def test_concurrent_teammate_creates_none_lost(tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir(exist_ok=True)
    ctx = mp.get_context("fork")
    n_proc, n_each = 4, 10
    barrier = ctx.Barrier(n_proc)
    procs = [ctx.Process(target=_teammate_writer,
                         args=(str(mem), f"m{i}", n_each, barrier))
             for i in range(n_proc)]
    _run(procs)

    mates = _read_store(str(mem), "teammates.json")["teammates"]
    assert len(mates) == n_proc * n_each
    names = {m["name"] for m in mates.values()}
    for i in range(n_proc):
        assert all(f"m{i}-{j}" in names for j in range(n_each))
