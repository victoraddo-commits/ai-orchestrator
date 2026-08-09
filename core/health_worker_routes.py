"""Kai Health Worker — FastAPI router.

Part of: Kai Mobile Command Node — Sub-project 4: Permanent Health Worker.

Exposes health trends, anomalies, baselines, forecasts, and health score
to the dashboard, mobile Command Center, and external consumers.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health-worker"])


# ---------------------------------------------------------------------------
# Health worker status
# ---------------------------------------------------------------------------


@router.get("/kai/health/status")
async def api_health_worker_status():
    """Get health worker status: running state, sample count, DB stats.

    Reads from a shared state file written by the health worker daemon thread
    in the scheduler process — the API and scheduler run in separate processes
    so in-memory state isn't accessible here.
    """
    import json
    import os
    from pathlib import Path
    from core.health_observatory import get_snapshot_stats

    stats = get_snapshot_stats()

    # Read worker liveness from shared state file
    worker_state = {"running": False, "sample_count": 0, "last_sample": None}
    try:
        memory_dir = os.environ.get("AI_ORCHESTRATOR_MEMORY_DIR", "memory")
        state_path = Path(memory_dir) / "health_worker_state.json"
        if state_path.exists():
            raw = state_path.read_text()
            worker_state.update(json.loads(raw))
    except Exception:
        pass

    # Worker is "running" if state file was updated within the last 90s
    # (30s interval + generous margin)
    from datetime import datetime, timezone
    running = False
    if worker_state.get("last_sample"):
        try:
            last = datetime.fromisoformat(worker_state["last_sample"])
            age = (datetime.now(timezone.utc) - last).total_seconds()
            running = age < 90
        except Exception:
            pass

    return {
        "worker": {
            "running": running,
            "sample_count": worker_state.get("sample_count", 0),
            "last_sample": worker_state.get("last_sample"),
        },
        "database": stats,
    }


# ---------------------------------------------------------------------------
# Health score
# ---------------------------------------------------------------------------


@router.get("/kai/health/score")
async def api_health_score(node: str = Query("localhost", description="Node name")):
    """Get composite health score (0-100) with component breakdown."""
    from core.health_observatory import get_health_score

    result = get_health_score(node=node)
    return result


# ---------------------------------------------------------------------------
# Metrics (time-series)
# ---------------------------------------------------------------------------


@router.get("/kai/health/metrics")
async def api_health_metrics(
    metric: Optional[str] = Query(None, description="Filter by metric name (e.g. host_cpu_pct)"),
    node: str = Query("localhost", description="Node name"),
    minutes: int = Query(60, ge=1, le=1440, description="Time window in minutes"),
):
    """Get recent health metric time-series data."""
    from core.health_observatory import get_recent_metrics

    return {
        "metrics": get_recent_metrics(metric=metric, node=node, minutes=minutes),
    }


# ---------------------------------------------------------------------------
# Trends
# ---------------------------------------------------------------------------


@router.get("/kai/health/trends/{metric}")
async def api_health_trend(
    metric: str,
    node: str = Query("localhost", description="Node name"),
    window_hours: int = Query(6, ge=1, le=168, description="Trend window in hours"),
):
    """Get trend analysis for a specific metric (slope, r², forecast, direction)."""
    from core.health_observatory import get_trend

    return get_trend(metric=metric, node=node, window_hours=window_hours)


# ---------------------------------------------------------------------------
# Forecasts
# ---------------------------------------------------------------------------


@router.get("/kai/health/forecasts")
async def api_health_forecasts(
    node: str = Query("localhost", description="Node name"),
):
    """Get resource exhaustion forecasts for disk, memory, and CPU."""
    from core.health_observatory import get_forecast

    return {"forecasts": get_forecast(node=node)}


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------


@router.get("/kai/health/baselines")
async def api_health_baselines(
    node: Optional[str] = Query(None, description="Filter by node"),
):
    """Get learned metric baselines (mean, stddev, sample count)."""
    from core.health_observatory import get_baselines

    return {"baselines": get_baselines(node=node)}


# ---------------------------------------------------------------------------
# Anomalies
# ---------------------------------------------------------------------------


@router.get("/kai/health/anomalies")
async def api_health_anomalies(
    acked: Optional[bool] = Query(None, description="Filter by ack status"),
    limit: int = Query(50, ge=1, le=500, description="Max results"),
):
    """Get detected health anomalies, newest first."""
    from core.health_observatory import get_anomalies

    return {"anomalies": get_anomalies(acked=acked, limit=limit)}


@router.post("/kai/health/anomalies/{anomaly_id}/ack")
async def api_ack_health_anomaly(anomaly_id: int):
    """Acknowledge a health anomaly."""
    from core.health_observatory import ack_anomaly

    ok = ack_anomaly(anomaly_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Anomaly {anomaly_id} not found")
    return {"ok": True}


@router.post("/kai/health/anomalies/ack-all")
async def api_ack_all_health_anomalies():
    """Acknowledge all health anomalies."""
    from core.health_observatory import ack_all_anomalies

    count = ack_all_anomalies()
    logger.info("All health anomalies acknowledged (%d total)", count)
    return {"ok": True, "count": count}
