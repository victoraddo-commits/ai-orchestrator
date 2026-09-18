"""§37 MODULE INTEGRATION — the one seam every KAI module consumes.

A module never builds its own agent framework. It calls
:meth:`ModuleBridge.request_capability` (or the equivalent API route); KAI then:

    1. resolves the module capability to a specialization + reusable skills
       (``core.integration.module_catalog``),
    2. reuses an existing healthy teammate or creates one through the one
       teammate Factory (``core.teammate.runtime`` -> ``factory``),
    3. runs the work as a mission through the one mission engine
       (``core.teammate.engine.WorkforceEngine``), which routes every prompt
       through ``ai_router`` / the Model Fabric under AgentGuard,
    4. records the outcome back into the module's Second Brain record and a
       durable module journal, emitting events on ``core.kai_event_bus``.

Exposure is the mirror image: :meth:`ensure_exposed` registers every module's
declared skills into the one Skill Registry and every module as a worker in the
one Workforce Registry. Nothing is duplicated — the bridge is a thin adapter.
"""
from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from core.memory import load as _memory_load, save as _memory_save, update as _memory_update
from core.integration import module_catalog as catalog
from core.integration.outcomes import OutcomeRecorder

logger = logging.getLogger(__name__)

REQUESTS_STORE = "module_integration.json"
SCHEMA_VERSION = 1
SOURCE = "module_bridge"

