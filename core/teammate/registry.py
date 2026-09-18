"""Teammate Registry (KAI 2.0 phase 21A — Teammate Factory foundation).

The Teammate Registry is the source of truth for every AI teammate KAI has
created. A teammate is a PERSISTENT specialized identity (Architect, Coder,
Reviewer, Security Engineer …) — distinct from a WORKER (execution instance)
and from a MODEL (intelligence).

Directive: ``uploads/kai-2-0-master-remediation-directive-2026-09-12.md``

Records live in ``memory/teammates.json`` with schema::

    {
      "schema_version": 1,
      "teammates": {
        "<id>": {
          "id": "<12 hex chars>",
          "name": "...",
          "specialization": "coder|architect|reviewer|...",
          "status": "READY",
          "capabilities": [...],
          "skills": [...],
          "resource_limits": {...},
          "assigned_missions": [...],
          "active_tasks": [...],
          "parent": "<id> | null",
          "children": [...],
          "dependencies": [...],
          "created_at": "<iso8601 UTC>",
          "updated_at": "<iso8601 UTC>",
          "last_active": "<iso8601 UTC>",
          "health": "HEALTHY|DEGRADED|STALLED|FAILED|BLOCKED|QUARANTINED|RECOVERING",
          "performance_metrics": {...},
          "verification_history": [...],
          "security_history": [...],
          "failure_history": [...],
          "audit_references": [...]
        }
      }
    }

All state changes go through :meth:`TeammateRegistry.transition` which
enforces :data:`VALID_TRANSITIONS`. Every transition writes a
``verification_history`` entry and an ``audit_references`` entry (with the
reason string) so the operator can reconstruct the teammate's timeline.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

from core.memory import load, save, update

logger = logging.getLogger(__name__)

_STORE = "teammates.json"
_SCHEMA_VERSION = 1

# Lifecycle states (verbatim from directive section 13 + 21A phase spec)
LIFECYCLE_STATES: tuple[str, ...] = (
    "CREATED",
    "CONFIGURED",
    "READY",
    "ASSIGNED",
    "EXECUTING",
    "WAITING",
    "REVIEW",
    "VERIFIED",
    "COMPLETED",
)

# Failure states (also verbatim). RETIRED is terminal.
FAILURE_STATES: tuple[str, ...] = (
    "FAILED",
    "DEGRADED",
    "BLOCKED",
    "QUARANTINED",
    "RECOVERING",
    "RETIRED",
)

# Any state may transition into a failure state, and RECOVERING → CREATED
# (a recovered teammate resumes as if freshly created — its history is
# retained so operators can inspect it, but the state machine restarts).
_HAPPY_PATH_NEXT: dict[str, tuple[str, ...]] = {
    "CREATED":    ("CONFIGURED", "READY"),
    "CONFIGURED": ("READY",),
    "READY":      ("ASSIGNED",),
    "ASSIGNED":   ("EXECUTING", "WAITING"),
    "EXECUTING":  ("WAITING", "REVIEW"),
    "WAITING":    ("EXECUTING", "REVIEW"),
    "REVIEW":     ("VERIFIED", "EXECUTING"),  # reviewer may bounce back to EXECUTING
    "VERIFIED":   ("COMPLETED",),
    "COMPLETED":  (),  # terminal in the happy path (can still be RETIRED)
}


def _build_valid_transitions() -> dict[str, tuple[str, ...]]:
    """Union happy-path transitions with failure-state transitions.

    Every non-RETIRED state may drop into any failure state EXCEPT RETIRED
    (that requires an explicit :meth:`retire`).  RECOVERING may re-enter
    CREATED to restart the state machine cleanly.
    """
    all_transitions: dict[str, tuple[str, ...]] = {}
    non_retired_failures = tuple(s for s in FAILURE_STATES if s != "RETIRED")
    for state in LIFECYCLE_STATES:
        allowed = list(_HAPPY_PATH_NEXT.get(state, ()))
        # Any lifecycle state can move into any failure class (except RETIRED
        # which is only reachable via .retire()).
        for f in non_retired_failures:
            if f not in allowed:
                allowed.append(f)
        all_transitions[state] = tuple(allowed)

    # Failure states: DEGRADED/BLOCKED/QUARANTINED may recover; FAILED may
    # be retried; RECOVERING re-enters CREATED; RETIRED is terminal.
    all_transitions["DEGRADED"]    = ("RECOVERING", "QUARANTINED", "FAILED", "RETIRED")
    all_transitions["BLOCKED"]     = ("RECOVERING", "READY", "RETIRED")
    all_transitions["QUARANTINED"] = ("RECOVERING", "RETIRED")
    all_transitions["FAILED"]      = ("RECOVERING", "RETIRED")
    all_transitions["RECOVERING"]  = ("CREATED", "READY", "FAILED", "RETIRED")
    all_transitions["RETIRED"]     = ()  # terminal
    return all_transitions


VALID_TRANSITIONS: dict[str, tuple[str, ...]] = _build_valid_transitions()

# Failure class → resulting state.  Registry uses this in record_failure.
_FAILURE_CLASS_TO_STATE: dict[str, str] = {
    "resource_exhaustion":     "DEGRADED",
    "malformed_output":        "DEGRADED",
    "repeated_failures":       "FAILED",
    "no_progress":             "STALLED_TO_DEGRADED",  # STALLED not in FAILURE_STATES → map to DEGRADED
    "tool_failure":            "DEGRADED",
    "model_failure":           "DEGRADED",
    "unauthorized_action":     "QUARANTINED",
    "security_violation":      "QUARANTINED",
    "policy_violation":        "QUARANTINED",
    "circular_behavior":       "DEGRADED",
    "excessive_retries":       "FAILED",
    "critical":                "FAILED",
    "blocked_dependency":      "BLOCKED",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Teammate:
    """Persistent teammate record."""
    id: str
    name: str
    specialization: str
    status: str = "CREATED"
    capabilities: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    resource_limits: dict[str, Any] = field(default_factory=dict)
    assigned_missions: list[str] = field(default_factory=list)
    active_tasks: list[str] = field(default_factory=list)
    parent: str | None = None
    children: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    last_active: str = ""
    health: str = "HEALTHY"
    performance_metrics: dict[str, Any] = field(default_factory=dict)
    verification_history: list[dict[str, Any]] = field(default_factory=list)
    security_history: list[dict[str, Any]] = field(default_factory=list)
    failure_history: list[dict[str, Any]] = field(default_factory=list)
    audit_references: list[dict[str, Any]] = field(default_factory=list)


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class TeammateRegistry:
    """Source of truth for KAI teammates. All mutations persist immediately."""

    def __init__(self) -> None:
        self._teammates: dict[str, Teammate] = {}
        self.load()

    # ── persistence ────────────────────────────────────────────────────────
    def load(self) -> None:
        raw = load(_STORE)
        if not isinstance(raw, dict) or "teammates" not in raw:
            self._teammates = {}
            return
        entries = raw.get("teammates") or {}
        if not isinstance(entries, dict):
            self._teammates = {}
            return
        loaded: dict[str, Teammate] = {}
        for tid, rec in entries.items():
            if not isinstance(rec, dict):
                continue
            try:
                loaded[tid] = Teammate(**{k: rec.get(k) if k in rec else _default_for(k)
                                          for k in Teammate.__dataclass_fields__.keys()})
            except Exception as e:
                logger.warning("teammate %s corrupted, skipping: %s", tid, e)
                continue
        self._teammates = loaded

    def save(self) -> None:
        """Merge this process's teammates into the on-disk store atomically.

        The registry keeps an in-memory cache, but the API and
        ``kai-scheduler`` are separate processes with separate caches. A
        plain whole-file overwrite would let the slower writer erase the
        other's newly created teammates. Reuse the fcntl.flock-backed
        :func:`core.memory.update` and merge per-record instead (directive
        §21/§52); records are keyed by unique id, so a merge never drops a
        concurrent create.
        """
        records = {tid: asdict(t) for tid, t in self._teammates.items()}

        def _merge(current: dict) -> dict:
            if not isinstance(current, dict) or not isinstance(current.get("teammates"), dict):
                current = {"schema_version": _SCHEMA_VERSION, "teammates": {}}
            current.setdefault("teammates", {})
            current["teammates"].update(records)
            current["schema_version"] = _SCHEMA_VERSION
            return current

        update(_STORE, _merge)

    # ── mutations ──────────────────────────────────────────────────────────
    def create(self, spec: dict[str, Any]) -> Teammate:
        """Materialize a teammate from a spec dict. Requires ``name`` +
        ``specialization``. All other fields optional. Returns the record;
        state is CREATED.
        """
        if not spec.get("name") or not spec.get("specialization"):
            raise ValueError("spec.name and spec.specialization are required")
        now = _now_iso()
        tid = _new_id()
        # Guard against the (astronomically improbable) collision.
        while tid in self._teammates:
            tid = _new_id()
        t = Teammate(
            id=tid,
            name=str(spec["name"]),
            specialization=str(spec["specialization"]),
            status="CREATED",
            capabilities=list(spec.get("capabilities") or []),
            skills=list(spec.get("skills") or []),
            resource_limits=dict(spec.get("resource_limits") or {}),
            parent=spec.get("parent"),
            dependencies=list(spec.get("dependencies") or []),
            created_at=now,
            updated_at=now,
            last_active=now,
            health="HEALTHY",
        )
        t.audit_references.append({"event": "create", "at": now, "reason": "initial create"})
        self._teammates[tid] = t
        self.save()
        return t

    def get(self, teammate_id: str) -> Teammate | None:
        return self._teammates.get(teammate_id)

    def list(self, status: str | None = None,
             specialization: str | None = None) -> list[Teammate]:
        results = list(self._teammates.values())
        if status:
            results = [t for t in results if t.status == status]
        if specialization:
            results = [t for t in results if t.specialization == specialization]
        return results

    def transition(self, teammate_id: str, new_state: str,
                   reason: str) -> Teammate | None:
        """Enforce the state machine. Returns the updated teammate or None
        if the teammate is unknown; raises ValueError for an illegal move.
        """
        t = self._teammates.get(teammate_id)
        if t is None:
            return None
        allowed = VALID_TRANSITIONS.get(t.status, ())
        if new_state not in allowed:
            raise ValueError(
                f"illegal transition {t.status} → {new_state} "
                f"(allowed: {list(allowed)})"
            )
        now = _now_iso()
        prev = t.status
        t.status = new_state
        t.updated_at = now
        t.last_active = now
        entry = {"from": prev, "to": new_state, "at": now, "reason": reason}
        t.verification_history.append(entry)
        t.audit_references.append({"event": "transition", **entry})
        self.save()
        return t

    def assign_mission(self, teammate_id: str, mission_id: str) -> bool:
        """Attach a mission and, if the teammate is READY, transition to
        ASSIGNED. Returns True on success; False if teammate is not READY
        or does not exist.
        """
        t = self._teammates.get(teammate_id)
        if t is None or t.status != "READY":
            return False
        if mission_id not in t.assigned_missions:
            t.assigned_missions.append(mission_id)
        self.transition(teammate_id, "ASSIGNED", f"mission {mission_id} assigned")
        return True

    def add_skill(self, teammate_id: str, skill_id: str,
                  skill_registry: Any) -> bool:
        """Link a skill_id from the global SkillRegistry onto a teammate.

        Resolves the skill via the registry (never duplicates skill
        definitions), and returns True only when the skill was newly added.
        """
        skill = skill_registry.get(skill_id)
        if skill is None:
            return False
        t = self._teammates.get(teammate_id)
        if t is None:
            return False
        if skill_id in t.skills:
            return False
        t.skills.append(skill_id)
        t.updated_at = _now_iso()
        t.audit_references.append({"event": "add_skill", "skill_id": skill_id,
                                   "at": _now_iso()})
        self.save()
        return True

    def record_failure(self, teammate_id: str, cls: str, detail: str) -> None:
        """Log a failure and move the teammate to the state implied by
        ``cls``. Unknown classes default to DEGRADED. If already in a
        terminal failure state (RETIRED / COMPLETED), only appends history.
        """
        t = self._teammates.get(teammate_id)
        if t is None:
            return
        now = _now_iso()
        t.failure_history.append({
            "class": cls, "detail": detail, "at": now, "prev_status": t.status,
        })
        target = _FAILURE_CLASS_TO_STATE.get(cls, "DEGRADED")
        # Map the pseudo-state we used for no_progress back to DEGRADED.
        if target == "STALLED_TO_DEGRADED":
            target = "DEGRADED"
        # If we're already in a state that has no allowed transitions
        # (RETIRED, COMPLETED), just persist the history.
        if not VALID_TRANSITIONS.get(t.status):
            t.updated_at = now
            self.save()
            return
        # Only transition if the failure class is a legal next step.
        if target in VALID_TRANSITIONS.get(t.status, ()):
            self.transition(teammate_id, target, f"failure: {cls} — {detail}")
        else:
            # Persist without illegal-transition error; log inline.
            t.audit_references.append({
                "event": "failure_no_transition", "class": cls, "detail": detail,
                "at": now, "state_kept": t.status,
            })
            t.updated_at = now
            self.save()

    def record_performance(self, teammate_id: str, *, success: bool,
                           latency_ms: float = 0.0, model: str = "",
                           skill_id: str = "", retries: int = 0
                           ) -> dict[str, Any] | None:
        """Accumulate per-teammate performance metrics (§40/§41).

        Metrics live on the teammate record (persisted in ``teammates.json``)
        so the Command Center, the learning signal and any process can read
        them. ``history`` is bounded so the record never grows without limit.
        """
        t = self._teammates.get(teammate_id)
        if t is None:
            return None
        pm = dict(t.performance_metrics or {})
        completed = int(pm.get("tasks_completed", 0)) + (1 if success else 0)
        failed = int(pm.get("tasks_failed", 0)) + (0 if success else 1)
        total_latency = float(pm.get("total_latency_ms", 0.0)) + float(latency_ms or 0.0)
        attempts = completed + failed
        pm["tasks_completed"] = completed
        pm["tasks_failed"] = failed
        pm["retries"] = int(pm.get("retries", 0)) + int(retries or 0)
        pm["total_latency_ms"] = round(total_latency, 2)
        pm["avg_latency_ms"] = round(total_latency / attempts, 2) if attempts else 0.0
        pm["success_rate"] = round(completed / attempts, 3) if attempts else 0.0
        if model:
            models = dict(pm.get("models") or {})
            m = dict(models.get(model) or {"completed": 0, "failed": 0,
                                           "total_latency_ms": 0.0})
            m["completed"] = int(m.get("completed", 0)) + (1 if success else 0)
            m["failed"] = int(m.get("failed", 0)) + (0 if success else 1)
            m["total_latency_ms"] = round(
                float(m.get("total_latency_ms", 0.0)) + float(latency_ms or 0.0), 2)
            models[model] = m
            pm["models"] = models
            pm["last_model"] = model
        now = _now_iso()
        pm["last_task_at"] = now
        history = list(pm.get("history") or [])
        history.append({"skill_id": skill_id, "success": bool(success),
                        "latency_ms": round(float(latency_ms or 0.0), 2),
                        "model": model, "at": now})
        pm["history"] = history[-20:]
        t.performance_metrics = pm
        t.last_active = now
        t.updated_at = now
        self.save()
        return pm

    def health_report(self) -> dict:
        """§27: per-teammate health + success/error rates and an aggregate.

        Read-only projection over the persisted teammate records (which carry
        the §41 performance metrics). ``alive`` is about process-level
        liveness as far as the registry can tell; ``ready``/``busy`` mirror
        the lifecycle state; ``last_heartbeat`` is the last activity stamp.
        """
        teammates: dict[str, Any] = {}
        by_status: dict[str, int] = {}
        by_health: dict[str, int] = {}
        for t in self._teammates.values():
            pm = t.performance_metrics or {}
            completed = int(pm.get("tasks_completed", 0))
            failed = int(pm.get("tasks_failed", 0))
            attempts = completed + failed
            success_rate = round(completed / attempts, 3) if attempts else 0.0
            status = t.status
            # The lifecycle state is authoritative: failure states are stored
            # on ``status`` while ``health`` is a coarse override that defaults
            # HEALTHY, so derive health from status when no override is set.
            health = t.health if (t.health and t.health != "HEALTHY") else (
                status if status in ("DEGRADED", "FAILED", "BLOCKED",
                                     "QUARANTINED", "RECOVERING") else "HEALTHY")
            by_status[status] = by_status.get(status, 0) + 1
            by_health[health] = by_health.get(health, 0) + 1
            teammates[t.id] = {
                "id": t.id,
                "name": t.name,
                "specialization": t.specialization,
                "status": status,
                "health": health,
                "alive": status != "RETIRED",
                "ready": status == "READY",
                "busy": status in ("ASSIGNED", "EXECUTING"),
                "current_missions": list(t.assigned_missions or []),
                "active_tasks": list(t.active_tasks or []),
                "last_heartbeat": t.last_active or t.updated_at or t.created_at,
                "tasks_completed": completed,
                "tasks_failed": failed,
                "success_rate": success_rate,
                "error_rate": round(1.0 - success_rate, 3) if attempts else None,
                "avg_latency_ms": pm.get("avg_latency_ms", 0.0),
                "retries": int(pm.get("retries", 0)),
                "last_model": pm.get("last_model"),
                "models": dict(pm.get("models") or {}),
                "last_task_at": pm.get("last_task_at"),
                "failure_history": len(t.failure_history or []),
                "quarantined": health == "QUARANTINED",
            }
        return {
            "schema": 1,
            "counts": {
                "total": len(self._teammates),
                "alive": sum(1 for r in teammates.values() if r["alive"]),
                "ready": sum(1 for r in teammates.values() if r["ready"]),
                "busy": sum(1 for r in teammates.values() if r["busy"]),
                "unhealthy": sum(1 for r in teammates.values()
                                 if r["health"] not in ("HEALTHY",)),
                "quarantined": sum(1 for r in teammates.values()
                                   if r["quarantined"]),
            },
            "by_status": by_status,
            "by_health": by_health,
            "teammates": teammates,
        }

    def retire(self, teammate_id: str, reason: str) -> None:
        """Force RETIRED state. Terminal — subsequent transitions raise."""
        t = self._teammates.get(teammate_id)
        if t is None:
            return
        # RETIRED is reachable from anywhere per absolute rule (audit trail
        # preserves history).  Bypass VALID_TRANSITIONS check for RETIRED
        # only.
        now = _now_iso()
        prev = t.status
        t.status = "RETIRED"
        t.updated_at = now
        entry = {"from": prev, "to": "RETIRED", "at": now, "reason": reason}
        t.verification_history.append(entry)
        t.audit_references.append({"event": "retire", **entry})
        self.save()


def _default_for(field_name: str) -> Any:
    """Return a sensible default for a Teammate field so old records that
    lack newer fields still load cleanly.
    """
    dc_field = Teammate.__dataclass_fields__.get(field_name)
    if dc_field is None:
        return None
    if dc_field.default_factory is not None and callable(dc_field.default_factory):  # type: ignore[misc]
        try:
            return dc_field.default_factory()  # type: ignore[misc]
        except Exception:
            pass
    return dc_field.default if dc_field.default is not None else None
