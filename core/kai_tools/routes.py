"""KAI Tool Bus HTTP surface — mounted by core/api.py.

  GET  /kai/tools            — machine-readable catalog (all tools + risk)
  POST /kai/tools/{tool_id}/execute — policy-gated execution

Execution requires the operator's write capability (kai.tools.execute) and
goes through core.kai_tools.policy.execute — the ONLY sanctioned path.
NOTE: the auth gate resolves at request time because _require_write_capability
lives in core.api, which imports this module (decoration-time resolution would
be a circular import).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.authz import CAPABILITIES

from core.kai_tools import builtin  # noqa: F401 — imports register the tools
from core.kai_tools.policy import execute
from core.kai_tools.registry import describe_all

CAPABILITIES.setdefault("kai.tools.execute", "Execute KAI tools via the tool bus")
CAPABILITIES.setdefault("kai.tools.list", "List available KAI tools")

router = APIRouter(prefix="/kai/tools", tags=["kai-tools"])


class ToolExecBody(BaseModel):
    args: dict = {}
    operator: str = "operator"
    reason: str = ""


def _authorize(request: Request) -> str:
    """Request-time capability check using api.py's own dependency logic."""
    from core.api import _require_write_capability  # lazy: avoids circular import
    checker = _require_write_capability("kai.tools.execute")
    # Run the FastAPI dependency manually against this request.
    import asyncio
    from fastapi import Header  # noqa: F401
    sig = __import__("inspect").signature(checker)
    kwargs = {}
    for name, param in sig.parameters.items():
        if name == "authorization":
            kwargs[name] = request.headers.get("authorization")
        elif name == "x_kai_session":
            kwargs[name] = request.headers.get("x-kai-session")
    out = checker(**kwargs)
    if asyncio.iscoroutine(out):
        out = asyncio.get_event_loop().run_until_complete(out) if False else out
        raise HTTPException(500, "async auth unsupported")
    return out


@router.get("")
def list_tools():
    return {"count": len(describe_all()), "tools": describe_all()}


@router.post("/{tool_id:path}/execute")
def exec_tool(tool_id: str, body: ToolExecBody, request: Request):
    from core.kai_tools.registry import REGISTRY
    if REGISTRY.get(tool_id) is None:
        raise HTTPException(404, f"unknown tool '{tool_id}'")
    operator = _authorize(request) or "operator"
    result = execute(tool_id, body.args, operator=operator, reason=body.reason)
    return {
        "tool": result.tool_id,
        "ok": result.ok,
        "executed": result.executed,
        "risk": result.risk,
        "duration_ms": result.duration_ms,
        "data": result.data if result.ok else None,
        "error": result.error,
        "approval_id": result.approval_id,
    }