_LOCK = threading.RLock()
_BRIDGE: Optional["ModuleBridge"] = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ModuleBridge:
    """Routes module capability requests into KAI's unified workforce."""

    def __init__(self, runtime: Any = None, engine: Any = None, bus: Any = None,
                 stores_base: Any = None,
                 requests_store: str = REQUESTS_STORE) -> None:
        if runtime is None:
            from core.teammate.runtime import get_runtime
            runtime = get_runtime()
        self.runtime = runtime
        if engine is None:
            from core.teammate.engine import WorkforceEngine
            engine = WorkforceEngine(runtime=runtime)
        self.engine = engine
        self.bus = bus if bus is not None else getattr(runtime, "bus", None)
        self.requests_store = requests_store

        from core.second_brain import SecondBrainWriter
        self._writer = SecondBrainWriter(stores_base)
        self._lock = threading.RLock()
        self._exposed = False
        self._written_entities: set[str] = set()
        self.recorder = OutcomeRecorder(engine=self.engine, bridge=self,
                                        bus=self.bus)

    # ── events ──────────────────────────────────────────────────────────────
    def _emit(self, topic: str, payload: dict) -> None:
        if self.bus is None:
            return
        try:
            self.bus.publish(topic, payload, source=SOURCE)
        except Exception:
            logger.debug("event publish failed for %s", topic, exc_info=True)

    # ── exposure (modules register capability with the workforce) ───────────
    def ensure_exposed(self) -> dict:
        """Idempotently register module skills + module workers. Returns counts."""
        with self._lock:
            if self._exposed:
                return self._summary()
            skills = 0
            for skill_id, spec in catalog.MODULE_SKILLS.items():
                if self._upsert_skill(spec):
                    skills += 1

            from core.workforce import registry as workforce
            for name, entry in catalog.MODULE_CATALOG.items():
                workforce.register(workforce.WorkerRecord(
                    worker_id=f"module:{name}",
                    kind="module",
                    capabilities=list(entry["capabilities"]),
                    permissions={"secrets": [], "network": ["module-apis"],
                                 "filesystem": []},
                    limits={"max_concurrency": 1, "timeout_seconds": 600},
                    metadata={"specialization": entry["specialization"],
                              "expertise": entry["expertise"]},
                    tools=[],
                    data_scope=["module"],
                    vault_scope=[],
                ))
            self._exposed = True
            summary = self._summary()
            self._emit("module.capabilities.exposed", summary)
            logger.info("module integration exposed: %s", summary)
            return summary

    def _upsert_skill(self, spec: dict) -> bool:
        from core.teammate.skills import SkillRecord
        existing = self.runtime.skills.get(spec["skill_id"])
        if existing is not None and existing.to_dict() == spec:
            return False
        self.runtime.skills.upsert(SkillRecord.from_dict(spec))
        return True

    def _summary(self) -> dict:
        return {
            "modules": len(catalog.MODULE_CATALOG),
            "skills": len(catalog.MODULE_SKILLS),
            "module_names": catalog.module_names(),
        }

    # ── capability lookup ───────────────────────────────────────────────────
    def _resolve_entry(self, module: str, register: bool = True) -> dict:
        """Resolve a module's catalog entry (registering its skills on demand)."""
        entry = catalog.catalog_entry(module)
        if entry is not None:
            if register:
                for skill_id in entry["skills"]:
                    spec = catalog.MODULE_SKILLS.get(skill_id)
                    if spec is not None:
                        self._upsert_skill(spec)
            return entry
        from core.module_registry import get_registered_modules
        modules = get_registered_modules()
        descriptor = modules.get(catalog.normalize_module(module)) or modules.get(module)
        if descriptor is None:
            raise KeyError(f"unknown module: {module}")
        entry = catalog.synthesize_entry(module, descriptor)
        if register:
            self._upsert_skill(entry["skill"])
        return entry

    def list_module_capabilities(self, module: Optional[str] = None) -> Any:
        """Return the capability surface of one module or every module."""
        if module is not None:
            entry = self._resolve_entry(module, register=False)
            return {
                "module": catalog.normalize_module(module),
                "specialization": entry["specialization"],
                "expertise": entry["expertise"],
                "skills": list(entry["skills"]),
                "capabilities": list(entry["capabilities"]),
            }
        rows = []
        for name in catalog.module_names():
            rows.append(self.list_module_capabilities(name))
        from core.module_registry import get_registered_modules
        known = {r["module"] for r in rows}
        for name in get_registered_modules():
            norm = catalog.normalize_module(name)
            if norm in known:
                continue
            rows.append(self.list_module_capabilities(name))
        return rows

    # ── the request path ────────────────────────────────────────────────────
    def request_capability(self, module: str, capability: str,
                           objective: Optional[str] = None,
                           execute: bool = True, background: bool = False,
                           project_path: Optional[str] = None,
                           skills: Optional[list] = None) -> dict:
        """Ask KAI for a module capability; returns the teammate + mission."""
        if not module:
            raise ValueError("module is required")
        if not capability:
            raise ValueError("capability is required")

        entry = self._resolve_entry(module)
        allowed = [s for s in entry["skills"] if self.runtime.skills.get(s)]
        if skills:
            skills = [s for s in skills if s in allowed]
            if not skills:
                raise ValueError(
                    f"module {module!r} does not expose skills {skills!r}")
        else:
            skills = allowed
        if not skills:
            raise ValueError(f"module {module!r} exposes no registered skills")

        specialization = entry["specialization"]
        mate_result = self.runtime.create_teammate(specialization, skills=skills)
        teammate_id = getattr(mate_result.teammate, "id", None)

        mission_id = f"mis-{uuid.uuid4().hex[:10]}"
        goal = objective or f"{entry['expertise']} for {module}"
        self._put_request({
            "mission_id": mission_id,
            "module": catalog.normalize_module(module),
            "capability": capability,
            "goal": goal,
            "specialization": specialization,
            "skills": skills,
            "teammate_id": teammate_id,
            "created_teammate": bool(getattr(mate_result, "created", False)),
            "status": "requested",
            "requested_at": _now(),
            "second_brain_record_id": "",
        })
        self._emit("module.capability.requested", {
            "module": catalog.normalize_module(module),
            "capability": capability,
            "mission_id": mission_id,
            "teammate_id": teammate_id,
            "specialization": specialization,
            "reused": not bool(getattr(mate_result, "created", False)),
        })

        mission = self.engine.create_mission(
            goal, execute=execute, background=background,
            project_path=project_path, skills=skills,
            specialization=specialization, mission_id=mission_id)

        return {
            "module": catalog.normalize_module(module),
            "capability": capability,
            "specialization": specialization,
            "skills": skills,
            "teammate_id": teammate_id,
            "created": bool(getattr(mate_result, "created", False)),
            "mission_id": mission_id,
            "mission_status": (mission or {}).get("status"),
            "mission": mission,
        }

    # ── outcome recording (mission -> module Second Brain/registry) ─────────
    def record_outcome(self, mission_id: str, mission: Optional[dict],
                       topic: str = "") -> Optional[dict]:
        entry = self.get_request(mission_id)
        if entry is None:
            return None

        mission = mission or {}
        status = mission.get("status", "UNKNOWN")
        tasks = mission.get("tasks") or []
        verification = mission.get("verification") or {}
        fact = {
            "module": entry["module"],
            "capability": entry["capability"],
            "mission_id": mission_id,
            "goal": entry.get("goal", ""),
            "status": status,
            "verified": bool(verification.get("passed")),
            "teammate_ids": list(mission.get("team_member_ids") or
                                 [entry.get("teammate_id")]),
            "tasks": [{"skill_id": t.get("skill_id"), "status": t.get("status")}
                      for t in tasks],
            "event": topic,
        }
        entity = f"module:{entry['module']}:{entry['capability']}"
        record_id = ""
        try:
            from core.second_brain import MemoryType, ChangeType, Confidence, SourceAuthority
            change = (ChangeType.UPDATED if entity in self._written_entities
                      else ChangeType.CREATED)
            record_id = self._writer.update(
                store_name="operational",
                entity=entity,
                entity_type="module_capability",
                memory_type=MemoryType.OPERATIONAL,
                fact=fact,
                changed_reason=f"module capability mission {status}",
                change_type=change,
                confidence=Confidence.CONFIRMED,
                source_authority=SourceAuthority.LIVE_SYSTEM,
                metadata={"module": entry["module"],
                          "capability": entry["capability"],
                          "mission_id": mission_id},
            )
            self._written_entities.add(entity)
        except Exception as exc:  # outcome recording must never break the bus
            logger.warning("second brain outcome write failed: %s", exc)

        entry.update({
            "status": status,
            "verified": bool(verification.get("passed")),
            "second_brain_record_id": record_id,
            "event": topic,
            "completed_at": _now(),
        })
        self._put_request(entry)
        self._emit("module.mission.recorded", {
            "module": entry["module"],
            "capability": entry["capability"],
            "mission_id": mission_id,
            "status": status,
            "verified": fact["verified"],
            "second_brain_record_id": record_id,
        })
        return entry

    # ── journal persistence ─────────────────────────────────────────────────
    def _load(self) -> dict:
        raw = _memory_load(self.requests_store)
        if not isinstance(raw, dict) or not isinstance(raw.get("requests"), dict):
            return {"schema_version": SCHEMA_VERSION, "requests": {}}
        return raw

    def _save(self, data: dict) -> None:
        data["schema_version"] = SCHEMA_VERSION
        _memory_save(self.requests_store, data)

    def _put_request(self, entry: dict) -> None:
        # Atomic cross-process read-modify-write: the outcome recorder thread
        # and the API/scheduler processes must not clobber each other's
        # module-request journal (directive §21/§52).
        def _apply(data: dict) -> dict:
            if not isinstance(data, dict) or not isinstance(data.get("requests"), dict):
                data = {"schema_version": SCHEMA_VERSION, "requests": {}}
            data["schema_version"] = SCHEMA_VERSION
            data["requests"][entry["mission_id"]] = entry
            return data

        with self._lock:
            _memory_update(self.requests_store, _apply)

    def get_request(self, mission_id: str) -> Optional[dict]:
        return self._load()["requests"].get(mission_id)

    def list_requests(self, module: Optional[str] = None) -> list[dict]:
        rows = list(self._load()["requests"].values())
        if module:
            norm = catalog.normalize_module(module)
            rows = [r for r in rows if r.get("module") == norm]
        return sorted(rows, key=lambda r: r.get("requested_at", ""), reverse=True)

    def close(self) -> None:
        try:
            self.recorder.stop()
        except Exception:
            pass


# ── process-wide singleton ──────────────────────────────────────────────────
def get_bridge() -> ModuleBridge:
    global _BRIDGE
    if _BRIDGE is None:
        with _LOCK:
            if _BRIDGE is None:
                _BRIDGE = ModuleBridge()
    return _BRIDGE


def reset_bridge() -> None:
    """Drop the singleton (tests / controlled rebuilds only)."""
    global _BRIDGE
    with _LOCK:
        if _BRIDGE is not None:
            _BRIDGE.close()
        _BRIDGE = None
