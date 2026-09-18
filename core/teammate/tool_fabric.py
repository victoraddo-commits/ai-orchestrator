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
