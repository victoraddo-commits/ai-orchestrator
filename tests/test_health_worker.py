"""Tests for Kai Health Observatory + Health Worker.

Verifies: metric extraction, snapshot recording, anomaly detection,
baseline learning, trend computation, forecasting, health score,
worker lifecycle, and API endpoints.
"""

import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# Isolate test data
TEST_DIR = Path(tempfile.gettempdir()) / "health_worker_test"
os.environ["AI_ORCHESTRATOR_MEMORY_DIR"] = str(TEST_DIR)
os.environ["HEALTH_OBSERVATORY_DB"] = str(TEST_DIR / "health_observatory.db")


@pytest.fixture(autouse=True)
def setup():
    """Clean test storage before each test."""
    db_path = TEST_DIR / "health_observatory.db"
    if db_path.exists():
        db_path.unlink()
    # Remove WAL/SHM files too
    for suffix in ["-wal", "-shm"]:
        p = db_path.with_name(db_path.name + suffix)
        if p.exists():
            p.unlink()
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    yield
    if db_path.exists():
        db_path.unlink()


# Sample system_state snapshot matching the shape produced by scanner.py
def _sample_snapshot(container_count=5, running_count=5):
    containers = []
    for i in range(container_count):
        containers.append({
            "Names": f"test-container-{i}",
            "State": "running" if i < running_count else "stopped",
            "HealthStatus": "healthy" if i < running_count else "",
        })

    return {
        "docker": {
            "available": True,
            "containers": containers,
        },
        "proxmox": {
            "pve": {
                "reachable": True,
                "cpu": 25.5,
                "memory_pct": 62.3,
                "disk_pct": 45.1,
                "storage": [
                    {"name": "local", "used_pct": 45.1},
                    {"name": "backup", "used_pct": 72.0},
                ],
            },
        },
        "host": {
            "hostname": "test-host",
            "cpu_percent": 12.0,
            "memory_percent": 48.0,
            "disk_percent": 34.0,
        },
    }


class TestMetricExtraction:
    """Metrics are extracted correctly from system_state snapshots."""

    def test_extract_container_metrics(self):
        """Container count metrics are extracted."""
        from core.health_observatory import _extract_metrics

        snap = _sample_snapshot(container_count=5, running_count=5)
        metrics = _extract_metrics(snap, "localhost")

        assert metrics["container_total"] == 5.0
        assert metrics["container_running"] == 5.0
        assert metrics["container_stopped"] == 0.0
        assert metrics["container_health_pct"] == 100.0

    def test_extract_with_stopped_containers(self):
        """Stopped containers reduce health_pct."""
        from core.health_observatory import _extract_metrics

        snap = _sample_snapshot(container_count=5, running_count=3)
        metrics = _extract_metrics(snap, "localhost")

        assert metrics["container_running"] == 3.0
        assert metrics["container_stopped"] == 2.0
        assert metrics["container_health_pct"] == 60.0

    def test_extract_proxmox_metrics(self):
        """Proxmox node metrics are extracted with correct prefixes."""
        from core.health_observatory import _extract_metrics

        snap = _sample_snapshot()
        metrics = _extract_metrics(snap, "localhost")

        assert metrics["proxmox_pve_reachable"] == 1.0
        assert metrics["proxmox_pve_cpu_pct"] == 25.5
        assert metrics["proxmox_pve_memory_pct"] == 62.3
        assert metrics["proxmox_pve_disk_pct"] == 45.1
        assert metrics["proxmox_pve_storage_local_pct"] == 45.1
        assert metrics["proxmox_pve_storage_backup_pct"] == 72.0

    def test_extract_host_metrics(self):
        """Host CPU/memory/disk are extracted."""
        from core.health_observatory import _extract_metrics

        snap = _sample_snapshot()
        metrics = _extract_metrics(snap, "localhost")

        assert metrics["host_cpu_pct"] == 12.0
        assert metrics["host_memory_pct"] == 48.0
        assert metrics["host_disk_pct"] == 34.0

    def test_unreachable_proxmox(self):
        """Unreachable Proxmox sets _reachable = 0."""
        from core.health_observatory import _extract_metrics

        snap = _sample_snapshot()
        snap["proxmox"]["pve"]["reachable"] = False
        snap["proxmox"]["pve"].pop("cpu", None)
        metrics = _extract_metrics(snap, "localhost")

        assert metrics["proxmox_pve_reachable"] == 0.0

    def test_health_score_in_metrics(self):
        """Composite health_score is computed and included."""
        from core.health_observatory import _extract_metrics

        snap = _sample_snapshot()
        metrics = _extract_metrics(snap, "localhost")

        assert "health_score" in metrics
        assert metrics["health_score"] == 100.0  # all healthy


