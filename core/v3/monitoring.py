"""Kai V3 Monitoring — Health monitoring and per-cycle observability.

Monitors vLLM endpoint, pod availability, worker health, stuck builds,
and provider quality. Produces a formatted report every cycle.
"""

import time
from datetime import datetime, timezone
from collections import Counter

from core.logger import info, error as log_error

# Timing
CYCLE_START_TIME: float | None = None

# Pipeline counts
_pipeline_counts: dict[str, int] = {}
_gpu_status: dict = {}
_worker_status: list[dict] = []


def start_cycle():
    """Mark the start of a monitoring cycle."""
    global CYCLE_START_TIME
    CYCLE_START_TIME = time.time()


def end_cycle() -> float:
    """End the current cycle and return duration in seconds."""
    global CYCLE_START_TIME
    if CYCLE_START_TIME is None:
        return 0.0
    duration = time.time() - CYCLE_START_TIME
    CYCLE_START_TIME = None
    return duration


def update_pipeline_counts(builds: list[dict]):
    """Update pipeline status counts from the current builds list."""
    global _pipeline_counts
    counts = Counter(b.get("status", "UNKNOWN") for b in builds)

    _pipeline_counts = {
        "planning": counts.get("PLANNING", 0),
        "generating": counts.get("GENERATING", 0),
        "review": (counts.get("CODE_REVIEW", 0) +
                   counts.get("SECURITY_REVIEW", 0) +
                   counts.get("TESTING", 0)),
        "testing": counts.get("TESTING", 0),
        "deploying": counts.get("DEPLOYING", 0),
        "completed": counts.get("COMPLETED", 0),
        "failed": counts.get("FAILED", 0),
        "blocked": (counts.get("WAITING_FOR_USER_INPUT", 0) +
                    counts.get("WAITING_FOR_ARCHITECTURE_APPROVAL", 0) +
                    counts.get("WAITING_FOR_DEPLOY_APPROVAL", 0)),
        "total": len(builds),
    }


def update_gpu_status(gpu_manager_state: dict):
    """Update GPU status from gpu_manager state."""
    global _gpu_status
    _gpu_status = gpu_manager_state


def update_worker_status(workers: list[dict]):
    """Update worker status."""
    global _worker_status
    _worker_status = workers


def get_pipeline_counts() -> dict:
    """Get current pipeline status counts."""
    return dict(_pipeline_counts)


def get_gpu_status() -> dict:
    """Get current GPU status."""
    return dict(_gpu_status)


def get_worker_status() -> list[dict]:
    """Get current worker status."""
    return list(_worker_status)


def check_stuck_builds(builds: list[dict],
                       generation_timeout: int = 2400,
                       deploying_timeout: int = 1800) -> list[dict]:
    """Detect builds stuck in GENERATING or DEPLOYING beyond their timeout.

    Returns a list of stuck build IDs with their details.
    """
    stuck = []
    now = datetime.now(timezone.utc)

    for build in builds:
        status = build.get("status", "")
        updated = build.get("updated", "")

        if not updated:
            continue

        try:
            updated_dt = datetime.fromisoformat(updated)
            elapsed = (now - updated_dt).total_seconds()
        except (ValueError, TypeError):
            continue

        if status == "GENERATING" and elapsed > generation_timeout:
            stuck.append({
                "build_id": build.get("id", ""),
                "name": build.get("name", ""),
                "status": status,
                "elapsed_s": round(elapsed, 1),
                "timeout_s": generation_timeout,
                "reason": "Generation timeout",
            })

        elif status == "DEPLOYING" and elapsed > deploying_timeout:
            stuck.append({
                "build_id": build.get("id", ""),
                "name": build.get("name", ""),
                "status": status,
                "elapsed_s": round(elapsed, 1),
                "timeout_s": deploying_timeout,
                "reason": "Deployment timeout",
            })

    if stuck:
        for s in stuck:
            log_error(f"STUCK BUILD: {s['name']} [{s['status']}] "
                      f"stuck for {s['elapsed_s']}s (timeout: {s['timeout_s']}s)")

    return stuck


def format_cycle_report(cycle_number: int,
                        duration_s: float,
                        builds_completed: int = 0,
                        builds_failed: int = 0,
                        auto_failed: list[dict] | None = None) -> str:
    """Format a human-readable cycle report for logs and dashboards.

    The format matches what Kai Command Center should display.
    """
    lines = [f"=== Kai V3 Cycle #{cycle_number} "
             f"(duration: {round(duration_s, 1)}s) ==="]

    # Pipeline
    pc = _pipeline_counts
    lines.append(
        f"  Pipeline: planning={pc.get('planning', 0)} "
        f"generating={pc.get('generating', 0)} "
        f"review={pc.get('review', 0)} "
        f"testing={pc.get('testing', 0)} "
        f"deploying={pc.get('deploying', 0)} "
        f"completed={pc.get('completed', 0)} "
        f"failed={pc.get('failed', 0)} "
        f"blocked={pc.get('blocked', 0)}"
    )

    # GPU
    for pod_name, pod_state in _gpu_status.items():
        status = pod_state.get("status", "?")
        healthy = pod_state.get("healthy", False)
        task = pod_state.get("current_task", "")
        task_short = task[:12] if task else "none"
        lines.append(
            f"  GPU {pod_name}: {status} "
            f"(healthy={healthy}, task={task_short})"
        )

    # Workers
    if _worker_status:
        active = [w for w in _worker_status if w.get("status") == "BUSY"]
        idle = [w for w in _worker_status if w.get("status") == "IDLE"]
        lines.append(f"  Workers: {len(active)} busy, {len(idle)} idle")
        for w in active[:5]:  # Top 5
            lines.append(
                f"    {w.get('role', '?')} on {w.get('pod', '?')} "
                f"-> {w.get('task', '?')}"
            )

    # Completion
    lines.append(
        f"  Results: {builds_completed} completed, "
        f"{builds_failed} failed, "
        f"{len(auto_failed or [])} auto-failed"
    )

    # Cost
    from core.v3.cost_tracker import get_cost_summary
    costs = get_cost_summary()
    for pod_name in ["qwen4", "qwen6"]:
        pod_costs = costs.get(pod_name, {})
        lines.append(
            f"  Cost {pod_name}: ${pod_costs.get('today_cost_usd', 0):.2f} today, "
            f"${pod_costs.get('total_cost_usd', 0):.2f} total, "
            f"{pod_costs.get('tasks_completed', 0)} tasks"
        )

    lines.append(f"  Total today: ${costs.get('totals', {}).get('today_cost_usd', 0):.2f}")

    return "\n".join(lines)


def report_cycle(cycle_number: int,
                 duration_s: float,
                 builds_completed: int = 0,
                 builds_failed: int = 0,
                 auto_failed: list[dict] | None = None):
    """Log the cycle report and return it as a string."""
    report = format_cycle_report(
        cycle_number, duration_s,
        builds_completed, builds_failed, auto_failed,
    )
    info(report)
    return report
