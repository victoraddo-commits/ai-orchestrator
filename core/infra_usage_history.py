"""Time-series history for Kai data usage — disk + per-interface bandwidth.

The live snapshot from :mod:`core.infra_usage` is point-in-time only; the
Command Center "Network & Data" graphs need a trend. This module samples each
snapshot into a small SQLite database (``memory/infra_usage_history.db``) and
serves bucketed time series for ``1h`` / ``24h`` / ``7d`` ranges.

Design notes:

* One row per sample, per (scope, interface) for network *counters*; byte/s
  rates are derived at query time from consecutive counter samples, so a
  missed sample never fabricates a rate.
* Disk usage is a level (used/total/%), not a rate.
* Sampling is **on-read with a minimum interval** (plus an optional scheduled
  sampler): the DB never grows faster than ``MIN_INTERVAL_S``.
* All writes are best-effort — history must never break the live usage API.
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

_DEFAULT_MIN_INTERVAL = float(os.environ.get("KAI_USAGE_HISTORY_MIN_INTERVAL", "30"))
_RETENTION_DAYS = float(os.environ.get("KAI_USAGE_HISTORY_RETENTION_DAYS", "30"))
MAX_POINTS = int(os.environ.get("KAI_USAGE_HISTORY_MAX_POINTS", "240"))

# range -> (window_seconds, bucket_seconds); bucket 0 keeps raw samples
RANGES: dict[str, tuple[float, int]] = {
    "1h": (3600.0, 0),
    "24h": (86400.0, 300),
    "7d": (7 * 86400.0, 3600),
}
DEFAULT_RANGE = "1h"

_SCHEMA_VERSION = 1
_SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    ts REAL PRIMARY KEY,
    generated_at TEXT
);
CREATE TABLE IF NOT EXISTS net_series (
    ts REAL, scope TEXT, iface TEXT,
    rx INTEGER, tx INTEGER
);
CREATE TABLE IF NOT EXISTS disk_series (
    ts REAL, scope TEXT, used INTEGER, total INTEGER, pct REAL
);
CREATE TABLE IF NOT EXISTS totals_series (
    ts REAL PRIMARY KEY,
    rx INTEGER, tx INTEGER, rx_rate REAL, tx_rate REAL,
    disk_used INTEGER, disk_total INTEGER, disk_pct REAL
);
CREATE INDEX IF NOT EXISTS ix_net_ts ON net_series (ts);
CREATE INDEX IF NOT EXISTS ix_net_ts_scope ON net_series (scope, iface, ts);
CREATE INDEX IF NOT EXISTS ix_disk_ts ON disk_series (ts);
"""


# ── storage ────────────────────────────────────────────────────────────────

def memory_dir() -> Path:
    """Resolve the memory dir the same way the topology graph does."""
    env = os.environ.get("AI_ORCHESTRATOR_MEMORY_DIR")
    return Path(env) if env else Path(__file__).parent.parent / "memory"


def db_path() -> Path:
    return memory_dir() / "infra_usage_history.db"


def connect(path: str | os.PathLike | None = None) -> sqlite3.Connection:
    p = Path(path) if path else db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db(conn: sqlite3.Connection | None = None) -> None:
    own = conn is None
    conn = conn or connect()
    try:
        conn.executescript(_SCHEMA)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema', ?)",
                     (str(_SCHEMA_VERSION),))
        conn.commit()
    finally:
        if own:
            conn.close()


# ── sampling ───────────────────────────────────────────────────────────────

def _scope_net(usage: dict):
    """Yield ``(scope, iface, rx, tx)`` for every host / container interface."""
    for host in usage.get("hosts") or []:
        name = host.get("name") or "host"
        for iface, counts in ((host.get("net") or {}).get("interfaces") or {}).items():
            yield f"host:{name}", iface, int(counts.get("rx", 0)), int(counts.get("tx", 0))
        for ct in host.get("containers") or []:
            for iface, counts in ((ct.get("net") or {}).get("interfaces") or {}).items():
                yield f"ct:{ct.get('vmid')}", iface, int(counts.get("rx", 0)), int(counts.get("tx", 0))


def _scope_disk(usage: dict):
    """Yield ``(scope, used, total, pct)`` for every host and container disk."""
    for host in usage.get("hosts") or []:
        name = host.get("name") or "host"
        for disk in host.get("disks") or []:
            yield (f"host:{name}", int(disk.get("used", 0)),
                   int(disk.get("total", 0)), float(disk.get("pct", 0) or 0))
        for ct in host.get("containers") or []:
            disk = ct.get("disk") or {}
            if disk:
                yield (f"ct:{ct.get('vmid')}", int(disk.get("used", 0)),
                       int(disk.get("total", 0)), float(disk.get("pct", 0) or 0))


