"""Tests for core/infra_usage_history.py — the SQLite trend store + query API."""
from __future__ import annotations

import sqlite3

import pytest

from core import infra_usage_history as h


def _usage(ts: float, rx: int, tx: int, used: int = 100, total: int = 1000) -> dict:
    return {
        "schema": 1,
        "ts": ts,
        "generated_at": "2026-09-24T00:00:00Z",
        "interval_s": None,
        "hosts": [{
            "name": "pve-b", "host": "192.168.1.110", "reachable": True, "error": None,
            "disks": [{"filesystem": "/dev/mapper/pve-root", "mount": "/",
                       "total": total, "used": used, "avail": total - used,
                       "pct": round(used / total * 100), "kind": "local"}],
            "mounts": [], "containers": [],
            "net": {"interfaces": {"nic0": {"rx": rx, "tx": tx},
                                   "lo": {"rx": 1, "tx": 1}},
                    "rx_total": rx, "tx_total": tx, "rx_rate": None, "tx_rate": None},
            "vms": [],
        }],
        "mounts": [],
        "totals": {"disk_total": total, "disk_used": used, "disk_pct": used / total * 100,
                   "rx_total": rx, "tx_total": tx, "rx_rate": None, "tx_rate": None},
    }


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_ORCHESTRATOR_MEMORY_DIR", str(tmp_path / "mem"))
    c = h.connect()
    h.init_db(c)
    yield c
    c.close()


def test_db_path_respects_memory_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_ORCHESTRATOR_MEMORY_DIR", str(tmp_path / "m"))
    assert str(h.db_path()).endswith("m/infra_usage_history.db")


def test_init_db_creates_tables(conn):
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"samples", "net_series", "disk_series", "totals_series", "meta"} <= names


def test_record_sample_persists_all_series(conn):
    h.record_sample(_usage(1000.0, 1000, 500), conn=conn)
    assert conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM totals_series").fetchone()[0] == 1
    scopes = {r[0] for r in conn.execute("SELECT scope FROM net_series")}
    assert "host:pve-b" in scopes
    ifaces = {r[0] for r in conn.execute(
        "SELECT iface FROM net_series WHERE scope='host:pve-b'")}
    assert {"nic0", "lo"} <= ifaces
    assert conn.execute("SELECT COUNT(*) FROM disk_series").fetchone()[0] == 1


def test_record_sample_is_idempotent_on_ts(conn):
    h.record_sample(_usage(1000.0, 1000, 500), conn=conn)
    h.record_sample(_usage(1000.0, 9999, 9999), conn=conn)
    assert conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM net_series").fetchone()[0] == 2


def test_query_history_computes_rates_from_counters(conn):
    h.record_sample(_usage(1000.0, 1000, 100), conn=conn)
    h.record_sample(_usage(1010.0, 3000, 300), conn=conn)
    out = h.query_history("1h", conn=conn, now=1010.0)
    assert out["range"] == "1h"
    assert out["bucket_s"] == 0
    assert out["sample_count"] == 2
    last = out["points"][-1]
    assert last["rx_rate"] == pytest.approx(200.0)   # 2000 B / 10 s
    assert last["tx_rate"] == pytest.approx(20.0)
    nic0 = next(s for s in out["interfaces"]["host:pve-b"] if s["iface"] == "nic0")
    assert nic0["series"][-1]["rx_rate"] == pytest.approx(200.0)
    assert out["disks"]["host:pve-b"][-1]["pct"] == pytest.approx(10.0)


def test_query_history_rejects_unknown_range(conn):
    with pytest.raises(ValueError):
        h.query_history("bogus", conn=conn)


def test_query_history_empty_is_safe(conn):
    out = h.query_history("24h", conn=conn, now=100.0)
    assert out["points"] == []
    assert out["sample_count"] == 0


def test_query_history_24h_buckets(conn):
    for i in range(4):
        h.record_sample(_usage(1000.0 + i * 300, 1000 + i * 100, 100), conn=conn)
    out = h.query_history("24h", conn=conn, now=2200.0)
    assert out["bucket_s"] == 300
    assert len(out["points"]) <= 4


def test_ensure_sampled_min_interval(conn):
    first = h.ensure_sampled(_usage(5000.0, 10, 10), now=5000.0, min_interval=30, conn=conn)
    assert first["sampled"] is True
    second = h.ensure_sampled(_usage(5010.0, 20, 20), now=5010.0, min_interval=30, conn=conn)
    assert second["sampled"] is False
    assert second["reason"] == "min_interval"
    third = h.ensure_sampled(_usage(5040.0, 30, 30), now=5040.0, min_interval=30, conn=conn)
    assert third["sampled"] is True


def test_ensure_sampled_no_data(conn):
    assert h.ensure_sampled({"ts": 1.0, "hosts": []}, now=2.0, conn=conn)["sampled"] is False


def test_prune_removes_old_rows(conn, monkeypatch):
    monkeypatch.setattr(h, "_RETENTION_DAYS", 1.0)
    h.record_sample(_usage(1000.0, 1, 1), conn=conn)
    h.record_sample(_usage(200000.0, 1, 1), conn=conn)
    assert conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 1


def test_stats_shape(conn):
    h.record_sample(_usage(1000.0, 1, 1), conn=conn)
    s = h.stats(conn=conn)
    assert s["samples"] == 1
    assert s["last_ts"] == 1000.0
    assert s["db"].endswith("infra_usage_history.db")


def test_route_registered():
    from core.cc_extra_routes import cc_extra_router
    paths = {r.path for r in cc_extra_router.routes}
    assert "/api/infra/usage/history" in paths
