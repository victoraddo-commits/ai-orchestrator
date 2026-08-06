"""Kai V3 Roadmap Manager — Phase-to-build lifecycle with compiler integration.

Replaces core/roadmap_manager.py.

Key features:
  - Compiler integration: roadmap → DAG → priority queue → builds
  - Dedup: same phase + non-terminal build → reuse
  - Dependency tracking: phases only spawn when deps are completed
  - Build recovery: recoverable builds (non-terminal) are resumed
  - Phase validation: validates phase before creating build
  - Stale reference protection: missing build → auto-fail phase
"""

import json
from pathlib import Path
from datetime import datetime, timezone

from core.memory import load, save, update
from core.logger import info, error as log_error

from core.v3.roadmap_compiler import (
    compile_roadmap, get_next_task, is_phase_ready,
    get_pending_tasks_grouped, NON_TERMINAL_PHASE_STATUSES,
)
from core.v3.build_manager import (
    create_build, load_builds, get_build, fail_build,
    NON_TERMINAL_BUILD_STATUSES, TERMINAL_BUILD_STATUSES,
)

ROADMAP_FILE = "roadmap.json"

DEFAULT_MAX_SPAWN_PER_CYCLE = 3  # Max new builds per cycle


def advance_roadmap(max_spawn: int | None = None) -> dict:
    """Advance the roadmap: compile, spawn new builds, validate existing.

    1. Compile roadmap → DAG + priority queue
    2. Spawn new builds for ready phases (up to max_spawn per cycle)
    3. Validate existing builds against roadmap phases (stale reference check)
    4. Return summary

    Returns:
        {spawned: [build_ids], failed: [phase_ids], compiled: True/False}
    """
    spawn_limit = max_spawn or DEFAULT_MAX_SPAWN_PER_CYCLE
    result = {
        "spawned": [],
        "failed": [],
        "errors": [],
        "compiled": True,
    }

    # 1. Compile
    try:
        compiled = compile_roadmap()
        result["compiled"] = True
        result["ready_count"] = len(compiled.get("tasks", []))
        result["blocked_count"] = len(compiled.get("blocked", []))
    except Exception as e:
        log_error(f"Roadmap compilation failed: {type(e).__name__}: {e}")
        result["compiled"] = False
        result["errors"].append(f"Compilation failed: {e}")
        return result

    # 2. Validate existing builds (stale reference check)
    _validate_existing_builds(compiled, result)

    # 3. Spawn new builds
    active_builds = load_builds()
    active_phase_ids = {
        b.get("phase_id", "") for b in active_builds
        if b.get("status") in NON_TERMINAL_BUILD_STATUSES
    }

    spawned = 0
    for task in compiled.get("tasks", []):
        if spawned >= spawn_limit:
            break

        phase_id = task.phase_id

        # Skip if already has an active build
        if phase_id in active_phase_ids:
            continue

        # Skip if dependencies not met (shouldn't happen — compiler filters — but belt-and-suspenders)
        if not is_phase_ready(compiled, phase_id):
            continue

        # Check if a recoverable build exists
        existing = _find_recoverable_build(phase_id)
        if existing:
            info(f"Recovering existing build {existing['id'][:12]} for phase {phase_id}")
            result["spawned"].append(existing["id"])
            spawned += 1
            active_phase_ids.add(phase_id)
            continue

        # Create new build
        try:
            phase = _get_phase(phase_id)
            if not phase:
                log_error(f"Phase {phase_id} not found in roadmap")
                result["errors"].append(f"Phase {phase_id} missing from roadmap")
                continue

            build = create_build(phase)
            info(f"Spawned build {build['id'][:12]} for phase {phase_id} "
                 f"'{phase.get('name', '?')}'")
            result["spawned"].append(build["id"])
            spawned += 1

        except Exception as e:
            log_error(f"Failed to create build for phase {phase_id}: "
                      f"{type(e).__name__}: {e}")
            result["errors"].append(f"Build creation failed for {phase_id}: {e}")

    info(f"Roadmap advance: {spawned} spawned, "
         f"{len(result['failed'])} failed, "
         f"{len(result['errors'])} errors")

    return result