def record_sample(usage: dict, conn: sqlite3.Connection | None = None) -> dict:
    """Persist one usage snapshot. Idempotent on the sample timestamp."""
    own = conn is None
    conn = conn or connect()
    try:
        init_db(conn)
        ts = float(usage.get("ts") or time.time())
        generated_at = usage.get("generated_at")
        conn.execute("INSERT OR REPLACE INTO samples (ts, generated_at) VALUES (?, ?)",
                     (ts, generated_at))
        totals = usage.get("totals") or {}
        conn.execute(
            "INSERT OR REPLACE INTO totals_series "
            "(ts, rx, tx, rx_rate, tx_rate, disk_used, disk_total, disk_pct) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (ts, int(totals.get("rx_total", 0) or 0), int(totals.get("tx_total", 0) or 0),
             totals.get("rx_rate"), totals.get("tx_rate"),
             int(totals.get("disk_used", 0) or 0), int(totals.get("disk_total", 0) or 0),
             totals.get("disk_pct")))
        conn.execute("DELETE FROM net_series WHERE ts = ?", (ts,))
        conn.execute("DELETE FROM disk_series WHERE ts = ?", (ts,))
        conn.executemany(
            "INSERT INTO net_series (ts, scope, iface, rx, tx) VALUES (?,?,?,?,?)",
            [(ts, s, i, rx, tx) for s, i, rx, tx in _scope_net(usage)])
        conn.executemany(
            "INSERT INTO disk_series (ts, scope, used, total, pct) VALUES (?,?,?,?,?)",
            [(ts, s, u, t, p) for s, u, t, p in _scope_disk(usage)])
        conn.commit()
        _prune(conn, ts)
        return {"ts": ts, "ok": True}
    finally:
        if own:
            conn.close()


def _prune(conn: sqlite3.Connection, now: float) -> None:
    cutoff = now - _RETENTION_DAYS * 86400.0
    for table in ("samples", "net_series", "disk_series", "totals_series"):
        conn.execute(f"DELETE FROM {table} WHERE ts < ?", (cutoff,))
    conn.commit()


def last_sample_ts(conn: sqlite3.Connection | None = None) -> float | None:
    own = conn is None
    conn = conn or connect()
    try:
        init_db(conn)
        row = conn.execute("SELECT MAX(ts) AS t FROM samples").fetchone()
        return float(row["t"]) if row and row["t"] is not None else None
    finally:
        if own:
            conn.close()


def ensure_sampled(usage: dict | None = None, *, now: float | None = None,
                   min_interval: float | None = None,
                   conn: sqlite3.Connection | None = None) -> dict:
    """Record a sample only when the min interval has elapsed.

    Returns ``{"sampled": bool, "reason": str, "ts": float|None}``. When
    ``usage`` is not provided the live snapshot is collected; collection
    failures are swallowed (history is best-effort).
    """
    now = time.time() if now is None else now
    interval = _DEFAULT_MIN_INTERVAL if min_interval is None else min_interval
    own = conn is None
    conn = conn or connect()
    try:
        last = last_sample_ts(conn)
        if last is not None and (now - last) < interval:
            return {"sampled": False, "reason": "min_interval", "ts": last}
        if usage is None:
            from core.infra_usage import collect_usage
            usage = collect_usage()
        if not (usage.get("hosts") or []):
            return {"sampled": False, "reason": "no_data", "ts": last}
        if not usage.get("ts"):
            usage = {**usage, "ts": now}
        res = record_sample(usage, conn=conn)
        return {"sampled": True, "reason": "recorded", "ts": res["ts"]}
    except Exception as e:  # noqa: BLE001
        return {"sampled": False, "reason": f"{type(e).__name__}: {e}", "ts": None}
    finally:
        if own:
            conn.close()


# ── querying ───────────────────────────────────────────────────────────────

