"""Model Fabric integration (TF Phase 4 / 21D).

Resolves a teammate skill to a task type and a provider chain sourced from
the fabric (``core/ai/ai_router.py``). The skill's declared roles are the
allow-set; the fabric's effective chain (after rotation, overrides, health)
is canonical and order is preserved. Every teammate prompt routes through
``ai_router.delegate`` — never a direct llm_clients/core.ai_provider call.

Fail-closed: ambiguous capability or an empty chain after filtering is an
error, not a silent fallback.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.ai import ai_router

if TYPE_CHECKING:
    from core.teammate.skills import SkillRecord, SkillRegistry

logger = logging.getLogger(__name__)


class SkillModelError(ValueError):
    """Raised when a skill cannot be resolved to a fabric provider chain."""


@dataclass(frozen=True)
class ModelPlan:
    skill_id: str
    task_type: str
    provider_chain: list[str]


# Single source for skill capability -> fabric task_type. Extend here only.
CAPABILITY_TO_TASK_TYPE = {
    "generate": "planning",
    "coding": "coding",
    "security": "review",
    "benchmark": "review",
    "deploy": "planning",
    "documentation": "documentation",
    "classification": "classification",
    "log_analysis": "log_analysis",
}


def resolve_task_type(skill: "SkillRecord") -> str:
    mreq = skill.model_requirements or {}
    cap = mreq.get("capability")
    if cap in CAPABILITY_TO_TASK_TYPE:
        return CAPABILITY_TO_TASK_TYPE[cap]
    roles = mreq.get("roles") or []
    if roles:
        for task_type, chain in ai_router.ROLE_PROVIDERS.items():
            if chain and chain[0] == roles[0]:
                return task_type
    raise SkillModelError(
        f"skill {skill.skill_id}: no task type for capability {cap!r}")


def resolve_model_plan(teammate_id: str, skill_id: str,
                       skill_registry: "SkillRegistry") -> ModelPlan:
    skill = skill_registry.get(skill_id)
    if skill is None:
        raise KeyError(f"unknown skill_id: {skill_id}")
    task_type = resolve_task_type(skill)
    effective = ai_router.get_effective_providers(task_type)
    roles = set((skill.model_requirements or {}).get("roles") or [])
    chain = [p for p in effective if p in roles]
    if not chain:
        raise SkillModelError(
            f"skill {skill_id}: no fabric provider in roles "
            f"{sorted(roles)} for task_type {task_type!r}")
    return ModelPlan(skill_id=skill_id, task_type=task_type,
                     provider_chain=chain)


def route(task_type: str, description: str, **kw):
    """Thin pass-through to the fabric. No resolution/fallback logic here."""
    return ai_router.delegate(description, task_type=task_type, **kw)