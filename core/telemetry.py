"""Unified telemetry — KAI 2.0 (§75 #55–#58).

One structured snapshot across the workforce: **worker**, **model**,
**mission**, and **teammate** telemetry. Read-only over the existing runtime
memory files; never a competing source of truth. Tolerant of the several
``records`` shapes the stores use.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

SCHEMA = 1

_LIST_KEYS = ("records", "teammates", "workers", "missions", "items", "list")


def _load(memory_dir, name):
    path = Path(memory_dir) / f"{name}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _unwrap(data, list_key):
    """Return the record list from the store's varied wrapping.

    Handles ``{"records":[..]}``, ``{"records":{"records":[..]}}`` (the
    double-wrapped worker store), ``{"<list_key>":[..]}`` and bare lists.
    """
    r = data
    for _ in range(4):
        if isinstance(r, dict) and isinstance(r.get("records"), (dict, list)):
            r = r["records"]
            continue
        break
    if isinstance(r, list):
        return r
    if isinstance(r, dict):
        for key in (list_key,) + _LIST_KEYS:
            value = r.get(key)
            if isinstance(value, list):
                return value
            # some stores key records by id: {"d9ac3c0e8ae4": {...}, ...}
            if isinstance(value, dict) and value and all(
                    isinstance(v, dict) for v in value.values()):
                return list(value.values())
        for value in r.values():
            if isinstance(value, list):
                return value
    return []


def _status_counts(items):
    counts = {}
    for item in items:
        status = str((item or {}).get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def worker_telemetry(memory_dir):
    items = _unwrap(_load(memory_dir, "workers"), "workers")
    return {"count": len(items), "by_status": _status_counts(items)}


def teammate_telemetry(memory_dir):
    items = _unwrap(_load(memory_dir, "teammates"), "teammates")
    return {"count": len(items), "by_status": _status_counts(items)}


def mission_telemetry(memory_dir):
    data = _load(memory_dir, "kai_missions") or _load(memory_dir, "missions")
    items = _unwrap(data, "missions")
    return {"count": len(items), "by_status": _status_counts(items)}


def model_telemetry(memory_dir):
    latency = _load(memory_dir, "provider_latency") or {}
    records = latency.get("records") if isinstance(latency, dict) else {}
    records = records if isinstance(records, dict) else {}
    providers = {}
    for name, value in records.items():
        if isinstance(value, dict):
            providers[name] = {
                "count": value.get("count"),
                "last_ms": value.get("last_duration_ms"),
                "ema_ms": value.get("ema_ms"),
            }
    return {"providers_seen": len(providers), "providers": providers}


def snapshot(memory_dir=None) -> dict:
    mem = memory_dir or os.environ.get("AI_ORCHESTRATOR_MEMORY_DIR", "memory")
    return {
        "schema": SCHEMA,
        "generated_at": time.time(),
        "worker": worker_telemetry(mem),
        "model": model_telemetry(mem),
        "mission": mission_telemetry(mem),
        "teammate": teammate_telemetry(mem),
    }


if __name__ == "__main__":  # pragma: no cover - manual inspection
    from pprint import pprint
    pprint(snapshot())