def get_roadmap_status() -> dict:
    """Get a comprehensive roadmap status summary.

    Includes DAG, queue depths, blocked phases, and pipeline status.
    """
    compiled = compile_roadmap()
    pending = get_pending_tasks_grouped(compiled)

    return {
        "total_phases": len(compiled.get("dag", {})),
        "completed": len(compiled.get("completed", [])),
        "failed": len(compiled.get("failed", [])),
        "ready": len(compiled.get("tasks", [])),
        "blocked": len(compiled.get("blocked", [])),
        "queue_depths": {
            "generating": len(pending.get("GENERATING", [])),
            "code_review": len(pending.get("CODE_REVIEW", [])),
            "deploying": len(pending.get("DEPLOYING", [])),
            "blocked": len(pending.get("BLOCKED", [])),
        },
        "top_blocked": [
            {
                "phase_id": t.phase_id,
                "name": t.name,
                "description": t.description,
                "score": t.score,
            }
            for t in compiled.get("blocked", [])[:10]
        ],
        "next_ready": [
            {
                "phase_id": t.phase_id,
                "name": t.name,
                "priority": t.priority,
                "score": t.score,
            }
            for t in compiled.get("tasks", [])[:10]
        ],
    }


def mark_phase_completed(phase_id: str):
    """Mark a roadmap phase as completed."""
    _update_phase_status(phase_id, "completed")


def mark_phase_failed(phase_id: str, reason: str = ""):
    """Mark a roadmap phase as failed with an optional reason."""
    _update_phase_status(phase_id, "failed", reason=reason)


def get_dependency_chain(phase_id: str) -> dict:
    """Get the full dependency chain for a phase: what it depends on and what depends on it."""
    compiled = compile_roadmap()
    dag = compiled.get("dag", {})

    node = dag.get(phase_id)
    if not node:
        return {"depends_on": [], "depended_by_by": [], "ready": False}

    # What depends on this phase
    depended_by = []
    for pid, n in dag.items():
        if phase_id in n.dependencies:
            depended_by.append({
                "phase_id": pid,
                "name": n.name,
                "status": n.status,
            })

    return {
        "phase_id": phase_id,
        "name": node.name,
        "status": node.status,
        "priority": node.priority,
        "depth": node.depth,
        "depends_on": [
            {"phase_id": d, "status": dag.get(d, {}).status if d in dag else "unknown"}
            for d in node.dependencies
        ],
        "depended_by": depended_by,
        "ready": is_phase_ready(compiled, phase_id),
    }


def _validate_existing_builds(compiled: dict, result: dict):
    """Check existing builds against roadmap phases.

    If a build references a phase that's no longer in the roadmap,
    or the build's phase ID doesn't match any roadmap phase,
    fail it with a stale reference reason.
    """
    builds = load_builds(include_terminal=True)
    dag = compiled.get("dag", {})

    for build in builds:
        phase_id = build.get("phase_id", "")
        build_status = build.get("status", "")

        # Only check non-terminal builds
        if build_status in TERMINAL_BUILD_STATUSES:
            continue

        if not phase_id:
            continue

        # Check: does the phase still exist in the roadmap?
        if phase_id not in dag:
            reason = (f"Build {build['id'][:12]} references missing phase {phase_id}. "
                      f"Stale reference — phase deleted from roadmap.")
            log_error(reason)
            fail_build(build, reason)
            result["failed"].append(phase_id)

        # Check: is the phase in a terminal state?
        elif dag[phase_id].status in ("failed", "rolled_back"):
            reason = (f"Phase {phase_id} is {dag[phase_id].status} in roadmap. "
                      f"Build stale — phase already terminated.")
            log_error(reason)
            fail_build(build, reason)
            result["failed"].append(phase_id)


def _find_recoverable_build(phase_id: str) -> dict | None:
    """Find a non-terminal build for a phase that can be recovered."""
    builds = load_builds(include_terminal=True)
    for b in builds:
        if b.get("phase_id") == phase_id:
            if b.get("status") not in TERMINAL_BUILD_STATUSES:
                return b
    return None


def _get_phase(phase_id: str) -> dict | None:
    """Get a roadmap phase by ID."""
    data = load(ROADMAP_FILE)
    phases = data.get("phases", data.get("records", [])) if isinstance(data, dict) else (data or [])
    for p in phases:
        if p.get("id") == phase_id:
            return p
    return None


def _update_phase_status(phase_id: str, new_status: str, reason: str = ""):
    """Update a phase's status in the roadmap."""
    def _update(data):
        phases = data.get("phases", data.get("records", []))
        for p in phases:
            if p.get("id") == phase_id:
                p["status"] = new_status
                p["updated"] = datetime.now(timezone.utc).isoformat()
                if reason:
                    p["status_reason"] = reason
                break
        return data

    try:
        update(ROADMAP_FILE, _update)
        info(f"Phase {phase_id} → {new_status}" + (f": {reason}" if reason else ""))
    except Exception as e:
        log_error(f"Failed to update phase {phase_id} status: {e}")
