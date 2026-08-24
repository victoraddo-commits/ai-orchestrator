"""KAI Tool Policy — JARVIS P3.

Central risk gate for tool execution (§26):
  SAFE       → execute immediately, audit-log the call.
  CONTROLLED → execute when autonomy level >= ACTIVE-equivalent; below that,
               create an approval request and return blocked. Every call is
               audit-logged either way.
  HIGH_RISK  → NEVER auto-executes. Creates an approval request via the
               existing core.approval queue and returns a blocked result
               carrying its id; execution happens only through the explicit
               approve→execute path.

Autonomy mapping: core/autonomy.py levels 0-5 map onto
  level >= 3 → CONTROLLED may run automatically
  level <= 2 → CONTROLLED requires approval
HIGH_RISK ignores autonomy entirely (§26 "explicit approval required").
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from core.kai_tools.registry import SAFE, CONTROLLED, HIGH_RISK, REGISTRY, ToolResult

_MEMORY_DIR = Path(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))) / "memory"
AUDIT_PATH = _MEMORY_DIR / "tool_audit.jsonl"
AUTONOMY_LEVEL_FILE = _MEMORY_DIR / "autonomy_level.json"

# Autonomy level at/above which CONTROLLED tools self-execute.
CONTROLLED_AUTO_LEVEL = 3


def current_autonomy_level() -> int:
    try:
        with open(AUTONOMY_LEVEL_FILE) as fh:
            return int(json.load(fh).get("level", 1))
    except Exception:
        return 1


def _audit(record: dict) -> None:
    record["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        with open(AUDIT_PATH, "a") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except OSError:
        pass  # audit failure must not block the tool result path


def request_approval(tool_id: str, args: dict, reason: str) -> str | None:
    """Create an approval request using the existing queue. Returns request id."""
    try:
        from core import approval
        req = approval.create_request(
            action=f"tool:{tool_id}",
            service="kai-tools",
            reason=reason or f"Tool {tool_id} requires approval",
        )
        # create_request returns the full request dict in current code
        rid = req.get("id") if isinstance(req, dict) else getattr(req, "id", None)
        return str(rid) if rid is not None else None
    except Exception:
        return None


def execute(tool_id: str, args: dict | None = None, *, operator: str = "system",
            reason: str = "") -> ToolResult:
    """Policy-gated tool execution. This is THE entrypoint other layers use."""
    # JARVIS P22: emergency stop refuses EVERYTHING (§52)
    try:
        from core.kai_emergency import is_stopped, check_rate
        if is_stopped():
            return ToolResult(tool_id, ok=False, executed=False,
                              error="EMERGENCY STOP active — all tool execution refused. "
                                    "Use emergency_resume to restore.")
        allowed, remaining = check_rate(operator)
        if not allowed:
            return ToolResult(tool_id, ok=False, executed=False,
                              error=f"rate limit exceeded for {operator} "
                                    f"({30}/min) — slow down")
    except ImportError:
        pass  # emergency module absent (fresh checkout) — degrade gracefully
    entry = REGISTRY.get(tool_id)
    if entry is None:
        return ToolResult(tool_id, False, error=f"unknown tool '{tool_id}'")
    spec = entry["spec"]
    args = dict(args or {})

    if spec.risk == HIGH_RISK:
        rid = request_approval(tool_id, args, reason)
        _audit({"tool": tool_id, "risk": spec.risk, "operator": operator,
                "decision": "blocked_pending_approval", "approval_id": rid,
                "args_keys": sorted(args.keys())})
        return ToolResult(tool_id, ok=False, executed=False,
                          error="HIGH RISK — awaiting your approval",
                          risk=spec.risk, approval_id=rid)

    if spec.risk == CONTROLLED and current_autonomy_level() < CONTROLLED_AUTO_LEVEL:
        rid = request_approval(tool_id, args, reason or f"CONTROLLED tool {tool_id} at low autonomy")
        _audit({"tool": tool_id, "risk": spec.risk, "operator": operator,
                "decision": "blocked_pending_approval", "approval_id": rid,
                "args_keys": sorted(args.keys())})
        return ToolResult(tool_id, ok=False, executed=False,
                          error="controlled action below autonomy threshold — approval requested",
                          risk=spec.risk, approval_id=rid)

    from core.kai_tools.registry import run_tool
    result = run_tool(tool_id, args)
    _audit({"tool": tool_id, "risk": spec.risk, "operator": operator,
            "decision": "auto_execute", "ok": result.ok, "ms": result.duration_ms,
            "error": result.error})
    return result
