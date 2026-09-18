"""Teammate Factory (TF Phase 6 / 21G).

Materializes a teammate from a requirement: reuse an existing healthy,
capable, available teammate if one matches, otherwise create + configure a
new one in the registry. Optionally binds a worker (21C) and emits
``teammate.created`` / ``teammate.reused`` on the event bus.

The Factory is the only sanctioned path from a requirement to a live
teammate; it never bypasses the registry or the worker integrator.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

SOURCE = "teammate_factory"
_AVAILABLE_STATES = ("READY", "ASSIGNED")


@dataclass
class FactoryResult:
    teammate: Any
    created: bool
    assignment: Any = None

    def to_dict(self) -> dict:
        return {
            "teammate_id": getattr(self.teammate, "id", None),
            "specialization": getattr(self.teammate, "specialization", None),
            "created": self.created,
            "assignment": getattr(self.assignment, "to_dict", lambda: self.assignment)(),
        }


def _get(req: Any, key: str, default=None):
    if isinstance(req, dict):
        return req.get(key, default)
    return getattr(req, key, default)


def _normalize_requirement(req: Any, mission_id: Optional[str]) -> dict:
    specs = _get(req, "specializations") or []
    specialization = _get(req, "specialization") or (specs[0] if specs else None)
    skills = _get(req, "required_skills") or _get(req, "skills") or []
    capabilities = _get(req, "capabilities") or _get(req, "required_capabilities") or []
    resources = _get(req, "resource_limits") or _get(req, "resource_requirements") or {}
    return {
        "specialization": specialization,
        "required_skills": list(skills),
        "capabilities": list(capabilities),
        "name": _get(req, "name"),
        "resource_limits": dict(resources),
        "dependencies": list(_get(req, "dependencies") or []),
        "mission_id": mission_id or _get(req, "mission_id") or _get(req, "id"),
    }


class Factory:
    def __init__(self, registry=None, integrator=None, bus=None,
                 skill_registry=None) -> None:
        if registry is None:
            from core.teammate.registry import TeammateRegistry
            registry = TeammateRegistry()
        self.registry = registry
        self.integrator = integrator
        self.bus = bus
        self.skill_registry = skill_registry

    # -- events -------------------------------------------------------------
    def _emit(self, topic: str, payload: dict) -> None:
        if self.bus is None:
            return
        publish = getattr(self.bus, "publish", None)
        if callable(publish):
            try:
                publish(topic, payload, source=SOURCE)
            except TypeError:
                publish(topic, payload)
        elif callable(self.bus):
            self.bus(topic, payload)

    # -- reuse --------------------------------------------------------------
    def _find_reusable(self, spec: dict):
        required = set(spec["required_skills"])
        required_caps = set(spec.get("capabilities") or [])
        for t in self.registry.list():
            if getattr(t, "health", "HEALTHY") != "HEALTHY":
                continue
            if getattr(t, "status", None) not in _AVAILABLE_STATES:
                continue
            if t.specialization != spec["specialization"]:
                continue
            if not required.issubset(set(t.skills or [])):
                continue
            # Never reuse a teammate that cannot satisfy the skill's
            # AgentGuard capability requirements.
            if not required_caps.issubset(set(t.capabilities or [])):
                continue
            return t
        return None

    # -- public API ---------------------------------------------------------
    def create_from_requirement(self, requirement, mission_id=None,
                                worker_id=None, skill_id=None) -> FactoryResult:
        spec = _normalize_requirement(requirement, mission_id)
        if not spec["specialization"]:
            raise ValueError("requirement must name a specialization")

        teammate = self._find_reusable(spec)
        created = teammate is None

        if created:
            teammate = self.registry.create({
                "name": spec["name"] or f"{spec['specialization']}-{uuid.uuid4().hex[:6]}",
                "specialization": spec["specialization"],
                "capabilities": spec["capabilities"],
                "skills": spec["required_skills"],
                "resource_limits": spec["resource_limits"],
                "dependencies": spec["dependencies"],
            })
            for state in ("CONFIGURED", "READY"):
                try:
                    self.registry.transition(teammate.id, state, "factory: materialize")
                except Exception as exc:  # registry may already be terminal
                    logger.debug("transition %s failed: %s", state, exc)
            self._emit("teammate.created", {
                "teammate_id": teammate.id,
                "specialization": teammate.specialization,
                "skills": list(teammate.skills),
                "mission_id": spec["mission_id"],
                "created": True,
            })
        else:
            self._emit("teammate.reused", {
                "teammate_id": teammate.id,
                "specialization": teammate.specialization,
                "skills": list(teammate.skills or []),
                "mission_id": spec["mission_id"],
                "created": False,
            })

        assignment = None
        if worker_id and self.integrator is not None:
            skill = skill_id or (spec["required_skills"][0]
                                 if spec["required_skills"] else None)
            assignment = self.integrator.assign(teammate, worker_id, skill)

        return FactoryResult(teammate=teammate, created=created,
                             assignment=assignment)

    # -- whole-team assembly (21S) ------------------------------------------
    def assemble_engineering_team(self, mission, plan=None) -> list:
        """Auto-select the minimum sufficient engineering team for ``mission``
        and materialize each specialist through ``create_from_requirement``.

        Returns the list of teammates (created or reused), in plan order.
        """
        from core.teammate.planner import SPECIALIST_CATALOG, plan_team

        if plan is None:
            plan = plan_team(mission, self.skill_registry or _NullRegistry())

        members = []
        for spec in plan.specializations:
            catalog_skills = list(
                SPECIALIST_CATALOG.get(spec, {}).get("skills") or [])
            # Capabilities are mandatory (AgentGuard enforces them against the
            # skill's required_capabilities). Derive them from the resolved
            # skills rather than materializing an incapable teammate.
            capabilities: set = set()
            if self.skill_registry is not None:
                catalog_skills = [s for s in catalog_skills
                                  if self.skill_registry.get(s) is not None]
                for sid in catalog_skills:
                    capabilities.update(
                        self.skill_registry.get(sid).required_capabilities or [])
            result = self.create_from_requirement(
                {"specialization": spec, "required_skills": catalog_skills,
                 "capabilities": sorted(capabilities)},
                mission_id=plan.mission_id)
            members.append(result.teammate)
        return members


class _NullRegistry:
    """Registry stub for planning when no Skill Registry is wired in."""

    def get(self, skill_id):
        return None

    def list(self):
        return []
