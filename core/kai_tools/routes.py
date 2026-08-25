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


@router.get("/world")
def world_summary():
    from core.world_model import get_state
    return get_state()


@router.get("/world/{entity_id:path}")
def world_entity(entity_id: str):
    from core.world_model import get_state
    st = get_state(entity_id)
    if st.get("entity") is None:
        raise HTTPException(404, f"unknown entity '{entity_id}'")
    return st


@router.post("/world/refresh")
def world_refresh_endpoint(request: Request):
    _authorize(request)  # refresh is read-only collection but auth-gate it anyway
    from core.world_model import build_snapshot
    snap = build_snapshot()
    return {"updated_at": snap.get("updated_at"), **(snap.get("counts") or {}),
            "changes": snap.get("changes_since_previous", [])}


@router.get("/impact/{entity_id:path}")
def world_impact(entity_id: str):
    from core.world_model import impact_of
    return impact_of(entity_id)


@router.get("/executive")
def executive_now():
    from core.kai_executive import prioritize
    return prioritize()


@router.get("/briefings")
def briefings_list(limit: int = 10):
    from core.kai_executive import _load, BRIEFINGS_PATH
    rows = _load(BRIEFINGS_PATH)[-limit:][::-1]
    return {"count": len(rows), "briefings": [
        {"kind": b.get("kind"), "ts": b.get("ts"), "counts": b.get("counts")} for b in rows]}


@router.get("/missions")
def missions_list(status: str | None = None):
    from core.kai_missions import list_missions
    return {"count": 0, "missions": list_missions(status)}


@router.post("/missions/{mission_id}/verify")
def mission_verify(mission_id: str, request: Request, body: dict = None):
    _authorize(request)
    from core.kai_missions import verify_mission
    body = body or {}
    return verify_mission(mission_id, bool(body.get("approved")),
                          note=str(body.get("note", "")))


@router.get("/enhancements")
def enhancements_status():
    from core.kai_enhancements import status
    return {"enhancements": status()}


@router.post("/enhancements/{key}/enable")
def enhancements_enable(key: str, request: Request):
    _authorize(request)
    from core.kai_enhancements import enable
    r = enable(key)
    if not r.get("ok"):
        raise HTTPException(404, r.get("error", "unknown"))
    return r


@router.post("/enhancements/{key}/disable")
def enhancements_disable(key: str, request: Request):
    _authorize(request)
    from core.kai_enhancements import disable
    r = disable(key)
    if not r.get("ok"):
        raise HTTPException(404, r.get("error", "unknown"))
    return r


@router.get("/factory/status")
def factory_status():
    from core.kai_tools.builtin import factory_status as _fs
    return _fs()


@router.get("/factory/reports")
def factory_reports(limit: int = 3):
    from core.kai_tools.builtin import factory_reports as _fr
    return _fr(limit=min(max(limit, 1), 10))


@router.get("/evolution/status")
def evolution_status():
    from core.kai_evolution import status
    return status()
