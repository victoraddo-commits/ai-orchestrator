"""Team Planner (TF Phase 5 / 21F).

Given a mission, produce a :class:`TeamPlan` — the minimum sufficient team
(required expertise, skills, tools, permissions, models) plus the execution
metadata the factory (21G) and executor (21J) need: workload, parallelizable
vs sequential work, verification and security requirements, and risk level.

The planner is read-only: it consults the Skill Registry (21B) and a static
specialist catalog. It creates nothing and touches no memory.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from typing import Any

logger = logging.getLogger(__name__)

# -- specialist catalog -----------------------------------------------------
# Maps a specialist identity to the reusable skills (21B ids) it needs and a
# baseline risk level. 21S later assembles full engineering teams from these.

SPECIALIST_CATALOG: dict[str, dict[str, Any]] = {
    "architect": {
        "expertise": "system architecture",
        "skills": ["inspect_repository", "inspect_service", "write_code"],
        "risk_level": "medium",
    },
    "planner": {
        "expertise": "task planning",
        "skills": ["inspect_repository", "inspect_service", "diagnose_failure"],
        "risk_level": "low",
    },
    "researcher": {
        "expertise": "research and discovery",
        "skills": ["inspect_repository", "inspect_logs", "inspect_database"],
        "risk_level": "low",
    },
    "coder": {
        "expertise": "software implementation",
        "skills": ["write_code", "run_tests"],
        "risk_level": "medium",
    },
    "reviewer": {
        "expertise": "code review",
        "skills": ["inspect_repository", "inspect_logs"],
        "risk_level": "low",
    },
    "qa": {
        "expertise": "quality assurance",
        "skills": ["run_tests", "verify_endpoint"],
        "risk_level": "low",
    },
    "security": {
        "expertise": "security engineering",
        "skills": ["run_security_scan", "inspect_service", "inspect_repository"],
        "risk_level": "high",
    },
    "devops": {
        "expertise": "deployment and operations",
        "skills": ["deploy_service", "rollback_service", "inspect_service"],
        "risk_level": "high",
    },
    "verifier": {
        "expertise": "independent verification",
        "skills": ["verify_endpoint", "run_tests"],
        "risk_level": "low",
    },
}

# Lower-case substrings that indicate a specialist is needed.
INTENT_KEYWORDS: dict[str, list[str]] = {
    "architect": ["architecture", "architect", "design"],
    "planner": ["plan", "roadmap", "schedule", "coordinate"],
    "researcher": ["research", "investigate", "discover", "analy", "explore"],
    "coder": ["implement", "code", "coding", "build", "fix", "refactor",
              "develop", "feature", "bug"],
    "reviewer": ["review", "critique"],
    "qa": ["test", "qa", "validate", "quality"],
    "security": ["security", "vulnerab", "harden", "audit", "threat"],
    "devops": ["deploy", "release", "provision", "infra", "rollback", "operate"],
    "verifier": ["verify", "verification", "acceptance"],
}

_DEFAULT_TEAM = ["planner", "coder", "verifier"]
_RISK_ORDER = {"low": 0, "medium": 1, "high": 2}


@dataclass
class TeamPlan:
    mission_id: str
    specializations: list = field(default_factory=list)
    required_expertise: list = field(default_factory=list)
    required_skills: list = field(default_factory=list)
    required_tools: list = field(default_factory=list)
    required_permissions: dict = field(default_factory=dict)
    required_models: list = field(default_factory=list)
    expected_workload: dict = field(default_factory=dict)
    dependencies: list = field(default_factory=list)
    parallelizable: list = field(default_factory=list)
    sequential: list = field(default_factory=list)
    verification_requirements: list = field(default_factory=list)
    security_requirements: dict = field(default_factory=dict)
    resource_requirements: dict = field(default_factory=dict)
    risk_level: str = "low"

    def to_dict(self) -> dict:
        return asdict(self)


# -- internals --------------------------------------------------------------

def _mission_text(mission: Any) -> str:
    if isinstance(mission, str):
        return mission
    if isinstance(mission, dict):
        for key in ("description", "objective", "goal", "name", "title"):
            if mission.get(key):
                return str(mission[key])
        return ""
    for attr in ("description", "objective", "goal", "name", "title"):
        val = getattr(mission, attr, None)
        if val:
            return str(val)
    return str(mission)


def _mission_id(mission: Any) -> str:
    if isinstance(mission, dict):
        return str(mission.get("id") or mission.get("mission_id") or "unknown")
    for attr in ("id", "mission_id"):
        val = getattr(mission, attr, None)
        if val:
            return str(val)
    return "unknown"


def _parse_mb(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip().lower()
    try:
        if s.endswith("gb"):
            return int(float(s[:-2]) * 1024)
        if s.endswith("g"):
            return int(float(s[:-1]) * 1024)
        if s.endswith("mb"):
            return int(float(s[:-2]))
        if s.endswith("m"):
            return int(float(s[:-1]))
        return int(float(s))
    except (ValueError, TypeError):
        return 0


def _fmt_mb(mb: int) -> str:
    if mb and mb % 1024 == 0:
        return f"{mb // 1024}g"
    return f"{mb}mb"


def _max_level(levels) -> str:
    best = "low"
    for lvl in levels:
        if _RISK_ORDER.get(str(lvl), 0) > _RISK_ORDER.get(best, 0):
            best = str(lvl)
    return best


def resolve_specializations(text, catalog=None, intent_keywords=None) -> list[str]:
    """Map mission text to an ordered list of specialists. Falls back to a
    minimum sufficient default team when no keyword matches."""
    catalog = SPECIALIST_CATALOG if catalog is None else catalog
    keywords = INTENT_KEYWORDS if intent_keywords is None else intent_keywords
    t = (text or "").lower()
    matched = [
        spec for spec in catalog
        if any(kw in t for kw in keywords.get(spec, []))
    ]
    if matched:
        return matched
    fallback = [s for s in _DEFAULT_TEAM if s in catalog]
    return fallback or list(catalog)[:1]


def _partition(skills: dict, order: list[str]):
    """Split selected skills into (can-start-now, dependency-ordered).

    parallelizable: skills with no in-set dependencies.
    sequential: skills with in-set dependencies, in stable topological order.
    """
    sel = set(order)

    def in_set_deps(sid):
        return [d for d in (skills[sid].dependencies or []) if d in sel]

    parallel = [sid for sid in order if not in_set_deps(sid)]
    seq_set = {sid for sid in order if in_set_deps(sid)}

    ordered: list[str] = []
    visited: set[str] = set()

    def visit(sid):
        if sid in visited:
            return
        visited.add(sid)
        for dep in in_set_deps(sid):
            visit(dep)
        if sid in seq_set:
            ordered.append(sid)

    for sid in order:
        if sid in seq_set:
            visit(sid)
    return parallel, ordered


def plan_team(mission, skill_registry, catalog=None,
              intent_keywords=None) -> TeamPlan:
    """Produce a :class:`TeamPlan` for ``mission`` from the Skill Registry.

    ``skill_registry`` needs only ``get(skill_id)``. Catalog skills that are
    not registered are skipped rather than failing.
    """
    catalog = SPECIALIST_CATALOG if catalog is None else catalog
    specs = resolve_specializations(
        _mission_text(mission), catalog, intent_keywords)

    skill_ids: list[str] = []
    for spec in specs:
        for sid in (catalog.get(spec, {}).get("skills") or []):
            if sid not in skill_ids and skill_registry.get(sid) is not None:
                skill_ids.append(sid)

    # Pull in transitive skill dependencies that are registered.
    changed = True
    while changed:
        changed = False
        for sid in list(skill_ids):
            for dep in (skill_registry.get(sid).dependencies or []):
                if dep not in skill_ids and skill_registry.get(dep) is not None:
                    skill_ids.append(dep)
                    changed = True

    skills = {sid: skill_registry.get(sid) for sid in skill_ids}

    expertise: list[str] = []
    for spec in specs:
        e = catalog.get(spec, {}).get("expertise")
        if e and e not in expertise:
            expertise.append(e)

    tools: set[str] = set()
    perms: dict = {"secrets": set(), "network": set(), "filesystem": set()}
    models: list[str] = []
    verification: list[str] = []
    levels: list[str] = [catalog.get(s, {}).get("risk_level", "low") for s in specs]
    cpu = mem_mb = timeout = 0

    for sid in skill_ids:
        skill = skills[sid]
        forbidden = set(skill.forbidden_tools or [])
        tools.update(t for t in (skill.allowed_tools or []) if t not in forbidden)

        rp = skill.required_permissions or {}
        for bucket in ("secrets", "network", "filesystem"):
            perms[bucket].update(rp.get(bucket, []) or [])

        for role in (skill.model_requirements or {}).get("roles", []) or []:
            if role not in models:
                models.append(role)

        if skill.verification_method and skill.verification_method not in verification:
            verification.append(skill.verification_method)

        levels.append((skill.security_requirements or {}).get("level", "low"))

        rr = skill.resource_requirements or {}
        try:
            cpu += int(rr.get("cpu", 0) or 0)
        except (ValueError, TypeError):
            pass
        mem_mb = max(mem_mb, _parse_mb(rr.get("memory")))
        try:
            timeout += int(skill.timeout or 0)
        except (ValueError, TypeError):
            pass

    sel = set(skill_ids)
    dependencies: list[str] = []
    for sid in skill_ids:
        for dep in (skills[sid].dependencies or []):
            if dep not in sel and dep not in dependencies:
                dependencies.append(dep)

    parallelizable, sequential = _partition(skills, skill_ids)

    resources: dict[str, Any] = {"cpu": cpu}
    if mem_mb:
        resources["memory"] = _fmt_mb(mem_mb)

    return TeamPlan(
        mission_id=_mission_id(mission),
        specializations=specs,
        required_expertise=expertise,
        required_skills=skill_ids,
        required_tools=sorted(tools),
        required_permissions={k: sorted(v) for k, v in perms.items()},
        required_models=models,
        expected_workload={
            "specialists": len(specs),
            "skills": len(skill_ids),
            "estimated_timeout_s": timeout,
        },
        dependencies=dependencies,
        parallelizable=parallelizable,
        sequential=sequential,
        verification_requirements=verification,
        security_requirements={"level": _max_level(levels)},
        resource_requirements=resources,
        risk_level=_max_level(levels),
    )
