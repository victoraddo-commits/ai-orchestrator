"""Kai V3 Orchestration Cycle — The new pipeline loop.

Replaces core/orchestrator_cycle.py.

New pipeline order:
  Roadmap Compile → GPU Lifecycle → Worker Assignment → Builds →
  Reviews → Deploy → Verify → Archive → Next Task

Each cycle:
  1. Compile roadmap into executable tasks
  2. Manage GPU lifecycle (start/stop pods as needed)
  3. Spawn new builds for ready phases
  4. Advance builds through the pipeline (two-pass)
  5. Detect and handle stuck/timeout builds
  6. Check GPU health
  7. Report cycle results
"""

import time
from datetime import datetime, timezone

from core.logger import info, error as log_error

from core.v3.roadmap_compiler import compile_roadmap, get_pending_tasks_grouped
from core.v3.roadmap_manager import advance_roadmap, get_roadmap_status
from core.v3.build_manager import advance_builds, load_builds, TERMINAL_BUILD_STATUSES
from core.v3.gpu_manager import (
    get_all_pod_states, get_all_health_status,
    check_health, should_start_pod, should_stop_pod,
    start_pod, stop_pod, POD_A, POD_B,
)
from core.v3.cost_tracker import record_startup, record_shutdown, get_cost_summary
from core.v3.worker_pool import init_workers, get_all_workers, get_pod_worker_summary
from core.v3.monitoring import (
    start_cycle, end_cycle, update_pipeline_counts,
    update_gpu_status, update_worker_status, format_cycle_report,
    check_stuck_builds,
)
from core.v3.recovery import get_recovery_queue

# Cycle counter
_cycle_number: int = 0


def run_cycle() -> dict:
    """Execute one complete orchestration cycle.

    Returns:
        {
            "cycle": int,
            "duration_s": float,
            "builds": list,
            "roadmap_progress": dict,
            "gpu_status": dict,
            "cost_summary": dict,
            "recovery_queue": list,
        }
    """
    global _cycle_number
    _cycle_number += 1

    start_cycle()
    info(f"=== Kai V3 Cycle #{_cycle_number} started ===")

    # ── 1. Initialize worker pool (first cycle only) ──
    if _cycle_number == 1:
        init_workers()

    # ── 2. Compile roadmap → DAG + priority queue ──
    try:
        compiled = compile_roadmap()
        pending = get_pending_tasks_grouped(compiled)
        info(f"Roadmap compiled: {len(compiled.get('tasks', []))} ready, "
             f"{len(compiled.get('blocked', []))} blocked")
    except Exception as e:
        log_error(f"Roadmap compile failed: {type(e).__name__}: {e}")
        compiled = {"tasks": [], "blocked": [], "completed": [], "failed": []}
        pending = {"GENERATING": [], "CODE_REVIEW": [], "DEPLOYING": [], "BLOCKED": []}

    # ── 3. GPU lifecycle management ──
    _manage_gpu_lifecycle(pending)

    # ── 4. Spawn new builds for ready phases ──
    roadmap_progress = advance_roadmap()

    # ── 5. Advance builds through pipeline (two-pass) ──
    builds = load_builds()
    build_summary = advance_builds()

    # Re-load builds after advancement
    all_builds = load_builds(include_terminal=True)
    builds = [b for b in all_builds
              if b.get("status") not in TERMINAL_BUILD_STATUSES]

    # ── 6. GPU health checks ──
    _check_gpu_health()

    # ── 7. Update monitoring ──
    update_pipeline_counts(all_builds)
    update_gpu_status(get_all_health_status())
    update_worker_status(get_all_workers())

    # ── 8. Check recovery queue ──
    recovery_queue = get_recovery_queue()

    # ── 9. Report ──
    duration_s = end_cycle()
    cost_summary = get_cost_summary()

    from core.v3.monitoring import report_cycle
    report = report_cycle(
        _cycle_number, duration_s,
        builds_completed=build_summary.get("completed", 0),
        builds_failed=build_summary.get("failed", 0),
        auto_failed=build_summary.get("auto_failed", []),
    )

    result = {
        "cycle": _cycle_number,
        "duration_s": round(duration_s, 1),
        "builds": builds,
        "build_summary": build_summary,
        "roadmap_progress": roadmap_progress,
        "roadmap_status": get_roadmap_status(),
        "gpu_status": get_all_pod_states(),
        "cost_summary": cost_summary,
        "recovery_queue": recovery_queue,
        "report": report,
    }

    info(f"=== Kai V3 Cycle #{_cycle_number} completed "
         f"({duration_s:.1f}s, {build_summary.get('completed', 0)} completed, "
         f"{build_summary.get('failed', 0)} failed) ===")

    return result


def _manage_gpu_lifecycle(pending: dict[str, list]):
    """Manage GPU pod lifecycle based on pending task queues.

    - Start Pod A if GENERATING queue > 0 and no generator available
    - Start Pod B if CODE_REVIEW or DEPLOYING queue > 0 and Pod B offline
    - Stop idle pods after timeout
    """
    pod_states = get_all_pod_states()

    # Pod A (Generator) — start if needed
    if should_start_pod(POD_A, {
        "GENERATING": len(pending.get("GENERATING", [])),
    }):
        info(f"Starting {POD_A} (GENERATING queue: "
             f"{len(pending.get('GENERATING', []))})")
        start_pod(POD_A)
        record_startup(POD_A)

    # Pod B (Reviewer) — start if needed
    if should_start_pod(POD_B, {
        "CODE_REVIEW": len(pending.get("CODE_REVIEW", [])),
        "DEPLOYING": len(pending.get("DEPLOYING", [])),
    }):
        info(f"Starting {POD_B} (REVIEW queue: "
             f"{len(pending.get('CODE_REVIEW', []))}, "
             f"DEPLOYING queue: {len(pending.get('DEPLOYING', []))})")
        start_pod(POD_B)
        record_startup(POD_B)

    # Pod A — stop if idle too long
    if should_stop_pod(POD_A):
        info(f"Stopping {POD_A} (idle timeout)")
        stop_pod(POD_A)
        record_shutdown(POD_A)

    # Pod B — stop if idle too long
    if should_stop_pod(POD_B):
        info(f"Stopping {POD_B} (idle timeout)")
        stop_pod(POD_B)
        record_shutdown(POD_B)


def _check_gpu_health():
    """Check health of all pods in READY/BUSY/HEALTH_CHECK states."""
    pod_states = get_all_pod_states()

    for pod_name, state in pod_states.items():
        status = state.get("status", "OFFLINE")
        if status in {"HEALTH_CHECK", "READY", "BUSY"}:
            try:
                healthy = check_health(pod_name)
                if not healthy and status in {"READY", "BUSY"}:
                    log_error(f"{pod_name} health check failed — pod marked offline")
            except Exception as e:
                log_error(f"{pod_name} health check error: {type(e).__name__}")