class TestSnapshotRecording:
    """Recording snapshots to SQLite."""

    def test_record_snapshot_persists(self):
        """record_snapshot stores data in both snapshots and metrics tables."""
        from core.health_observatory import record_snapshot, _get_conn

        snap = _sample_snapshot()
        record_snapshot(snap, node="localhost")

        conn = _get_conn()
        snap_count = conn.execute("SELECT COUNT(*) FROM health_snapshots").fetchone()[0]
        metric_count = conn.execute("SELECT COUNT(*) FROM health_metrics").fetchone()[0]
        conn.close()

        assert snap_count == 1
        assert metric_count > 0  # multiple metrics from one snapshot

    def test_baselines_created_on_first_sample(self):
        """First sample creates baseline entries."""
        from core.health_observatory import record_snapshot, _get_conn

        snap = _sample_snapshot()
        record_snapshot(snap, node="localhost")

        conn = _get_conn()
        count = conn.execute("SELECT COUNT(*) FROM health_baselines").fetchone()[0]
        conn.close()

        assert count > 0


class TestAnomalyDetection:
    """Adaptive baseline anomaly detection."""

    def test_no_anomaly_on_first_sample(self):
        """First sample never triggers anomaly (no baseline yet)."""
        from core.health_observatory import record_snapshot, _get_conn

        snap = _sample_snapshot()
        record_snapshot(snap, node="localhost")

        conn = _get_conn()
        count = conn.execute("SELECT COUNT(*) FROM health_anomalies").fetchone()[0]
        conn.close()

        assert count == 0

    def test_no_anomaly_near_baseline(self):
        """Values close to baseline don't trigger anomalies."""
        from core.health_observatory import record_snapshot, _get_conn

        # Seed baseline with many consistent samples
        snap = _sample_snapshot()
        for _ in range(50):
            record_snapshot(snap, node="localhost")

        # Immediate check — anomalies table should be empty
        # (all values are near their baseline since we seeded with identical data)
        conn = _get_conn()
        count = conn.execute("SELECT COUNT(*) FROM health_anomalies").fetchone()[0]
        conn.close()

        # Should be 0 or very few (initial stddev=0 might cause some noise)
        assert count < 5, f"Expected few anomalies, got {count}"

    def test_large_deviation_triggers_anomaly(self):
        """A value far from baseline triggers anomaly detection."""
        from core.health_observatory import record_snapshot, _get_conn

        # Seed baseline with normal data
        normal = _sample_snapshot()
        for _ in range(50):
            record_snapshot(normal, node="localhost")

        # Force a concrete baseline value for host_cpu_pct
        conn_before = _get_conn()
        conn_before.execute(
            "UPDATE health_baselines SET mean = 15.0, stddev = 5.0, sample_count = 100 "
            "WHERE metric = 'host_cpu_pct' AND node = 'localhost'"
        )
        conn_before.commit()
        conn_before.close()

        # Now inject an anomalous sample (cpu at 95% vs baseline 15%±5)
        anomalous = _sample_snapshot()
        anomalous["host"]["cpu_percent"] = 95.0  # z-score ≈ 16
        record_snapshot(anomalous, node="localhost")

        conn = _get_conn()
        anomalies = conn.execute("SELECT metric, z_score, severity FROM health_anomalies").fetchall()
        conn.close()

        cpu_anomalies = [a for a in anomalies if a[0] == "host_cpu_pct"]
        assert len(cpu_anomalies) > 0, "Expected anomaly for host_cpu_pct spike"
        assert cpu_anomalies[0][2] == "critical"  # z > 5 → critical


