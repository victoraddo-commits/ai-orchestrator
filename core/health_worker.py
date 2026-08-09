"""Kai Health Worker — permanent background health sampling thread.

Part of: Kai Mobile Command Node — Sub-project 4: Permanent Health Worker.

The HealthWorker runs as a daemon thread within the scheduler process,
sampling system health every 30 seconds independently of the orchestrator
cycle.  This means health data continues to flow even during long-running
build generations that would otherwise block the main cycle.

Uses the same scanner → system_state pipeline as the orchestrator, feeding
the SQLite-backed health_observatory for anomaly detection, trend analysis,
and forecasting.
"""

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from core.health_observatory import record_snapshot, get_snapshot_stats
from core.health_observatory import get_anomalies as _get_anomalies
from core.logger import info

logger = logging.getLogger(__name__)

# Sampling interval — independent of the orchestrator's 60s cycle
SAMPLE_INTERVAL = 30  # seconds

# How often to send summary stats to the logger (every N samples)
SUMMARY_EVERY_N = 20  # every 10 minutes at 30s intervals


class HealthWorker:
    """Permanent background thread for independent health sampling.

    Started alongside the scheduler loop, survives the full process lifetime.
    Sampling is NOT gated on the orchestrator cycle — it runs continuously
    even while builds are generating.

    Usage:
        worker = HealthWorker()
        worker.start()
        # ... process lifetime ...
        worker.stop()
    """

    def __init__(self, interval: int = SAMPLE_INTERVAL):
        self.interval = interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._sample_count = 0

    # -------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------

    def start(self):
        """Launch the health worker daemon thread."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("HealthWorker: already running, not starting again")
            return

        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="kai-health-worker",
            daemon=True,
        )
        self._thread.start()
        info(f"HealthWorker: started (interval={self.interval}s)")

    def stop(self, timeout: float = 5.0):
        """Signal the health worker to stop and wait for graceful shutdown."""
        if self._thread is None or not self._thread.is_alive():
            return

        info("HealthWorker: stopping...")
        self._stop.set()
        self._thread.join(timeout=timeout)

        if self._thread.is_alive():
            logger.warning("HealthWorker: did not stop within %.1fs", timeout)
        else:
            info(f"HealthWorker: stopped (collected {self._sample_count} samples)")

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def sample_count(self) -> int:
        return self._sample_count

    def _write_state_file(self):
        """Write a tiny state file so the API process can see worker liveness.

        The scheduler and API run in separate processes, so in-memory state
        isn't shared.  This file bridges the gap.
        """
        import json
        from pathlib import Path
        from datetime import datetime, timezone

        state = {
            "running": self.is_running,
            "sample_count": self._sample_count,
            "last_sample": datetime.now(timezone.utc).isoformat(),
        }
        try:
            path = Path(os.environ.get("AI_ORCHESTRATOR_MEMORY_DIR", "memory"))
            path.mkdir(parents=True, exist_ok=True)
            tmp = path / ".health_worker_state.tmp"
            dest = path / "health_worker_state.json"
            tmp.write_text(json.dumps(state))
            tmp.replace(dest)
        except Exception:
            pass  # best-effort; logging here could spam

    # -------------------------------------------------------------------
    # Main loop
    # -------------------------------------------------------------------

    def _run(self):
        """Main sampling loop — runs until _stop is set."""
        while not self._stop.is_set():
            started = time.monotonic()

            try:
                self._sample()
            except Exception as exc:
                logger.error("HealthWorker: sample failed: %s", exc)

            # Check for new anomalies and notify
            try:
                self._notify_anomalies()
            except Exception as exc:
                logger.error("HealthWorker: anomaly notify failed: %s", exc)

            # Sleep for the remainder of the interval
            elapsed = time.monotonic() - started
            remaining = max(0, self.interval - elapsed)
            self._stop.wait(remaining)

    # -------------------------------------------------------------------
    # Sampling
    # -------------------------------------------------------------------

    def _sample(self):
        """Take one health snapshot and record it."""
        try:
            from core.scanner import scan
            from core.state import build_state
        except ImportError:
            # Scanner may not be importable in a test environment
            logger.debug("HealthWorker: scanner not available, skipping sample")
            return

        result = scan()
        build_state(
            docker=result.get("docker"),
            host={"hostname": result.get("hostname")},
        )

        # 2026-08-09: Sub-project 5 — WireGuard Health Metrics
        # Collect WG tunnel metrics and merge into the snapshot so the health
        # observatory tracks tunnel health alongside system health.
        try:
            from core.wireguard_manager import collect_wg_health_metrics
            wg_metrics = collect_wg_health_metrics()
            result["wireguard"] = wg_metrics
        except Exception as exc:
            logger.debug("HealthWorker: wireguard metrics skipped: %s", exc)

        record_snapshot(result)
        self._sample_count += 1

        # Write shared state file so the API process can see worker health
        self._write_state_file()

        if self._sample_count % SUMMARY_EVERY_N == 0:
            stats = get_snapshot_stats()
            info(
                f"HealthWorker: {self._sample_count} samples collected | "
                f"{stats['metrics_count']} metrics, {stats['anomalies_total']} anomalies "
                f"({stats['anomalies_unacked']} unacked), "
                f"{stats['db_size_bytes'] / 1_048_576:.1f} MB"
            )

    # -------------------------------------------------------------------
    # Anomaly notification
    # -------------------------------------------------------------------

    def _notify_anomalies(self):
        """Check for un-notified anomalies and enqueue them via NotificationManager."""
        try:
            from core.health_observatory import _get_conn
        except ImportError:
            return

        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT id, metric, node, actual_value, baseline_mean, z_score, severity "
                "FROM health_anomalies WHERE notified = 0 LIMIT 5"
            ).fetchall()

            if not rows:
                return

            for row in rows:
                anom_id, metric, node, actual, baseline, z_score, severity = row
                self._send_anomaly_notification(
                    anom_id, metric, node, actual, baseline, z_score, severity, conn
                )
        finally:
            conn.close()

    def _send_anomaly_notification(self, anom_id, metric, node, actual, baseline, z_score, severity, conn):
        """Enqueue a notification for a detected anomaly."""
        try:
            from core.notifications import NotificationManager
        except ImportError:
            return

        title = f"Health Anomaly: {metric}"
        body = (
            f"Node: {node}\n"
            f"Value: {actual:.1f} (baseline: {baseline:.1f})\n"
            f"Z-score: {z_score:.1f}\n"
            f"This metric is outside its normal range — investigate."
        )

        notif = NotificationManager.enqueue(
            severity="critical" if severity == "critical" else "important",
            title=title,
            body=body,
            source="health_worker",
        )

        if notif:
            conn.execute(
                "UPDATE health_anomalies SET notified = 1 WHERE id = ?",
                (anom_id,),
            )
            conn.commit()
            info(f"HealthWorker: anomaly notification sent for {metric} (id={anom_id}, z={z_score:.1f})")


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

# Default instance — created by scheduler on startup
_default_worker: Optional[HealthWorker] = None


def get_worker() -> Optional[HealthWorker]:
    """Return the default HealthWorker instance, or None if not started."""
    return _default_worker


def start_worker(interval: int = SAMPLE_INTERVAL) -> HealthWorker:
    """Start the default health worker.  Returns the instance."""
    global _default_worker
    if _default_worker is None:
        _default_worker = HealthWorker(interval=interval)
    if not _default_worker.is_running:
        _default_worker.start()
    return _default_worker


def stop_worker():
    """Stop the default health worker."""
    global _default_worker
    if _default_worker is not None:
        _default_worker.stop()
        _default_worker = None
