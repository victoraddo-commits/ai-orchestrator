"""KAI Tool Bus — JARVIS P2.

A registry of callable tools, each with a typed descriptor (id, name,
description, risk class, inputs/outputs, timeout, audit requirement) so the
Brain can discover capabilities dynamically and never hallucinate one that
doesn't exist (§10, §62).

Tools WRAP existing verified orchestrator functions — nothing here
reimplements infrastructure. Every tool call goes through the policy engine
(P3): SAFE runs automatically (subject to autonomy level), CONTROLLED is
logged + policy-governed, HIGH_RISK creates an approval request and blocks
until resolved (§26).

This module has no FastAPI dependency; the HTTP surface lives in
tool_routes.py so the bus can also be used in-process by the chat layer.
"""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable


# --- risk classes (JARVIS §26) ---------------------------------------------

SAFE = "safe"                # read-only / inspection — automatic
CONTROLLED = "controlled"    # logged mutations within policy — automatic at
                             # ACTIVE+ autonomy, approval below that
HIGH_RISK = "high_risk"      # destructive/financial/security — ALWAYS approval


RISK_ORDER = {SAFE: 0, CONTROLLED: 1, HIGH_RISK: 2}


@dataclass
class ToolSpec:
    id: str                    # dotted namespace: kai.server.inspect
    name: str
    description: str
    risk: str                  # SAFE | CONTROLLED | HIGH_RISK
    inputs: dict = field(default_factory=dict)    # {name: type-name}
    outputs: str = "json"
    timeout_s: float = 30.0
    requires_approval_note: bool = False          # include note in result
    owner: str = "core"
    version: str = "1.0.0"
    tags: list[str] = field(default_factory=list)


@dataclass
class ToolResult:
    tool_id: str
    ok: bool
    data: Any = None
    error: str | None = None
    duration_ms: int = 0
    risk: str = SAFE
    approval_id: str | None = None     # set when HIGH_RISK created a request
    executed: bool = True              # False when blocked pending approval


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, dict] = {}

    def register(self, spec: ToolSpec, fn: Callable[..., Any]) -> None:
        if not spec.id.startswith("kai."):
            raise ValueError(f"tool ids must be namespaced 'kai.<area>.<verb>': {spec.id}")
        if spec.risk not in RISK_ORDER:
            raise ValueError(f"invalid risk class '{spec.risk}' for {spec.id}")
        self._tools[spec.id] = {"spec": spec, "fn": fn}

    def get(self, tool_id: str) -> dict | None:
        return self._tools.get(tool_id)

    def list(self, tag: str | None = None, risk: str | None = None) -> list[ToolSpec]:
        out = []
        for entry in self._tools.values():
            s = entry["spec"]
            if tag and tag not in s.tags:
                continue
            if risk and s.risk != risk:
                continue
            out.append(s)
        return sorted(out, key=lambda s: s.id)

    def ids(self) -> list[str]:
        return sorted(self._tools.keys())


REGISTRY = ToolRegistry()


def tool(spec: ToolSpec):
    """Decorator: @tool(ToolSpec(...))"""
    def wrap(fn):
        REGISTRY.register(spec, fn)
        return fn
    return wrap


def describe_all() -> list[dict]:
    """Machine-readable catalog for the Brain / Command Center."""
    return [
        {
            "id": s.id, "name": s.name, "description": s.description,
            "risk": s.risk, "inputs": s.inputs, "outputs": s.outputs,
            "timeout_s": s.timeout_s, "owner": s.owner, "version": s.version,
            "tags": s.tags,
        }
        for s in REGISTRY.list()
    ]


# --- execution with timing + honest failure --------------------------------

def _invoke(entry: dict, args: dict) -> ToolResult:
    spec: ToolSpec = entry["spec"]
    start = time.monotonic()
    try:
        data = entry["fn"](**args)
        dur = int((time.monotonic() - start) * 1000)
        # Tools may return (ok, payload) tuples for soft failures.
        if isinstance(data, tuple) and len(data) == 2 and isinstance(data[0], bool):
            ok, payload = data
            return ToolResult(spec.id, ok, data=payload, duration_ms=dur, risk=spec.risk)
        return ToolResult(spec.id, True, data=data, duration_ms=dur, risk=spec.risk)
    except Exception as exc:  # noqa: BLE001 — tools are a trust boundary
        dur = int((time.monotonic() - start) * 1000)
        return ToolResult(
            spec.id, False, error=f"{type(exc).__name__}: {exc}",
            duration_ms=dur, risk=spec.risk,
        )


def run_tool(tool_id: str, args: dict | None = None) -> ToolResult:
    """Execute a registered tool by id. Raises KeyError on unknown tool."""
    entry = REGISTRY.get(tool_id)
    if entry is None:
        raise KeyError(f"unknown tool '{tool_id}' — see kai.tools.list for available tools")
    return _invoke(entry, dict(args or {}))