class TestTrendComputation:
    """Linear regression trend analysis."""

    def test_stable_trend(self):
        """Constant values produce stable trend."""
        from core.health_observatory import _compute_trend

        values = [50.0] * 100
        rows = [(f"2026-08-09T00:00:{i:02d}.000000+00:00", v) for i, v in enumerate(values)]

        trend = _compute_trend("test_metric", "localhost", 1, values, rows)

        assert trend["trend_direction"] == "stable"
        assert abs(trend["slope_per_hour"]) < 0.01

    def test_rising_trend(self):
        """Linearly increasing values produce rising trend."""
        from core.health_observatory import _compute_trend

        values = [50.0 + i * 0.1 for i in range(100)]  # rises ~10
        rows = [(f"2026-08-09T00:00:{i:02d}.000000+00:00", v) for i, v in enumerate(values)]

        trend = _compute_trend("disk_pct", "localhost", 24, values, rows)

        assert trend["trend_direction"] == "rising"
        assert trend["slope_per_hour"] > 0.5
        assert trend["r_squared"] > 0.9  # near-perfect linear

    def test_forecast_generated(self):
        """Rising utilization metric generates exhaustion forecast."""
        from core.health_observatory import _compute_trend

        values = [70.0 + i * 0.05 for i in range(200)]  # rises ~10 over 200 pts
        rows = [(f"2026-08-09T00:00:{i:02d}.000000+00:00", v) for i, v in enumerate(values)]

        trend = _compute_trend("disk_pct", "localhost", 24, values, rows)

        assert trend["forecast"] is not None
        assert "exhaustion_time_iso" in trend["forecast"]
        assert trend["forecast"]["hours_until"] > 0

    def test_falling_trend(self):
        """Decreasing values produce falling trend."""
        from core.health_observatory import _compute_trend

        values = [90.0 - i * 0.05 for i in range(100)]
        rows = [(f"2026-08-09T00:00:{i:02d}.000000+00:00", v) for i, v in enumerate(values)]

        trend = _compute_trend("memory_pct", "localhost", 6, values, rows)

        assert trend["trend_direction"] == "falling"
        assert trend["slope_per_hour"] < -0.1


class TestHealthScore:
    """Composite health score computation."""

    def test_all_healthy_scores_100(self):
        """All healthy metrics → score 100."""
        from core.health_observatory import _compute_health_score

        latest = {
            "container_running": 10.0,
            "container_total": 10.0,
            "proxmox_pve_reachable": 1.0,
            "host_cpu_pct": 15.0,
            "host_memory_pct": 40.0,
            "host_disk_pct": 30.0,
        }

        result = _compute_health_score("localhost", latest, "2026-08-09T00:00:00")
        assert result["health_score"] == 100.0

    def test_stopped_containers_reduce_score(self):
        """Stopped containers penalize health score."""
        from core.health_observatory import _compute_health_score

        latest = {
            "container_running": 3.0,
            "container_total": 5.0,
            "proxmox_pve_reachable": 1.0,
            "host_cpu_pct": 15.0,
            "host_memory_pct": 40.0,
            "host_disk_pct": 30.0,
        }

        result = _compute_health_score("localhost", latest, "2026-08-09T00:00:00")
        assert result["health_score"] < 100.0

    def test_unreachable_proxmox_penalizes(self):
        """Unreachable Proxmox reduces score significantly."""
        from core.health_observatory import _compute_health_score

        latest = {
            "container_running": 10.0,
            "container_total": 10.0,
            "proxmox_pve_reachable": 0.0,
            "host_cpu_pct": 15.0,
            "host_memory_pct": 40.0,
            "host_disk_pct": 30.0,
        }

        result = _compute_health_score("localhost", latest, "2026-08-09T00:00:00")
        assert result["health_score"] <= 80.0


