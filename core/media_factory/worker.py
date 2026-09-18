"""Scheduler-callable media worker (§47).

``run_media_cycle()`` is self-throttled and fail-safe: it refuses to run more
often than ``MEDIA_CYCLE_MIN_INTERVAL`` seconds (persisted across restarts) and
never raises into the scheduler. Call it from the scheduler loop or manually.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Optional

from core.media_factory import config, engine

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_last_run_memory = 0.0


def _state_path() -> Path:
    return config.MEDIA_DATA_DIR / "worker_state.json"


def _read_last_run() -> float:
    global _last_run_memory
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        return float(data.get("last_run", 0.0))
    except (OSError, ValueError):
        return _last_run_memory


def _write_last_run(ts: float) -> None:
    global _last_run_memory
    _last_run_memory = ts
    try:
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"last_run": ts}), encoding="utf-8")
    except OSError as exc:  # noqa: BLE001 - state persistence is best-effort
        logger.warning("worker state write failed (%s)", type(exc).__name__)


def run_media_cycle(
    force: bool = False,
    *,
    geo: Optional[str] = None,
    create_content: bool = True,
) -> dict:
    """Run one media cycle unless throttled. Never raises."""
    with _lock:
        now = time.time()
        last = _read_last_run()
        interval = config.media_cycle_min_interval()
        if not force and (now - last) < interval:
            return {
                "status": "SKIPPED",
                "reason": "throttled",
                "seconds_until_next": round(interval - (now - last), 1),
            }
        _write_last_run(now)
    try:
        return engine.run_cycle(geo=geo, create_content=create_content)
    except Exception as exc:  # noqa: BLE001 - fail-safe for the scheduler
        logger.warning("media cycle failed (%s)", type(exc).__name__)
        return {"status": config.STATUS_FAILED, "error": f"{type(exc).__name__}: {exc}"}
