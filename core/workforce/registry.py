"""Worker registry — persistent identity records for every orchestrator
worker class (pool slots, providers, local models, roles).

Persistence goes through core.memory (atomic writes, .bak backups, and —
critically — automatic redirection to a temp dir under pytest via the
conftest isolated_memory fixture). Schema mirrors memory/agents.json.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

from core.memory import load as _memory_load, save as _memory_save

REGISTRY_FILE = "workers.json"
SCHEMA_VERSION = 1

_LOCK = threading.Lock()

_VALID_STATUS = ("idle", "busy", "degraded", "dead", "paused")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class WorkerRecord:
    worker_id: str
    kind: str                       # pool_worker | provider | role | local_model
    capabilities: list              # e.g. ["generate", "review", "deploy"]
    permissions: dict               # {"secrets": [...], "network": [...], "filesystem": []}
    limits: dict                    # {"max_concurrency", "timeout_seconds", "max_cost_usd"}
    environment: str = "production" # production | development
    temporary: bool = False         # temporary model — auto-expires
    expires_at: Optional[str] = None
    status: str = "idle"
    health: dict = field(default_factory=lambda: {
        "last_heartbeat": None,
        "consecutive_failures": 0,
        "last_reason": None,
        "circuit_state": "closed",
        "transitions": [],
    })
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "WorkerRecord":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})


def _load_all() -> dict:
    data = _memory_load(REGISTRY_FILE)
    if isinstance(data, dict) and isinstance(data.get("records"), list):
        return data
    return {"schema_version": SCHEMA_VERSION, "records": []}


def _save_all(data: dict) -> None:
    _memory_save(REGISTRY_FILE, data)


def register(record: WorkerRecord) -> None:
    """Insert or upsert a worker. Upsert preserves runtime state (status,
    health) of an existing record but refreshes the static definition."""
    with _LOCK:
        data = _load_all()
        for i, existing in enumerate(data["records"]):
            if existing.get("worker_id") == record.worker_id:
                fresh = record.to_dict()
                fresh["status"] = existing.get("status", "idle")
                fresh["health"] = existing.get("health") or record.health
                data["records"][i] = fresh
                _save_all(data)
                return
        data["records"].append(record.to_dict())
        _save_all(data)


def get(worker_id: str) -> Optional[WorkerRecord]:
    data = _load_all()
    for existing in data["records"]:
        if existing.get("worker_id") == worker_id:
            return WorkerRecord.from_dict(existing)
    return None


def list_workers(kind: Optional[str] = None,
                 environment: Optional[str] = None,
                 status: Optional[str] = None) -> list:
    data = _load_all()
    out = []
    for existing in data["records"]:
        if kind and existing.get("kind") != kind:
            continue
        if environment and existing.get("environment") != environment:
            continue
        if status and existing.get("status") != status:
            continue
        out.append(WorkerRecord.from_dict(existing))
    return out


def update_status(worker_id: str, new_status: str, reason: str = None,
                  increment_failures: bool = False) -> bool:
    """Transition a worker's status with audited history. Returns False if
    the worker is unknown or the status is invalid."""
    if new_status not in _VALID_STATUS:
        return False
    with _LOCK:
        data = _load_all()
        for existing in data["records"]:
            if existing.get("worker_id") != worker_id:
                continue
            old = existing.get("status", "idle")
            health = existing.get("health") or {}
            transitions = health.get("transitions") or []
            transitions.append({"at": _now(), "from": old, "to": new_status,
                                "reason": reason})
            health["transitions"] = transitions[-20:]
            health["last_reason"] = reason
            if increment_failures:
                health["consecutive_failures"] = \
                    int(health.get("consecutive_failures", 0)) + 1
            existing["status"] = new_status
            existing["health"] = health
            _save_all(data)
            return True
    return False


def record_heartbeat(worker_id: str) -> bool:
    """Mark liveness; resets consecutive failures. Does NOT auto-revive a
    dead/paused worker — recovery does that explicitly."""
    with _LOCK:
        data = _load_all()
        for existing in data["records"]:
            if existing.get("worker_id") != worker_id:
                continue
            health = existing.get("health") or {}
            health["last_heartbeat"] = _now()
            health["consecutive_failures"] = 0
            existing["health"] = health
            _save_all(data)
            return True
    return False


def set_circuit_state(worker_id: str, circuit_state: str) -> bool:
    with _LOCK:
        data = _load_all()
        for existing in data["records"]:
            if existing.get("worker_id") != worker_id:
                continue
            health = existing.get("health") or {}
            health["circuit_state"] = circuit_state
            existing["health"] = health
            _save_all(data)
            return True
    return False


def revive(worker_id: str) -> bool:
    """Recovery path: return a worker to idle after verified healing."""
    return update_status(worker_id, "idle", reason="recovered by self_healing")


def deregister_expired() -> list:
    """Remove expired temporary workers. Returns removed worker_ids."""
    now = _now()
    with _LOCK:
        data = _load_all()
        keep, removed = [], []
        for existing in data["records"]:
            exp = existing.get("expires_at")
            if existing.get("temporary") and exp and exp < now:
                removed.append(existing["worker_id"])
                continue
            keep.append(existing)
        if removed:
            data["records"] = keep
            _save_all(data)
        return removed
