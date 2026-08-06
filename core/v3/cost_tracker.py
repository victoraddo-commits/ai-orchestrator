"""Kai V3 Cost Tracker — Per-pod runtime, cost, and task accounting.

Tracks GPU usage to maximize completed features per GPU dollar.
Metrics stored in memory/gpu_metrics.json via the memory API.
"""

from datetime import datetime, timezone

from core.memory import load, save
from core.logger import info

METRICS_FILE = "gpu_metrics.json"

POD_A = "qwen4"
POD_B = "qwen6"

POD_COST_PER_HOUR = {
    POD_A: 2.09,  # RTX PRO 6000 96GB
    POD_B: 2.09,  # RTX PRO 6000 96GB
}


def _init_metrics() -> dict:
    """Load or initialize the GPU metrics store."""
    metrics = load(METRICS_FILE)
    if not metrics:
        metrics = {
            "schema_version": 1,
            "pods": {},
            "daily_totals": {},
        }
    return metrics


def _save_metrics(metrics: dict):
    """Persist metrics to disk."""
    save(METRICS_FILE, metrics)


def record_startup(pod_name: str):
    """Record that a pod has started."""
    metrics = _init_metrics()
    pod_metrics = _ensure_pod_metrics(metrics, pod_name)
    pod_metrics["last_startup"] = datetime.now(timezone.utc).isoformat()
    pod_metrics["total_startups"] += 1
    _save_metrics(metrics)


def record_shutdown(pod_name: str):
    """Record that a pod has shut down."""
    metrics = _init_metrics()
    pod_metrics = _ensure_pod_metrics(metrics, pod_name)
    pod_metrics["last_shutdown"] = datetime.now(timezone.utc).isoformat()

    # Calculate runtime since last startup
    last_startup = pod_metrics.get("last_startup")
    if last_startup:
        try:
            start_dt = datetime.fromisoformat(last_startup)
            runtime = (datetime.now(timezone.utc) - start_dt).total_seconds()
            pod_metrics["total_runtime_seconds"] += runtime
        except (ValueError, TypeError):
            pass

    pod_metrics["total_shutdowns"] += 1
    _save_metrics(metrics)


def record_task_start(pod_name: str, task_id: str):
    """Record the start of a task on a pod."""
    metrics = _init_metrics()
    pod_metrics = _ensure_pod_metrics(metrics, pod_name)
    pod_metrics["active_since"] = datetime.now(timezone.utc).isoformat()
    pod_metrics["current_task"] = task_id
    _save_metrics(metrics)


def record_task_complete(pod_name: str, task_id: str,
                         runtime_seconds: float = 0.0,
                         success: bool = True):
    """Record the completion of a task on a pod."""
    metrics = _init_metrics()
    pod_metrics = _ensure_pod_metrics(metrics, pod_name)

    pod_metrics["total_tasks_completed"] += 1
    pod_metrics["total_active_seconds"] += runtime_seconds

    if success:
        pod_metrics["total_tasks_succeeded"] += 1
    else:
        pod_metrics["total_tasks_failed"] += 1

    pod_metrics["current_task"] = None
    pod_metrics["active_since"] = None

    # Update today's totals
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    daily = metrics.setdefault("daily_totals", {})
    today_metrics = daily.setdefault(today, {
        "tasks_completed": 0,
        "runtime_seconds": 0.0,
        "cost_usd": 0.0,
    })
    today_metrics["tasks_completed"] += 1
    today_metrics["runtime_seconds"] += runtime_seconds

    # Calculate cost: hours * hourly rate
    hours = runtime_seconds / 3600.0
    cost_per_hour = POD_COST_PER_HOUR.get(pod_name, 2.09)
    cost = hours * cost_per_hour
    today_metrics["cost_usd"] += cost
    pod_metrics["total_cost_usd"] += cost

    _save_metrics(metrics)


def record_idle_start(pod_name: str):
    """Record that a pod has entered idle state."""
    metrics = _init_metrics()
    pod_metrics = _ensure_pod_metrics(metrics, pod_name)
    pod_metrics["idle_since"] = datetime.now(timezone.utc).isoformat()
    _save_metrics(metrics)


def record_idle_end(pod_name: str):
    """Record that a pod has exited idle state."""
    metrics = _init_metrics()
    pod_metrics = _ensure_pod_metrics(metrics, pod_name)

    idle_since = pod_metrics.get("idle_since")
    if idle_since:
        try:
            idle_start = datetime.fromisoformat(idle_since)
            idle_seconds = (datetime.now(timezone.utc) - idle_start).total_seconds()
            pod_metrics["total_idle_seconds"] += round(idle_seconds, 1)
        except (ValueError, TypeError):
            pass

    pod_metrics["idle_since"] = None
    _save_metrics(metrics)


def get_cost_summary() -> dict:
    """Return a formatted cost summary for monitoring/dashboard use."""
    metrics = _init_metrics()
    pods = metrics.get("pods", {})
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    daily = metrics.get("daily_totals", {})

    summary = {}
    total_today = 0.0
    total_month = 0.0

    for pod_name, config in POD_COST_PER_HOUR.items():
        pod_metrics = pods.get(pod_name, {})
        runtime_s = pod_metrics.get("total_runtime_seconds", 0.0)
        runtime_h = runtime_s / 3600.0
        total_cost = runtime_h * config

        today_cost = daily.get(today, {}).get("cost_usd", 0.0)
        total_today += today_cost
        total_month += total_cost

        summary[pod_name] = {
            "runtime_hours": round(runtime_h, 2),
            "active_seconds": round(pod_metrics.get("total_active_seconds", 0.0), 1),
            "idle_seconds": round(pod_metrics.get("total_idle_seconds", 0.0), 1),
            "tasks_completed": pod_metrics.get("total_tasks_completed", 0),
            "tasks_succeeded": pod_metrics.get("total_tasks_succeeded", 0),
            "tasks_failed": pod_metrics.get("total_tasks_failed", 0),
            "total_cost_usd": round(total_cost, 2),
            "today_cost_usd": round(today_cost, 2),
            "startups": pod_metrics.get("total_startups", 0),
            "shutdowns": pod_metrics.get("total_shutdowns", 0),
            "avg_cost_per_task": (
                round(total_cost / pod_metrics["total_tasks_completed"], 4)
                if pod_metrics.get("total_tasks_completed")
                else 0.0
            ),
        }

    summary["totals"] = {
        "today_cost_usd": round(total_today, 2),
        "month_cost_usd": round(total_month, 2),
    }

    return summary


def get_pod_cost_breakdown(pod_name: str) -> dict:
    """Get detailed cost metrics for a single pod."""
    summary = get_cost_summary()
    return summary.get(pod_name, {})


def _ensure_pod_metrics(metrics: dict, pod_name: str) -> dict:
    """Ensure a pod has initialized metrics in the store."""
    pods = metrics.setdefault("pods", {})
    if pod_name not in pods:
        pods[pod_name] = {
            "total_runtime_seconds": 0.0,
            "total_active_seconds": 0.0,
            "total_idle_seconds": 0.0,
            "total_tasks_completed": 0,
            "total_tasks_succeeded": 0,
            "total_tasks_failed": 0,
            "total_cost_usd": 0.0,
            "total_startups": 0,
            "total_shutdowns": 0,
            "last_startup": None,
            "last_shutdown": None,
            "active_since": None,
            "idle_since": None,
            "current_task": None,
        }
    return pods[pod_name]
