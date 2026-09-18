"""Tool Fabric (TF Phase 7 / 21I).

Declares the tools a teammate may use — derived from the ``allowed_tools`` /
``forbidden_tools`` of its registered skills — and enforces that scope at the
Tool Bus. Forbidden and undeclared tools are denied before dispatch and the
attempt is audited. Also binds each teammate to an isolated sandbox workspace.

This is the tool-side analogue of 21H's ExecutionGuard: ExecutionGuard gates
*actions*, the Tool Fabric gates *tools*.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

try:  # the Tool Bus is optional so this module imports in leaner checkouts
    from core.kai import tool_bus as _tool_bus
    ToolAccessDenied = _tool_bus.ToolAccessDenied
except Exception:  # pragma: no cover - exercised only where tool_bus is absent
    _tool_bus = None

    class ToolAccessDenied(PermissionError):
        """Raised when a tool is invoked outside the declared scope."""

logger = logging.getLogger(__name__)

SOURCE = "tool_fabric"

SANDBOX_ROOT = Path(os.environ.get(
    "AI_ORCHESTRATOR_SANDBOX_ROOT",
    str(Path.home() / ".ai-orchestrator" / "sandboxes"),
))

_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")


@dataclass
class ToolScope:
    allowed: set = field(default_factory=set)
    forbidden: set = field(default_factory=set)

    @property
    def tools(self) -> set:
        """Effective allowlist: declared allowed minus forbidden."""
        return set(self.allowed) - set(self.forbidden)

    def allows(self, name: str) -> bool:
        return name in self.tools


def derive_tool_scope(teammate: Any, skill_registry: Any) -> ToolScope:
    """Union the allowed/forbidden tools across the teammate's skills, plus
    any explicit teammate-level ``allowed_tools`` / ``forbidden_tools``."""
    allowed: set = set()
    forbidden: set = set()

    for sid in (getattr(teammate, "skills", None) or []):
        rec = skill_registry.get(sid) if skill_registry is not None else None
        if rec is None:
            continue
        allowed.update(rec.allowed_tools or [])
        forbidden.update(rec.forbidden_tools or [])

    allowed.update(getattr(teammate, "allowed_tools", None) or [])
    forbidden.update(getattr(teammate, "forbidden_tools", None) or [])

    return ToolScope(allowed=allowed, forbidden=forbidden)


class ToolFabric:
    def __init__(self, skill_registry=None, bus=None,
                 audit: Optional[Callable[[dict], None]] = None) -> None:
        self.skill_registry = skill_registry
        self.bus = bus if bus is not None else _tool_bus
        self.audit = audit

    # -- scope --------------------------------------------------------------
    def scope_for(self, teammate: Any) -> ToolScope:
        return derive_tool_scope(teammate, self.skill_registry)

    def is_allowed(self, teammate: Any, tool_name: str) -> bool:
        return self.scope_for(teammate).allows(tool_name)

    # -- audit --------------------------------------------------------------
    def _record(self, teammate: Any, tool_name: str, decision: str,
                reason: str) -> None:
        entry = {
            "event": "tool_access",
            "tool": tool_name,
            "decision": decision,
            "reason": reason,
        }
        if self.audit is not None:
            try:
                self.audit(entry)
            except Exception as exc:  # audit must never break execution
                logger.debug("tool audit callable failed: %s", exc)
        history = getattr(teammate, "security_history", None)
        if isinstance(history, list):
            history.append(entry)

    # -- invocation ---------------------------------------------------------
    def invoke(self, teammate: Any, tool_name: str, params: Optional[dict] = None) -> dict:
        tid = getattr(teammate, "id", "?")

        if not isinstance(tool_name, str) or not _TOOL_NAME_RE.match(tool_name):
            self._record(teammate, str(tool_name), "denied", "malformed tool name")
            raise ToolAccessDenied(
                f"malformed tool name {tool_name!r} rejected"
            )

        scope = self.scope_for(teammate)
        if not scope.allows(tool_name):
            self._record(teammate, tool_name, "denied",
                         "not in declared tool scope")
            raise ToolAccessDenied(
                f"tool {tool_name!r} is not declared for teammate {tid}"
            )

        try:
            result = self.bus.invoke(tool_name, params or {}, scope=scope.tools)
        except ToolAccessDenied:
            self._record(teammate, tool_name, "denied", "blocked at tool bus")
            raise
        except Exception:
            self._record(teammate, tool_name, "error", "handler raised")
            raise

        self._record(teammate, tool_name, "allowed", "ok")
        return result

    # -- sandbox ------------------------------------------------------------
    def sandbox_for(self, teammate: Any) -> Path:
        """Return (creating if needed) the teammate's isolated workspace."""
        tid = getattr(teammate, "id", None) or "unknown"
        path = SANDBOX_ROOT / str(tid) / "workspace"
        path.mkdir(parents=True, exist_ok=True)
        return path


