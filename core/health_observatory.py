"""Kai Health Observatory — SQLite-backed health metrics, anomaly detection, trend analysis.

Part of: Kai Mobile Command Node — Sub-project 4: Permanent Health Worker.

Key capabilities:
- Time-series storage of health metrics at 30s resolution (24h retention)
- Adaptive baseline learning (EMA — slow adaptation to real workload shifts)
- Anomaly detection: z-score > 3 vs per-metric baseline
- Trend detection: linear regression on 1h/6h/24h sliding windows
- Resource forecasting: projected time-to-full for disk and memory
- Multi-resolution rollups: 5min (7d) and hourly (30d)

Database: memory/health_observatory.db (SQLite, WAL mode)
Zero new dependencies — sqlite3 is Python stdlib.
"""

import json
import logging
import math
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DB_PATH = Path(os.environ.get(
    "HEALTH_OBSERVATORY_DB",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "memory", "health_observatory.db"),
))

# Retention
RAW_RETENTION_HOURS = 24        # 30s samples kept for 24h
ROLLUP_5MIN_RETENTION_DAYS = 7  # 5min rollups kept for 7 days
ROLLUP_HOURLY_RETENTION_DAYS = 30  # hourly rollups kept for 30 days

# Anomaly detection
ANOMALY_Z_SCORE_THRESHOLD = 3.0   # z-score above this → anomaly
BASELINE_MIN_SAMPLES = 30         # minimum samples before baseline is trusted
BASELINE_EMA_ALPHA = 0.05         # slow adaptation rate for baseline

# Trend windows (in data points, each = 30s)
TREND_1H_POINTS = 120    # 1 hour
TREND_6H_POINTS = 720    # 6 hours
TREND_24H_POINTS = 2880  # 24 hours

# Forecasting
FORECAST_CRITICAL_PCT = 95.0   # pct at which we call "disk/mem full"
FORECAST_MIN_SLOPE = 0.001     # min slope (% per 30s) to bother forecasting

# Thread safety
_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def _ensure_schema(conn):
    """Create tables if they don't exist.  Idempotent."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS health_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            data TEXT NOT NULL,
            node TEXT DEFAULT 'localhost'
        );

        CREATE TABLE IF NOT EXISTS health_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            metric TEXT NOT NULL,
            value REAL NOT NULL,
            node TEXT DEFAULT 'localhost'
        );

        CREATE INDEX IF NOT EXISTS idx_metrics_time ON health_metrics(timestamp);
        CREATE INDEX IF NOT EXISTS idx_metrics_metric ON health_metrics(metric, timestamp);
        CREATE INDEX IF NOT EXISTS idx_metrics_node_metric ON health_metrics(node, metric, timestamp);

        CREATE TABLE IF NOT EXISTS health_anomalies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            metric TEXT NOT NULL,
            node TEXT DEFAULT 'localhost',
            baseline_mean REAL,
            baseline_stddev REAL,
            actual_value REAL,
            z_score REAL,
            severity TEXT DEFAULT 'warning',
            acked INTEGER DEFAULT 0,
            notified INTEGER DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_anomalies_time ON health_anomalies(timestamp);

        CREATE TABLE IF NOT EXISTS health_baselines (
            metric TEXT NOT NULL,
            node TEXT DEFAULT 'localhost',
            mean REAL NOT NULL,
            stddev REAL NOT NULL,
            sample_count INTEGER DEFAULT 0,
            last_updated TEXT,
            PRIMARY KEY (metric, node)
        );

        CREATE TABLE IF NOT EXISTS health_rollups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            resolution TEXT NOT NULL,  -- '5min' or 'hourly'
            metric TEXT NOT NULL,
            node TEXT DEFAULT 'localhost',
            avg_value REAL,
            min_value REAL,
            max_value REAL,
            stddev_value REAL,
            sample_count INTEGER
        );

        CREATE INDEX IF NOT EXISTS idx_rollups_time_res ON health_rollups(timestamp, resolution);
    """)
    conn.commit()