def _bucket(ts: float, bucket: int) -> float:
    if not bucket:
        return ts
    return float(int(ts // bucket) * bucket)


def _rate(points: list[tuple[float, float]]) -> list[tuple[float, float | None]]:
    """Derive per-second rates from monotonic counter points.

    ``points`` is a list of ``(t, counter)``; returns ``(t, rate|None)``. A
    counter reset (rate < 0) is reported as ``None`` rather than a fabricated
    spike.
    """
    out: list[tuple[float, float | None]] = []
    prev: tuple[float, float] | None = None
    for t, c in points:
        if prev is None:
            out.append((t, None))
        else:
            dt = t - prev[0]
            dc = c - prev[1]
            out.append((t, (dc / dt) if (dt > 0 and dc >= 0) else None))
        prev = (t, c)
    return out


def _downsample(rows: list[sqlite3.Row], key: str, bucket: int) -> list[tuple[float, float]]:
    """Fold counter rows into per-bucket maxima (cumulative counters)."""
    acc: dict[float, float] = {}
    for r in rows:
        b = _bucket(float(r["ts"]), bucket)
        v = float(r[key] or 0)
        if b not in acc or v > acc[b]:
            acc[b] = v
    return sorted(acc.items())


def query_history(range_: str = DEFAULT_RANGE, conn: sqlite3.Connection | None = None,
                  now: float | None = None) -> dict:
    """Return bucketed time series for ``range_`` (``1h`` | ``24h`` | ``7d``)."""
    if range_ not in RANGES:
        raise ValueError(f"unknown range: {range_!r}")
    window, bucket = RANGES[range_]
    now = time.time() if now is None else now
    since = now - window
    own = conn is None
    conn = conn or connect()
    try:
        init_db(conn)
        trows = conn.execute(
            "SELECT * FROM totals_series WHERE ts >= ? ORDER BY ts", (since,)).fetchall()
        points = []
        if bucket:
            tot_acc: dict[float, sqlite3.Row] = {}
            for r in trows:
                tot_acc[_bucket(float(r["ts"]), bucket)] = r
            trows = [tot_acc[b] for b in sorted(tot_acc)]
        # totals values are already per-sample; treat rx/tx as counters over time
        rx_pts = [(float(r["ts"]), float(r["rx"] or 0)) for r in trows]
        tx_pts = [(float(r["ts"]), float(r["tx"] or 0)) for r in trows]
        rx_r = dict(_rate(rx_pts))
        tx_r = dict(_rate(tx_pts))
        for r in trows:
            t = float(r["ts"])
            points.append({
                "t": t, "rx": r["rx"], "tx": r["tx"],
                "rx_rate": r["rx_rate"] if r["rx_rate"] is not None else rx_r.get(t),
                "tx_rate": r["tx_rate"] if r["tx_rate"] is not None else tx_r.get(t),
                "disk_used": r["disk_used"], "disk_total": r["disk_total"],
                "disk_pct": r["disk_pct"],
            })

        nrows = conn.execute(
            "SELECT * FROM net_series WHERE ts >= ? ORDER BY scope, iface, ts",
            (since,)).fetchall()
        interfaces: dict[str, list[dict]] = {}
        by_iface: dict[tuple[str, str], list[sqlite3.Row]] = {}
        for r in nrows:
            by_iface.setdefault((r["scope"], r["iface"]), []).append(r)
        for (scope, iface), rows in by_iface.items():
            rx_pts = _downsample(rows, "rx", bucket)
            tx_pts = _downsample(rows, "tx", bucket)
            tx_by_t = dict(tx_pts)
            rx_r = dict(_rate(rx_pts))
            tx_r = dict(_rate(tx_pts))
            series = [{"t": t, "rx": int(rx), "tx": int(tx_by_t.get(t, 0)),
                       "rx_rate": rx_r.get(t), "tx_rate": tx_r.get(t)}
                      for t, rx in rx_pts]
            interfaces.setdefault(scope, []).append({"iface": iface, "series": series})

        drows = conn.execute(
            "SELECT * FROM disk_series WHERE ts >= ? ORDER BY scope, ts", (since,)).fetchall()
        disks: dict[str, list[dict]] = {}
        for r in drows:
            t = float(r["ts"])
            disks.setdefault(r["scope"], []).append({
                "t": _bucket(t, bucket), "used": r["used"], "total": r["total"],
                "pct": r["pct"]})
        # de-dup bucket keys per scope (keep last)
        for scope, series in disks.items():
            dedup: dict[float, dict] = {}
            for pt in series:
                dedup[pt["t"]] = pt
            disks[scope] = [dedup[k] for k in sorted(dedup)]

        return {
            "range": range_, "bucket_s": bucket, "since": since, "until": now,
            "sample_count": len(trows), "points": points,
            "interfaces": interfaces, "disks": disks,
        }
    finally:
        if own:
            conn.close()


def stats(conn: sqlite3.Connection | None = None) -> dict:
    """Small health/discovery block for the API (row counts + window)."""
    own = conn is None
    conn = conn or connect()
    try:
        init_db(conn)
        row = conn.execute(
            "SELECT COUNT(*) AS n, MIN(ts) AS first, MAX(ts) AS last FROM samples").fetchone()
        return {
            "samples": row["n"] or 0, "first_ts": row["first"], "last_ts": row["last"],
            "db": str(db_path()), "min_interval_s": _DEFAULT_MIN_INTERVAL,
            "retention_days": _RETENTION_DAYS,
        }
    finally:
        if own:
            conn.close()