# ── §12 tool registry projection ────────────────────────────────────────────
_LEVEL_TO_RISK = {"low": "safe", "medium": "controlled",
                  "high": "high_risk", "critical": "high_risk"}
_RISK_ORDER = {"safe": 0, "controlled": 1, "high_risk": 2}


def tool_catalog(skill_registry: Any = None) -> dict:
    """Unified, inspectable (§12) tool catalog.

    Merges the authoritative KAI tool-bus specs (risk, input/output schema,
    timeout, auth, rollback, audit) with the tools the canonical skills
    grant/forbid, so every tool carries its risk level, required permissions,
    allowed environments and the skills that use or forbid it.
    """
    tools: dict = {}

    def _entry(name: str) -> dict:
        return tools.setdefault(name, {
            "tool": name, "risk": "safe", "inputs": {}, "outputs": "json",
            "timeout_s": None, "auth_required": False, "rollback": None,
            "audit": True,
            "permissions": {"vault": [], "network": [], "filesystem": []},
            "environments": ["production"],
            "used_by_skills": [], "forbidden_by": [], "registered": False,
        })

    # 1. KAI tool-bus specs (the declared schema). Importing builtin triggers
    #    the @tool decorators that register the canonical tools.
    try:
        from core.kai_tools import builtin  # noqa: F401
        from core.kai_tools.registry import REGISTRY
        for s in REGISTRY.list():
            e = _entry(s.id)
            e.update({
                "risk": s.risk, "inputs": dict(s.inputs or {}),
                "outputs": s.outputs, "timeout_s": s.timeout_s,
                "auth_required": bool(getattr(s, "auth_required", False)),
                "rollback": getattr(s, "rollback", None),
                "audit": bool(getattr(s, "audit", True)),
                "permissions": dict(getattr(s, "permissions", {}) or {}),
                "environments": list(getattr(s, "environments", None)
                                     or ["production"]),
                "registered": True,
            })
    except Exception:  # tool bus absent in lean checkouts — skill view only
        pass

    # 2. Skill grants — conservative risk from the skill's security level.
    if skill_registry is not None:
        try:
            skills = skill_registry.list()
        except Exception:
            skills = []
        for skill in skills:
            level = (skill.security_requirements or {}).get("level", "low")
            risk = _LEVEL_TO_RISK.get(level, "safe")
            perms = skill.required_permissions or {}
            for name in (skill.allowed_tools or []):
                e = _entry(name)
                if skill.skill_id not in e["used_by_skills"]:
                    e["used_by_skills"].append(skill.skill_id)
                if _RISK_ORDER.get(risk, 0) > _RISK_ORDER.get(e["risk"], 0):
                    e["risk"] = risk
                for bucket in ("secrets", "network", "filesystem"):
                    target = "vault" if bucket == "secrets" else bucket
                    tgt = e["permissions"].setdefault(target, [])
                    for value in (perms.get(bucket) or []):
                        if value not in tgt:
                            tgt.append(value)
            for name in (skill.forbidden_tools or []):
                e = _entry(name)
                if skill.skill_id not in e["forbidden_by"]:
                    e["forbidden_by"].append(skill.skill_id)

    for e in tools.values():
        for bucket in ("vault", "network", "filesystem"):
            e["permissions"][bucket] = sorted(set(e["permissions"].get(bucket) or []))
        e["used_by_skills"] = sorted(e["used_by_skills"])
        e["forbidden_by"] = sorted(e["forbidden_by"])
    return {"schema": 1, "count": len(tools),
            "tools": {name: tools[name] for name in sorted(tools)}}
