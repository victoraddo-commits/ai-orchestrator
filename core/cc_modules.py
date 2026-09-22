"""Command Center — per-module API proxy.

Lets the Command Center manage every module from one dashboard (no external
links). Module credentials are injected server-side; the browser never sees
module tokens. Mounted by ``core/api.py`` as ``app.include_router(cc_router)``.

    Browser → /cc/{module}/{path} → module API (with server-side token)
"""
from __future__ import annotations

import os

import httpx
from fastapi import APIRouter, HTTPException, Request, Response

cc_router = APIRouter(prefix="/cc", tags=["command-center-modules"])


def _require_operator(request: Request) -> None:
    """Writes through the module proxy require an operator (bridge token, valid
    CC session, or auth-proxy identity headers). Identity headers are honoured
    only from a trusted peer (``core.auth.trusted_proxy``)."""
    if request.headers.get("authorization"):
        return
    tok = request.headers.get("x-kai-session", "")
    if tok:
        from core import authz
        try:
            if authz._resolve_session(tok):
                return
        except Exception:  # noqa: BLE001
            pass
    from core.auth.trusted_proxy import proxy_identity
    if proxy_identity(request, request.headers.get("x-kai-user"),
                      request.headers.get("x-kai-user-id")):
        return
    raise HTTPException(status_code=401, detail="operator session required")

MODULES = {
    "legal": {
        "base": os.environ.get("CC_LEGAL_URL", "http://192.168.1.100:8100"),
        "token_file": os.environ.get("CC_LEGAL_TOKEN_FILE",
                                     "/opt/ai-orchestrator/memory/legal_brain.token"),
        "header": "X-Legal-Token", "scheme": None,
    },
    "arbitra": {
        "base": os.environ.get("CC_ARBITRA_URL", "http://192.168.1.118:8096"),
        "token_file": os.environ.get("CC_ARBITRA_TOKEN_FILE", ""),
        "header": "Authorization", "scheme": "Bearer ",
    },
    "telegram": {
        "base": os.environ.get("CC_TELEGRAM_URL", "http://192.168.1.111:8099"),
        "token_file": "", "header": None, "scheme": None,
    },
    "talent": {
        "base": os.environ.get("CC_TALENT_URL", "http://192.168.1.115:4000"),
        "token_file": os.environ.get("CC_TALENT_TOKEN_FILE", "/etc/kai/talent_token"),
        "header": "X-Talent-Token", "scheme": None,
    },
    "android": {
        "base": os.environ.get("CC_ANDROID_URL", "http://192.168.1.120:4000"),
        "token_file": os.environ.get("CC_ANDROID_TOKEN_FILE", "/etc/kai/android_factory_token"),
        "header": "X-Factory-Token", "scheme": None,
    },
    "docs": {
        "base": os.environ.get("CC_DOCS_URL", "http://192.168.1.112:4010"),
        "token_file": os.environ.get("CC_DOCS_TOKEN_FILE", "/etc/kai/kai_docs_token"),
        "header": "X-Kai-Docs-Token", "scheme": None,
    },
}

_SAFE = {"content-type", "content-length", "content-disposition"}


def _token(cfg: dict) -> str:
    tf = cfg.get("token_file") or ""
    if tf and os.path.exists(tf):
        try:
            with open(tf) as fh:
                return fh.read().strip()
        except OSError:
            return ""
    return ""


@cc_router.api_route("/{module}/{path:path}",
                     methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def module_proxy(module: str, path: str, request: Request):
    cfg = MODULES.get(module)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"unknown module {module!r}")
    if request.method not in ("GET", "HEAD"):
        _require_operator(request)
    url = cfg["base"].rstrip("/") + "/" + path
    headers = {}
    hdr = cfg.get("header")
    tok = _token(cfg)
    if hdr and tok:
        headers[hdr] = (cfg.get("scheme") or "") + tok
    ctype = request.headers.get("content-type", "application/json")
    body = await request.body()
    for extra in ("x-filename",):
        if request.headers.get(extra):
            headers[extra] = request.headers[extra]
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            r = await client.request(request.method, url,
                                     params=request.query_params,
                                     content=body,
                                     headers={**headers, "content-type": ctype})
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502,
                            detail=f"module {module} unreachable: {type(e).__name__}")
    out_headers = {k: v for k, v in r.headers.items() if k.lower() in _SAFE}
    return Response(content=r.content, status_code=r.status_code,
                    media_type=r.headers.get("content-type", "application/json"),
                    headers=out_headers)