def _get_conn() -> sqlite3.Connection:
    """Get a thread-local database connection with WAL mode enabled."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    _ensure_schema(conn)
    return conn


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------


def record_snapshot(snapshot_data: dict, node: str = "localhost"):
    """Store a complete health snapshot.

    Extracts numeric metrics from the snapshot and stores them as individual
    metric rows for efficient time-series queries.
    """
    now = _now_iso()
    data_json = json.dumps(snapshot_data, default=str)

    with _lock:
        conn = _get_conn()
        try:
            # Store the full snapshot
            conn.execute(
                "INSERT INTO health_snapshots (timestamp, data, node) VALUES (?, ?, ?)",
                (now, data_json, node),
            )

            # Extract and store individual metrics
            metrics = _extract_metrics(snapshot_data, node)
            for metric, value in metrics.items():
                conn.execute(
                    "INSERT INTO health_metrics (timestamp, metric, value, node) VALUES (?, ?, ?, ?)",
                    (now, metric, value, node),
                )

            conn.commit()

            # Check for anomalies on new data
            _check_anomalies(conn, metrics, now, node)

            # Update baselines
            _update_baselines(conn, metrics, node, now)

            # Prune old data
            _prune_old_data(conn, now)

            # Run rollups periodically (every 5 min)
            _maybe_rollup(conn, now)

            conn.commit()
        finally:
            conn.close()


def ack_anomaly(anomaly_id: int) -> bool:
    """Mark an anomaly as acknowledged.  Returns True if found."""
    with _lock:
        conn = _get_conn()
        try:
            cur = conn.execute(
                "UPDATE health_anomalies SET acked = 1 WHERE id = ?", (anomaly_id,)
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def ack_all_anomalies() -> int:
    """Acknowledge all unacknowledged anomalies.  Returns count acked."""
    with _lock:
        conn = _get_conn()
        try:
            cur = conn.execute(
                "UPDATE health_anomalies SET acked = 1 WHERE acked = 0"
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Read operations (no lock needed — WAL mode readers don't block)
# ---------------------------------------------------------------------------


def get_recent_metrics(
    metric: Optional[str] = None,
    node: Optional[str] = None,
    minutes: int = 60,
) -> list[dict]:
    """Return metric time-series for the last N minutes."""
    conn = _get_conn()
    try:
        cutoff = _iso_minutes_ago(minutes)
        if metric and node:
            rows = conn.execute(
                "SELECT timestamp, metric, value, node FROM health_metrics "
                "WHERE metric = ? AND node = ? AND timestamp >= ? ORDER BY timestamp",
                (metric, node, cutoff),
            ).fetchall()
        elif metric:
            rows = conn.execute(
                "SELECT timestamp, metric, value, node FROM health_metrics "
                "WHERE metric = ? AND timestamp >= ? ORDER BY timestamp",
                (metric, cutoff),
            ).fetchall()
        elif node:
            rows = conn.execute(
                "SELECT timestamp, metric, value, node FROM health_metrics "
                "WHERE node = ? AND timestamp >= ? ORDER BY timestamp",
                (node, cutoff),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT timestamp, metric, value, node FROM health_metrics "
                "WHERE timestamp >= ? ORDER BY timestamp",
                (cutoff,),
            ).fetchall()
        return [_row_dict(r, ["timestamp", "metric", "value", "node"]) for r in rows]
    finally:
        conn.close()


def get_anomalies(acked: Optional[bool] = None, limit: int = 50) -> list[dict]:
    """Return recent anomalies, newest first."""
    conn = _get_conn()
    try:
        if acked is True:
            rows = conn.execute(
                "SELECT * FROM health_anomalies WHERE acked = 1 ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        elif acked is False:
            rows = conn.execute(
                "SELECT * FROM health_anomalies WHERE acked = 0 ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM health_anomalies ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        cols = ["id", "timestamp", "metric", "node", "baseline_mean", "baseline_stddev",
                "actual_value", "z_score", "severity", "acked", "notified"]
        return [_row_dict(r, cols) for r in rows]
    finally:
        conn.close()


def get_baselines(node: Optional[str] = None) -> list[dict]:
    """Return learned baselines for all metrics."""
    conn = _get_conn()
    try:
        if node:
            rows = conn.execute(
                "SELECT * FROM health_baselines WHERE node = ? ORDER BY metric", (node,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM health_baselines ORDER BY node, metric"
            ).fetchall()
        cols = ["metric", "node", "mean", "stddev", "sample_count", "last_updated"]
        return [_row_dict(r, cols) for r in rows]
    finally:
        conn.close()


def get_trend(
    metric: str,
    node: str = "localhost",
    window_hours: int = 6,
) -> dict:
    """Return trend data for a metric over a time window.

    Computes linear regression slope and r², plus the forecast if the
    metric represents a percentage utilization (disk, memory).
    """
    conn = _get_conn()
    try:
        cutoff = _iso_hours_ago(window_hours)
        rows = conn.execute(
            "SELECT timestamp, value FROM health_metrics "
            "WHERE metric = ? AND node = ? AND timestamp >= ? ORDER BY timestamp",
            (metric, node, cutoff),
        ).fetchall()

        if len(rows) < 10:
            return {
                "metric": metric,
                "node": node,
                "window_hours": window_hours,
                "sample_count": len(rows),
                "slope": 0,
                "slope_per_hour": 0,
                "r_squared": 0,
                "current_value": rows[-1][1] if rows else 0,
                "forecast": None,
                "trend_direction": "stable",
            }

        values = [r[1] for r in rows]
        return _compute_trend(metric, node, window_hours, values, rows)
    finally:
        conn.close()


def get_health_score(node: str = "localhost") -> dict:
    """Compute a composite health score (0-100) from recent metrics."""
    conn = _get_conn()
    try:
        cutoff = _iso_minutes_ago(15)
        # Get latest value for each metric in the last 15min
        rows = conn.execute(
            "SELECT metric, value FROM health_metrics "
            "WHERE node = ? AND timestamp >= ? ORDER BY timestamp DESC",
            (node, cutoff),
        ).fetchall()

        if not rows:
            return {"node": node, "health_score": 100, "components": {}, "sample_age": "no_data"}

        # Take most recent value per metric
        latest = {}
        for r in rows:
            if r[0] not in latest:
                latest[r[0]] = r[1]

        # Use the most recent sample's timestamp
        sample_time = _now_iso()
        return _compute_health_score(node, latest, sample_time)
    finally:
        conn.close()


def get_snapshot_stats() -> dict:
    """Return database-level stats: row counts, size, retention status."""
    conn = _get_conn()
    try:
        metrics_count = conn.execute("SELECT COUNT(*) FROM health_metrics").fetchone()[0]
        anomaly_count = conn.execute("SELECT COUNT(*) FROM health_anomalies").fetchone()[0]
        unacked_anomalies = conn.execute(
            "SELECT COUNT(*) FROM health_anomalies WHERE acked = 0"
        ).fetchone()[0]
        baseline_count = conn.execute("SELECT COUNT(*) FROM health_baselines").fetchone()[0]
        oldest_metric = conn.execute(
            "SELECT MIN(timestamp) FROM health_metrics"
        ).fetchone()[0]
        newest_metric = conn.execute(
            "SELECT MAX(timestamp) FROM health_metrics"
        ).fetchone()[0]

        db_size = DB_PATH.stat().st_size if DB_PATH.exists() else 0

        return {
            "metrics_count": metrics_count,
            "anomalies_total": anomaly_count,
            "anomalies_unacked": unacked_anomalies,
            "baselines_count": baseline_count,
            "oldest_sample": oldest_metric,
            "newest_sample": newest_metric,
            "db_size_bytes": db_size,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Forecast (high-level — builds on trend data)
# ---------------------------------------------------------------------------


def get_forecast(node: str = "localhost") -> list[dict]:
    """Return forecasts for all utilization-type metrics."""
    utilization_metrics = ["disk_pct", "memory_pct", "cpu_pct"]
    forecasts = []

    for metric in utilization_metrics:
        trend = get_trend(metric, node, window_hours=24)
        if trend["forecast"]:
            forecasts.append({
                "metric": metric,
                "node": node,
                "current_pct": trend["current_value"],
                "slope_per_hour": trend["slope_per_hour"],
                "projected_exhaustion": trend["forecast"]["exhaustion_time_iso"],
                "hours_until_exhaustion": trend["forecast"]["hours_until"],
                "confidence": trend["r_squared"],
                "trend_direction": trend["trend_direction"],
            })

    return forecasts


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_minutes_ago(minutes: int) -> str:
    import datetime as _dt
    return (_dt.datetime.now(timezone.utc) - _dt.timedelta(minutes=minutes)).isoformat()


def _iso_hours_ago(hours: int) -> str:
    import datetime as _dt
    return (_dt.datetime.now(timezone.utc) - _dt.timedelta(hours=hours)).isoformat()


def _row_dict(row, cols):
    return {cols[i]: row[i] for i in range(len(cols))}


def _extract_metrics(snapshot: dict, node: str) -> dict[str, float]:
    """Walk a system_state snapshot and extract numeric health metrics."""
    metrics = {}

    # Docker container counts
    docker = snapshot.get("docker", {})
    containers = docker.get("containers", [])
    if containers:
        total = len(containers)
        running = sum(1 for c in containers if isinstance(c, dict) and c.get("State") == "running")
        metrics["container_total"] = float(total)
        metrics["container_running"] = float(running)
        metrics["container_stopped"] = float(total - running)
        metrics["container_health_pct"] = (running / total * 100) if total > 0 else 0.0

    # Proxmox nodes
    proxmox = snapshot.get("proxmox", {})
    for pnode_name, pnode_data in proxmox.items():
        if not isinstance(pnode_data, dict):
            continue
        prefix = f"proxmox_{pnode_name}"
        if pnode_data.get("reachable"):
            metrics[f"{prefix}_reachable"] = 1.0
            cpu = pnode_data.get("cpu", 0)
            if isinstance(cpu, (int, float)):
                metrics[f"{prefix}_cpu_pct"] = float(cpu)
            mem_pct = pnode_data.get("memory_pct", 0)
            if isinstance(mem_pct, (int, float)):
                metrics[f"{prefix}_memory_pct"] = float(mem_pct)
            disk_pct = pnode_data.get("disk_pct", 0)
            if isinstance(disk_pct, (int, float)):
                metrics[f"{prefix}_disk_pct"] = float(disk_pct)

            storage_list = pnode_data.get("storage", [])
            if isinstance(storage_list, list):
                for s in storage_list:
                    if isinstance(s, dict):
                        sname = s.get("name", "unknown")
                        used_pct = s.get("used_pct", 0)
                        if isinstance(used_pct, (int, float)):
                            metrics[f"{prefix}_storage_{sname}_pct"] = float(used_pct)
        else:
            metrics[f"{prefix}_reachable"] = 0.0

    # Host-level from system_state
    host = snapshot.get("host", {})
    if isinstance(host, dict):
        cpu = host.get("cpu_percent", host.get("cpu", 0))
        if isinstance(cpu, (int, float)):
            metrics["host_cpu_pct"] = float(cpu)
        mem = host.get("memory_percent", host.get("memory", {}).get("percent", 0) if isinstance(host.get("memory"), dict) else 0)
        if isinstance(mem, (int, float)):
            metrics["host_memory_pct"] = float(mem)
        disk = host.get("disk_percent", host.get("disk", {}).get("percent", 0) if isinstance(host.get("disk"), dict) else 0)
        if isinstance(disk, (int, float)):
            metrics["host_disk_pct"] = float(disk)

    # WireGuard tunnel metrics (Sub-project 5: WireGuard Resilience)
    wireguard = snapshot.get("wireguard", {})
    if isinstance(wireguard, dict):
        for key, value in wireguard.items():
            if isinstance(value, (int, float)):
                metrics[f"wg_{key}"] = float(value)

    # Composite health score
    if containers and "container_running" in metrics:
        score = 100.0
        stopped = metrics.get("container_stopped", 0)
        if stopped > 0:
            score -= min(50, stopped * 10)
        # Adjust for Proxmox reachability
        proxmox_unreachable = sum(
            1 for k, v in metrics.items()
            if k.endswith("_reachable") and v == 0.0
        )
        score -= min(30, proxmox_unreachable * 15)
        # Adjust for WireGuard tunnel health
        if metrics.get("wg_wg_tunnel_reachable", 1.0) == 0.0:
            score -= 20
        metrics["health_score"] = max(0.0, score)

    return metrics


def _check_anomalies(conn, metrics: dict[str, float], now: str, node: str):
    """Check new metric values against learned baselines for anomalies."""
    for metric, value in metrics.items():
        baseline = conn.execute(
            "SELECT mean, stddev, sample_count FROM health_baselines WHERE metric = ? AND node = ?",
            (metric, node),
        ).fetchone()

        if baseline is None:
            continue

        mean, stddev, sample_count = baseline
        if sample_count < BASELINE_MIN_SAMPLES:
            continue  # not enough data to trust baseline
        if stddev < 1e-10:
            continue  # avoid division by zero (constant metric)

        z_score = abs(value - mean) / stddev

        if z_score > ANOMALY_Z_SCORE_THRESHOLD:
            severity = "critical" if z_score > 5.0 else "warning"
            conn.execute(
                "INSERT INTO health_anomalies (timestamp, metric, node, baseline_mean, "
                "baseline_stddev, actual_value, z_score, severity) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (now, metric, node, mean, stddev, value, round(z_score, 2), severity),
            )
            from core.logger import info as _info
            _info(
                f"health_observatory: anomaly detected — {node}:{metric} = {value:.1f} "
                f"(baseline {mean:.1f}±{stddev:.1f}, z={z_score:.1f})"
            )


def _update_baselines(conn, metrics: dict[str, float], node: str, now: str):
    """Update per-metric baselines using exponential moving average."""
    for metric, value in metrics.items():
        existing = conn.execute(
            "SELECT mean, stddev, sample_count FROM health_baselines WHERE metric = ? AND node = ?",
            (metric, node),
        ).fetchone()

        if existing:
            old_mean, old_stddev, count = existing
            # EMA update
            alpha = BASELINE_EMA_ALPHA
            new_mean = old_mean + alpha * (value - old_mean)
            # Welford-style stddev update
            delta = value - old_mean
            new_stddev = math.sqrt(
                max(0, old_stddev ** 2 + alpha * (delta ** 2 - old_stddev ** 2))
            )
            conn.execute(
                "UPDATE health_baselines SET mean = ?, stddev = ?, sample_count = ?, last_updated = ? "
                "WHERE metric = ? AND node = ?",
                (new_mean, new_stddev, count + 1, now, metric, node),
            )
        else:
            conn.execute(
                "INSERT INTO health_baselines (metric, node, mean, stddev, sample_count, last_updated) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (metric, node, value, 0.0, 1, now),
            )


def _prune_old_data(conn, now: str):
    """Remove samples older than retention thresholds."""
    import datetime as _dt
    try:
        now_dt = _dt.datetime.fromisoformat(now)
    except ValueError:
        return

    raw_cutoff = (now_dt - _dt.timedelta(hours=RAW_RETENTION_HOURS)).isoformat()
    rollup_5m_cutoff = (now_dt - _dt.timedelta(days=ROLLUP_5MIN_RETENTION_DAYS)).isoformat()
    rollup_h_cutoff = (now_dt - _dt.timedelta(days=ROLLUP_HOURLY_RETENTION_DAYS)).isoformat()

    conn.execute("DELETE FROM health_snapshots WHERE timestamp < ?", (raw_cutoff,))
    conn.execute("DELETE FROM health_metrics WHERE timestamp < ?", (raw_cutoff,))
    conn.execute("DELETE FROM health_rollups WHERE resolution = '5min' AND timestamp < ?", (rollup_5m_cutoff,))
    conn.execute("DELETE FROM health_rollups WHERE resolution = 'hourly' AND timestamp < ?", (rollup_h_cutoff,))
    # Keep anomalies for 90 days
    anomaly_cutoff = (now_dt - _dt.timedelta(days=90)).isoformat()
    conn.execute("DELETE FROM health_anomalies WHERE timestamp < ?", (anomaly_cutoff,))


def _maybe_rollup(conn, now: str):
    """Periodically compute 5min and hourly rollups to save space."""
    import datetime as _dt
    try:
        now_dt = _dt.datetime.fromisoformat(now)
    except ValueError:
        return

    # Compute 5min rollups if it's been more than 5 min since last one
    last_5m = conn.execute(
        "SELECT MAX(timestamp) FROM health_rollups WHERE resolution = '5min'"
    ).fetchone()[0]

    if last_5m is None or (now_dt - _dt.datetime.fromisoformat(last_5m)).total_seconds() >= 300:
        _compute_rollup(conn, "5min", 300, now_dt)

    # Compute hourly rollups
    last_h = conn.execute(
        "SELECT MAX(timestamp) FROM health_rollups WHERE resolution = 'hourly'"
    ).fetchone()[0]

    if last_h is None or (now_dt - _dt.datetime.fromisoformat(last_h)).total_seconds() >= 3600:
        _compute_rollup(conn, "hourly", 3600, now_dt)


def _compute_rollup(conn, resolution: str, window_seconds: int, now_dt):
    """Aggregate health_metrics into rollup buckets."""
    import datetime as _dt
    cutoff = (now_dt - _dt.timedelta(seconds=window_seconds)).isoformat()

    # Get unique metric+node pairs that have new data
    pairs = conn.execute(
        "SELECT DISTINCT metric, node FROM health_metrics WHERE timestamp >= ?",
        (cutoff,),
    ).fetchall()

    for metric, node in pairs:
        rows = conn.execute(
            "SELECT value FROM health_metrics WHERE metric = ? AND node = ? AND timestamp >= ?",
            (metric, node, cutoff),
        ).fetchall()

        if not rows:
            continue

        values = [r[0] for r in rows]
        n = len(values)
        avg_v = sum(values) / n
        min_v = min(values)
        max_v = max(values)

        # Compute stddev in Python (cleaner than nested SQL subqueries)
        variance = sum((v - avg_v) ** 2 for v in values) / n
        stddev_v = math.sqrt(variance)

        bucket_time = now_dt.replace(second=0, microsecond=0).isoformat()
        conn.execute(
            "INSERT INTO health_rollups (timestamp, resolution, metric, node, "
            "avg_value, min_value, max_value, stddev_value, sample_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (bucket_time, resolution, metric, node, avg_v, min_v, max_v, stddev_v, n),
        )


def _compute_trend(metric: str, node: str, window_hours: int, values: list, rows: list) -> dict:
    """Compute linear regression trend and forecast."""
    n = len(values)
    x_mean = (n - 1) / 2.0
    y_mean = sum(values) / n

    numerator = sum((i - x_mean) * (values[i] - y_mean) for i in range(n))
    denominator = sum((i - x_mean) ** 2 for i in range(n))

    if abs(denominator) < 1e-10:
        slope = 0.0
    else:
        slope = numerator / denominator

    # R-squared
    y_pred = [y_mean + slope * (i - x_mean) for i in range(n)]
    ss_res = sum((values[i] - y_pred[i]) ** 2 for i in range(n))
    ss_tot = sum((values[i] - y_mean) ** 2 for i in range(n))
    r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0

    # Slope per hour (each point is ~30s apart, 120 points per hour)
    slope_per_hour = slope * 120

    current_value = values[-1]

    # Determine direction
    if abs(slope_per_hour) < 0.01:
        direction = "stable"
    elif slope_per_hour > 0:
        direction = "rising"
    else:
        direction = "falling"

    # Forecast exhaustion if metric is utilization and slope is positive
    forecast = None
    if slope > 0 and current_value < FORECAST_CRITICAL_PCT:
        points_until = (FORECAST_CRITICAL_PCT - current_value) / slope
        hours_until = points_until / 120  # 120 points per hour
        if 0 < hours_until < 8760:  # less than a year out
            import datetime as _dt
            exhaustion_time = _dt.datetime.now(timezone.utc) + _dt.timedelta(hours=hours_until)
            forecast = {
                "exhaustion_time_iso": exhaustion_time.isoformat(),
                "hours_until": round(hours_until, 1),
                "critical_pct": FORECAST_CRITICAL_PCT,
            }

    return {
        "metric": metric,
        "node": node,
        "window_hours": window_hours,
        "sample_count": n,
        "slope": round(slope, 6),
        "slope_per_hour": round(slope_per_hour, 4),
        "r_squared": round(r_squared, 4),
        "current_value": round(current_value, 2),
        "forecast": forecast,
        "trend_direction": direction,
    }


def _compute_health_score(node, latest_metrics, sample_time) -> dict:
    """Compute a composite 0-100 health score."""
    score = 100.0
    components = {}

    # Container health
    running = latest_metrics.get("container_running", 0)
    total = latest_metrics.get("container_total", 0)
    if total > 0:
        pct = running / total * 100
        components["containers"] = {"running": int(running), "total": int(total), "health_pct": round(pct, 1)}
        if pct < 100:
            score -= min(30, (total - running) * 10)

    # Proxmox reachability
    proxmox_healthy = True
    for k, v in latest_metrics.items():
        if k.endswith("_reachable"):
            node_name = k.replace("_reachable", "")
            components[f"proxmox_{node_name}"] = {"reachable": v == 1.0}
            if v == 0.0:
                proxmox_healthy = False
                score -= 20

    # Resource pressure
    for key, label in [("host_cpu_pct", "cpu"), ("host_memory_pct", "memory"), ("host_disk_pct", "disk")]:
        val = latest_metrics.get(key, 0)
        if val > 0:
            components[label] = {"pct": round(val, 1)}
            if val > 90:
                score -= 25
            elif val > 70:
                score -= 10

    return {
        "node": node,
        "health_score": max(0, round(score, 1)),
        "components": components,
        "computed_at": sample_time,
    }
