"""Worker Registry integration (TF Phase 3 / 21C).

Binds teammates (core/teammate/registry.py) to workers
(core/workforce/registry.py) and emits worker lifecycle events on the KAI
event bus: worker.started / worker.failed / worker.reassigned /
worker.completed.

Persistence via ``core.memory``: memory/worker_assignments.json, schema v1.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional, Union

from core.memory import load as _memory_load, save as _memory_save
from core.workforce import registry as workforce_registry

logger = logging.getLogger(__name__)

STORE = "worker_assignments.json"
SCHEMA_VERSION = 1

# Transition table (mirrors VALID_TRANSITIONS in core/teammate/registry.py).
# completed / failed / reassigned are terminal.
_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "assigned": {"running", "completed", "failed", "reassigned"},
    "running": {"completed", "failed", "reassigned"},
    "completed": set(),
    "failed": set(),
    "reassigned": set(),
}

_TOPIC_STARTED = "worker.started"
_TOPIC_FAILED = "worker.failed"
_TOPIC_REASSIGNED = "worker.reassigned"
_TOPIC_COMPLETED = "worker.completed"

_SOURCE = "teammate_factory"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class WorkerAssignment:
    teammate_id: str
    worker_id: str
    skill_id: str
    status: str = "assigned"
    started_at: str = field(default_factory=_now_iso)
    last_heartbeat: Optional[str] = None
    attempts: int = 1
    ended_at: Optional[str] = None
    assignment_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    reason: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "WorkerAssignment":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})


class WorkerIntegrator:
    """Owns the teammate⇄worker binding lifecycle and its events."""

    def __init__(self, assignment_store: Optional[dict[str, WorkerAssignment]] = None,
                 bus: Any = None) -> None:
        self._assignments: dict[str, WorkerAssignment] = (
            assignment_store if assignment_store is not None else {})
        self._bus = bus  # injected event bus; default None → module-level publish
        if assignment_store is None:
            self.load()

    # -- persistence ----------------------------------------------------
    def load(self) -> None:
        raw = _memory_load(STORE)
        if not isinstance(raw, dict) or "assignments" not in raw:
            self._assignments = {}
            return
        entries = raw.get("assignments") or {}
        if not isinstance(entries, dict):
            self._assignments = {}
            return
        loaded: dict[str, WorkerAssignment] = {}
        for aid, rec in entries.items():
            if not isinstance(rec, dict):
                logger.warning("assignment %s not a dict, skipping", aid)
                continue
            try:
                parsed = WorkerAssignment.from_dict(rec)
            except Exception as e:
                logger.warning("assignment %s corrupted, skipping: %s", aid, e)
                continue
            loaded[parsed.assignment_id] = parsed
        self._assignments = loaded

    def save(self) -> None:
        _memory_save(STORE, {
            "schema_version": SCHEMA_VERSION,
            "assignments": {aid: a.to_dict() for aid, a in self._assignments.items()},
        })

    # -- internal -------------------------------------------------------
    def _emit(self, topic: str, assignment: WorkerAssignment, **extra) -> None:
        payload = {
            "assignment_id": assignment.assignment_id,
            "teammate_id": assignment.teammate_id,
            "worker_id": assignment.worker_id,
            "skill_id": assignment.skill_id,
            "status": assignment.status,
            "attempts": assignment.attempts,
        }
        if assignment.reason is not None:
            payload["reason"] = assignment.reason
        payload.update(extra)
        if self._bus is not None:
            self._bus.publish(topic, payload, source=_SOURCE)
        else:
            from core.kai_event_bus import publish as bus_publish
            bus_publish(topic, payload, source=_SOURCE)

    def get(self, assignment: Union["WorkerAssignment", str]) -> Optional[WorkerAssignment]:
        key = getattr(assignment, "assignment_id", assignment)
        return self._assignments.get(key)

    def _resolve(self, assignment: Union["WorkerAssignment", str]) -> WorkerAssignment:
        current = self.get(assignment)
        if current is not None:
            return current
        raise KeyError(
            f"unknown assignment {getattr(assignment, 'assignment_id', assignment)}")

    def _guard_transition(self, current: WorkerAssignment, target: str) -> None:
        allowed = _STATUS_TRANSITIONS.get(current.status, set())
        if target not in allowed:
            raise ValueError(
                f"invalid transition {current.status}->{target} "
                f"for {current.assignment_id}")

    def _put(self, assignment: WorkerAssignment) -> WorkerAssignment:
        self._assignments[assignment.assignment_id] = assignment
        self.save()
        return assignment

    # -- lifecycle ------------------------------------------------------
    def assign(self, teammate: Any, worker_id: str, skill_id: str,
               reason: str = "", attempts: int = 1) -> WorkerAssignment:
        """Bind a worker to a teammate. No event fires here — the worker is
        only "started" once execution actually begins (mark_running)."""
        worker = workforce_registry.get(worker_id)
        if worker is None:
            raise ValueError(f"unknown worker: {worker_id}")
        if getattr(teammate, "status", "READY") not in ("READY", "ASSIGNED"):
            raise ValueError(f"teammate {getattr(teammate, 'id', '?')} not ready for assignment")
        assignment = WorkerAssignment(
            teammate_id=str(getattr(teammate, "id", "?")),
            worker_id=worker_id,
            skill_id=skill_id,
            status="assigned",
            attempts=attempts,
            reason=reason or None,
        )
        self._put(assignment)
        return assignment

    def mark_running(self, assignment: Union["WorkerAssignment", str]) -> WorkerAssignment:
        current = self._resolve(assignment)
        self._guard_transition(current, "running")
        current.status = "running"
        current.last_heartbeat = _now_iso()
        self._put(current)
        self._emit(_TOPIC_STARTED, current)
        return current

    def mark_completed(self, assignment: Union["WorkerAssignment", str],
                       result: str = "success") -> WorkerAssignment:
        current = self._resolve(assignment)
        self._guard_transition(current, "completed")
        current.status = "completed"
        current.ended_at = _now_iso()
        current.last_heartbeat = _now_iso()
        self._put(current)
        self._emit(_TOPIC_COMPLETED, current, result=result)
        return current

    def mark_failed(self, assignment: Union["WorkerAssignment", str],
                    reason: str = "") -> WorkerAssignment:
        current = self._resolve(assignment)
        self._guard_transition(current, "failed")
        if reason:
            current.reason = reason
        current.status = "failed"
        current.ended_at = _now_iso()
        current.last_heartbeat = _now_iso()
        self._put(current)
        extra = {"reason": reason} if reason else {}
        self._emit(_TOPIC_FAILED, current, **extra)
        worker = workforce_registry.get(current.worker_id)
        if worker is not None:
            workforce_registry.update_status(
                current.worker_id, "dead", reason=f"assignment failed: {reason}",
                increment_failures=True)
        return current

    def reassign(self, teammate: Any, new_worker_id: str,
                 reason: str = "") -> WorkerAssignment:
        worker = workforce_registry.get(new_worker_id)
        if worker is None:
            raise ValueError(f"unknown worker: {new_worker_id}")
        old = self._last_active_for_teammate(str(getattr(teammate, "id", "?")))
        new = self.assign(teammate, worker_id=new_worker_id,
                          skill_id=old.skill_id if old else "",
                          reason=reason,
                          attempts=old.attempts + 1 if old else 1)
        if old is not None:
            self._guard_transition(old, "reassigned")
            old.status = "reassigned"
            old.ended_at = _now_iso()
            self._put(old)
            self._emit(_TOPIC_REASSIGNED, old, new_worker_id=new_worker_id,
                       new_assignment_id=new.assignment_id, reason=reason or None)
        return new

    def _last_active_for_teammate(self, teammate_id: str) -> Optional[WorkerAssignment]:
        active = [a for a in self._assignments.values()
                  if a.teammate_id == teammate_id
                  and a.status in ("assigned", "running")]
        if not active:
            return None
        return max(active, key=lambda a: a.started_at)

    def track_health(self, teammate: Any, worker_id: str) -> Optional[WorkerAssignment]:
        """Heartbeat + reconcile assignment status from worker health."""
        tid = str(getattr(teammate, "id", "?"))
        current = self._last_active_for_teammate(tid)
        if current is None or current.status not in ("assigned", "running"):
            return current
        worker = workforce_registry.get(worker_id)
        if worker is None:
            return current
        if worker.status == "dead" or worker.health.get("circuit_state") == "open":
            self.mark_failed(current, reason="worker unhealthy")
            return current
        workforce_registry.record_heartbeat(worker_id)
        if current.status == "assigned":
            self.mark_running(current)
        else:
            current.last_heartbeat = _now_iso()
            self._put(current)
        if worker.status == "idle":
            workforce_registry.update_status(
                worker_id, "busy", reason="track_health: assignment active")
        return current