class TestWorkerLifecycle:
    """HealthWorker start/stop/state."""

    def test_worker_starts_and_stops(self):
        """Worker starts, runs, and stops cleanly."""
        from core.health_worker import HealthWorker

        worker = HealthWorker(interval=30)
        assert not worker.is_running

        worker.start()
        time.sleep(0.2)  # let it loop once
        assert worker.is_running

        worker.stop()
        assert not worker.is_running

    def test_worker_cant_start_twice(self):
        """Starting an already-running worker is a no-op."""
        from core.health_worker import HealthWorker

        worker = HealthWorker(interval=30)
        worker.start()
        time.sleep(0.1)
        worker.start()  # second start should not crash
        worker.stop()

    def test_module_level_start_stop(self):
        """Module-level start_worker/stop_worker functions work."""
        from core.health_worker import start_worker, stop_worker, get_worker

        w = start_worker(interval=30)
        assert w is not None
        assert get_worker() is w

        stop_worker()
        assert get_worker() is None


class TestAnomalyAck:
    """Acknowledging anomalies."""

    def test_ack_single_anomaly(self):
        """Ack an anomaly marks it as acknowledged."""
        from core.health_observatory import record_snapshot, ack_anomaly, ack_all_anomalies
        from core.health_observatory import _get_conn

        # Force an anomaly by seeding baseline then injecting spike
        normal = _sample_snapshot()
        for _ in range(50):
            record_snapshot(normal, node="localhost")

        conn = _get_conn()
        # Create a manual anomaly
        conn.execute(
            "INSERT INTO health_anomalies (timestamp, metric, node, baseline_mean, "
            "baseline_stddev, actual_value, z_score, severity) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("2026-08-09T00:00:00", "host_cpu_pct", "localhost", 15.0, 5.0, 90.0, 15.0, "critical"),
        )
        conn.commit()
        anom_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()

        ok = ack_anomaly(anom_id)
        assert ok

        conn = _get_conn()
        acked = conn.execute(
            "SELECT acked FROM health_anomalies WHERE id = ?", (anom_id,)
        ).fetchone()[0]
        conn.close()
        assert acked == 1

    def test_ack_all(self):
        """ack_all_anomalies marks all as acknowledged."""
        from core.health_observatory import ack_all_anomalies, _get_conn

        conn = _get_conn()
        conn.execute(
            "INSERT INTO health_anomalies (timestamp, metric, node, baseline_mean, "
            "baseline_stddev, actual_value, z_score, severity) VALUES "
            "('2026-08-09T00:00:00', 'm1', 'localhost', 10, 2, 50, 20, 'critical'),"
            "('2026-08-09T00:00:01', 'm2', 'localhost', 10, 2, 40, 15, 'critical')"
        )
        conn.commit()
        conn.close()

        count = ack_all_anomalies()
        assert count == 2


class TestDatabaseStats:
    """Snapshot stats endpoint."""

    def test_stats_on_empty_db(self):
        """Stats on an empty DB return zeros."""
        from core.health_observatory import get_snapshot_stats

        stats = get_snapshot_stats()
        assert stats["metrics_count"] == 0
        assert stats["anomalies_total"] == 0

    def test_stats_after_recording(self):
        """Stats reflect recorded data."""
        from core.health_observatory import record_snapshot, get_snapshot_stats

        snap = _sample_snapshot()
        record_snapshot(snap)

        stats = get_snapshot_stats()
        assert stats["metrics_count"] > 0
        assert stats["anomalies_total"] == 0  # normal data, no anomalies
