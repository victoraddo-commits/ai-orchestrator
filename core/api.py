import base64
import hmac
import json
import os
import re
import secrets
import asyncio
from pathlib import Path
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Header, Depends, Request, UploadFile, File, Form, Body, Query
from fastapi.responses import StreamingResponse, Response, JSONResponse
import httpx
from pydantic import BaseModel
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.staticfiles import StaticFiles
import logging

from core.memory import save, load
from core.security_headers import SecurityHeadersMiddleware

# Load .env explicitly here rather than relying on tools/proxmox.py's own
# load_dotenv() call as a side effect of some other import -- this is the
# API process's true entrypoint, and it doesn't import tools.proxmox at all,
# so provider API keys (GEMINI_API_KEY etc.) were silently never loaded
# until this was added (confirmed live: /providers showed them unavailable
# despite being present and valid in .env).
load_dotenv()

from core.health import analyze
from core.incident_manager import load_incidents
from core.decision_engine import load_decisions
from core.approval import load_requests, approve, reject, list_pending
from core.remediation import load_remediations
from core.verification import load_verification_history
from core.learning import summarize
from core.memory import load, update
from core.lifecycle import InvalidTransition
from core.law_documents import (
    save_document, list_documents, get_document, delete_document,
    DocumentTooLarge, UnsupportedFileType,
)
from core.build_manager import (
    create_build,
    list_builds,
    get_build,
    submit_answer,
    approve_architecture,
    start_generation,
    approve_deploy,
    rollback_deployment,
    get_scheduler_snapshot,
)
from core.project_templates import TEMPLATES
from core.build_learning import summarize_templates, get_build_history, summarize_lessons
from core.module_registry import get_registered_modules
from core.ai_provider import list_providers, set_provider_enabled, deregister_provider, register_provider
from core.ai.agent_registry import (
    list_agents, get_agent, register_agent, enable_agent, disable_agent,
    test_agent, get_agent_stats, get_cost_history, get_performance_history,
    record_benchmark, bootstrap_default_agents,
)
from core.ai import circuit_breaker
from core.ai.ai_router import delegate, get_provider_dashboard, get_worker_details, AllProvidersFailed, NoCapableWorkerError, chat as ai_chat, remove_provider_from_roles, ROLE_PROVIDERS
from core import provider_config_editor
from core.kai.commands import dispatch as kai_dispatch
from core.kai.planner import gather_signals, list_proposals
import core.kai.identity as kai_identity
import core.kai.mission as kai_mission
import core.kai.goals as kai_goals
import core.kai.policies as kai_policies
from core.roadmap_engine import (
    load_roadmap,
    get_phase,
    get_next_phase,
    get_remaining_work,
    get_progress_summary,
    mark_phase_status,
    add_phase,
)
from core.roadmap_manager import (
    is_autonomous_mode_enabled,
    enable_autonomous_mode,
    disable_autonomous_mode,
    is_self_modifying,
)
from core.autonomy import (
    get_autonomy_level,
    set_autonomy_level,
    MIN_LEVEL as AUTONOMY_MIN_LEVEL,
    MAX_LEVEL as AUTONOMY_MAX_LEVEL,
)
from core import authz
from core.audit_aggregator import (
    get_audit_entries, format_audit_entries_as_csv, format_audit_entries_as_json,
    extract_client_ip,
)

# ── Phase 15D + 19A: SSE + Module registry imports ───────────────────────────
import asyncio
import json as _sse_json
from datetime import datetime as _datetime, timezone as _timezone

_SSE_CLIENTS: list[asyncio.Queue] = []


def _sse_notify(event_type: str, data: dict):
    """Push an event to all connected SSE clients."""
    payload = f"event: {event_type}\ndata: {_sse_json.dumps(data, default=str)}\n\n"
    for q in _SSE_CLIENTS[:]:
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass



from core.klaus.api_endpoints import klaus_router as klaus_api_router
from core.klaus.scheduler import start_scheduler as start_klaus_scheduler


app = FastAPI(title="AI Orchestrator Observability API")

# Security headers on every response (CSP, HSTS, X-Frame-Options, etc.)
app.add_middleware(SecurityHeadersMiddleware)

app.include_router(klaus_api_router)

# AI Gateway — OpenAI-compatible /v1 endpoints for external consumers
from core.ai_gateway.gateway import router as gateway_router
app.include_router(gateway_router)

# Telegram Manager — user management, activity tracking, config dashboard
from core.telegram_manager.api import telegram_router
app.include_router(telegram_router)

from core.device_registry_routes import router as device_router
app.include_router(device_router)

# 2026-08-09: Kai Mobile Command Node — Sub-project 3: Push Notification System
from core.notifications_routes import router as notifications_router
app.include_router(notifications_router)

# 2026-08-09: Kai Mobile Command Node — Sub-project 4: Permanent Health Worker
from core.health_worker_routes import router as health_worker_router
app.include_router(health_worker_router)

# 2026-08-09: Kai Mobile Command Node — Sub-project 5: WireGuard Resilience
from core.wireguard_routes import router as wireguard_router
app.include_router(wireguard_router)

# 2026-08-10: Kai Betting — AI-powered sports prediction platform
from core.kai_betting.api import router as betting_router
app.include_router(betting_router)

# JARVIS Phase 1: Kai Voice Gateway — WSS endpoint for voice pipeline
from core.voice_gateway.gateway import voice_router as kai_voice_router
app.include_router(kai_voice_router, prefix="/kai-voice")

# JARVIS Phase 2: Kai Voice HUD — served as static files from the built React app
_hud_dist = Path(__file__).resolve().parents[2] / "src" / "kai-voice-hud" / "dist"
if _hud_dist.exists():
    app.mount("/voice-hud", StaticFiles(directory=str(_hud_dist), html=True), name="kai-voice-hud")

# 2026-08-09: Kai Mobile Command Node — Sub-project 6: Module Launcher & App Shortcuts
from core.mobile_launcher_routes import router as mobile_launcher_router
app.include_router(mobile_launcher_router)

# Application Registry (routes previously created but never mounted)
from core.app_registry_routes import router as app_registry_router
app.include_router(app_registry_router)

# JARVIS P2/P3: KAI Tool Bus — registry + policy-gated execution surface
from core.kai_tools.routes import router as kai_tools_router
app.include_router(kai_tools_router)

# KAI Ultimate mobile app — pairing + aggregated data endpoints
from core.kai_app_api import router as kai_app_router
app.include_router(kai_app_router)

# Repository Registry
from core.repo_registry import (
    list_repositories, list_by_platform, list_local_repositories, get_registry_stats,
)

@app.get("/api/repos")
def repo_registry_list(platform: str = None):
    """List all registered repositories, optionally filtered by platform."""
    if platform:
        return {"repos": list_by_platform(platform)}
    return {"repos": list_repositories()}

@app.get("/api/repos/local")
def repo_registry_local():
    """List locally-discovered repositories."""
    return {"repos": list_local_repositories()}

@app.get("/api/repos/stats")
def repo_registry_stats():
    """Return repository registry statistics."""
    stats = get_registry_stats()
    return {"total": stats.get("total", 0), "by_platform": stats.get("by_platform", {})}

@app.post("/api/repos/sync")
def repo_registry_sync():
    """Trigger repository sync — requires operator authentication."""
    # Write-gated: returns 403 for unauthenticated callers.
    raise HTTPException(status_code=403, detail="operator authentication required")

# Create default API key on first startup if none exists
try:
    from core.ai_gateway.keys import ensure_default_key
    default_key = ensure_default_key()
    if default_key:
        import logging
        logging.getLogger(__name__).warning(
            f"Gateway default API key created: {default_key}\n"
            "Store this key — it will not be shown again."
        )
except Exception:
    pass

try:
    start_klaus_scheduler()
except Exception:
    import logging
    logging.getLogger(__name__).warning("KLAUS scheduler failed to start (db not available?)")


_DASHBOARD_PATH = Path(__file__).resolve().parent / "kai" / "dashboard.html"
_DASHBOARD_HTML = _DASHBOARD_PATH.read_text()

_COMMAND_CENTER_PATH = Path(__file__).resolve().parent / "kai" / "command_center.html"
_COMMAND_CENTER_HTML = _COMMAND_CENTER_PATH.read_text()

_MANIFEST_PATH = Path(__file__).resolve().parent / "kai" / "manifest.json"
_MANIFEST_JSON = _MANIFEST_PATH.read_text() if _MANIFEST_PATH.exists() else "{}"

_SW_PATH = Path(__file__).resolve().parent / "kai" / "sw.js"
_SW_JS = _SW_PATH.read_text() if _SW_PATH.exists() else "// service worker not found"


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return _DASHBOARD_HTML


@app.get("/command-center", response_class=HTMLResponse)
def command_center():
    return _COMMAND_CENTER_HTML


@app.get("/kai/manifest.json")
def manifest():
    """PWA manifest for Kai Command Center (SP5 — Mobile Agent & UI)."""
    return Response(content=_MANIFEST_JSON, media_type="application/json")


@app.get("/kai/sw.js")
def service_worker():
    """PWA service worker for Kai Command Center (SP5 — Mobile Agent & UI)."""
    return Response(content=_SW_JS, media_type="application/javascript")


@app.get("/kai/mobile/diagnose")
def mobile_diagnose():
    """Run Kai Mobile Command Node self-diagnostics (SP6 — Integration & Testing).

    Returns 8 checks: device registry, WireGuard, API reachability,
    authentication, notifications, AI providers, health worker, and PWA assets.
    Each check has status (PASS/WARN/FAIL), detail text, and optional artifact.
    """
    from core.kai.mobile_diagnose import run_diagnostic
    return run_diagnostic()


@app.get("/")
def root_redirect():
    return RedirectResponse(url="/command-center")


from core.bridge_auth import (
    API_TOKEN_PATH,
    BRIDGE_OPERATOR,
    _load_api_token,
    require_bridge_token,
)

_load_api_token()  # ensure the token file exists as soon as the API starts,
# not lazily on the first write request -- the plugin bridge needs to
# be able to read it before it ever makes that first call.


# ── Dashboard login (Phase 17D, upgraded to username/password same day) ─────
# The standalone dashboard is served on 0.0.0.0 so anyone on the LAN can load
# it.  The proxy routes below forward requests with the real bridge token, so
# they must gate on something the LAN operator knows but anonymous LAN visitors
# do not.  A username/password pair fills that role: the operator logs in once
# in the UI, the browser stores the credentials (as a pre-built HTTP Basic
# Authorization header) in localStorage, and every proxy request carries it.
# The credentials themselves never grant access to the backend -- that still
# requires the bridge token, which the proxy injects server-side and which
# unconditionally overwrites whatever Authorization header the browser sent
# (see dashboard_proxy below), so a dashboard login can never be replayed as
# the real bridge token.

DASHBOARD_CREDENTIALS_PATH = Path(
    os.environ.get(
        "AI_ORCHESTRATOR_DASHBOARD_CREDENTIALS_PATH",
        str(Path.home() / ".ai-orchestrator" / "dashboard_credentials.json"),
    )
)

DASHBOARD_PROXY_OPERATOR = "dashboard-proxy"

DEFAULT_DASHBOARD_USERNAME = "Kai"
DEFAULT_DASHBOARD_PASSWORD = "2pEgK9msGB6BGoxKVW7b75wl98BW5Hr-GGQ4rZiRWCQ"


def _load_dashboard_credentials() -> dict:
    """Return {"username": ..., "password": ...}, creating the file with the
    default credentials on first use (same security hygiene as
    _load_api_token: 0600 file, 0700 parent dir). The admin can change the
    password (or username) anytime via POST /dashboard/api/change-password,
    or by editing this file directly -- it's plain JSON, not encrypted, since
    it protects a same-LAN convenience gate, not the real bridge token."""
    if not DASHBOARD_CREDENTIALS_PATH.exists():
        DASHBOARD_CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        DASHBOARD_CREDENTIALS_PATH.parent.chmod(0o700)
        try:
            fd = os.open(DASHBOARD_CREDENTIALS_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            try:
                os.write(
                    fd,
                    json.dumps(
                        {"username": DEFAULT_DASHBOARD_USERNAME, "password": DEFAULT_DASHBOARD_PASSWORD}
                    ).encode(),
                )
            finally:
                os.close(fd)
    return json.loads(DASHBOARD_CREDENTIALS_PATH.read_text())


def _save_dashboard_credentials(username: str, password: str) -> None:
    DASHBOARD_CREDENTIALS_PATH.write_text(json.dumps({"username": username, "password": password}))
    DASHBOARD_CREDENTIALS_PATH.chmod(0o600)


# Ensure the credentials file exists on startup (same reasoning as the bridge
# token: the dashboard page's JS needs the login gate to be live before the
# first interactive request).
_load_dashboard_credentials()


def _parse_basic_auth(authorization: str | None) -> tuple[str, str] | None:
    if not authorization or not authorization.lower().startswith("basic "):
        return None
    try:
        decoded = base64.b64decode(authorization[6:]).decode("utf-8")
        username, _, password = decoded.partition(":")
    except Exception:
        return None
    return username, password


def _require_dashboard_login(
    authorization: str | None = Header(default=None),
) -> None:
    """FastAPI dependency: enforces HTTP Basic username/password (checked
    against DASHBOARD_CREDENTIALS_PATH) on proxy routes."""
    creds = _load_dashboard_credentials()
    parsed = _parse_basic_auth(authorization)
    if parsed is None:
        raise HTTPException(status_code=401, detail="Missing or invalid dashboard login")
    username, password = parsed
    username_ok = hmac.compare_digest(username.encode(), creds["username"].encode())
    password_ok = hmac.compare_digest(password.encode(), creds["password"].encode())
    if not (username_ok and password_ok):
        raise HTTPException(status_code=401, detail="Missing or invalid dashboard login")


class DashboardChangePasswordRequest(BaseModel):
    new_password: str
    new_username: str | None = None


@app.post("/dashboard/api/change-password")
def dashboard_change_password(
    body: DashboardChangePasswordRequest,
    _: None = Depends(_require_dashboard_login),
):
    """Admin can change the dashboard password (and optionally username)
    anytime, gated on already knowing the current login -- not the real
    bridge token, this only ever governs the same-LAN convenience gate."""
    if not body.new_password:
        raise HTTPException(status_code=400, detail="new_password must not be empty")
    creds = _load_dashboard_credentials()
    new_username = body.new_username or creds["username"]
    _save_dashboard_credentials(new_username, body.new_password)
    return {"ok": True, "username": new_username}


# ── Dashboard same-origin proxy (Phase 17D) ──────────────────────────────────
# Routes under /dashboard/api/proxy/* forward requests to the backend with the
# real bridge token injected.  The browser never sees the token; it only sees
# the passphrase it already knows.

_PROXY_TARGET_HOST = os.environ.get("AI_ORCHESTRATOR_API_HOST", "127.0.0.1")
_PROXY_TARGET_PORT = int(os.environ.get("AI_ORCHESTRATOR_API_PORT", "8000"))
_PROXY_BASE_URL = f"http://{_PROXY_TARGET_HOST}:{_PROXY_TARGET_PORT}"

# In tests, set AI_ORCHESTRATOR_PROXY_BASE_URL to override the target URL.
# For in-process ASGI testing (where the proxy IS the backend), callers can
# call _set_proxy_client() with an httpx.AsyncClient configured with an
# ASGITransport so no real network connection is needed.
_PROXY_BASE_URL = os.environ.get("AI_ORCHESTRATOR_PROXY_BASE_URL", _PROXY_BASE_URL)
_proxy_client: httpx.AsyncClient | None = None


# Global variables for SSE connections and event broadcasting


_sse_event_queue = asyncio.Queue()

def _set_proxy_client(client: "httpx.AsyncClient | None") -> None:
    """Override the proxy's httpx client.  For tests only."""
    global _proxy_client
    _proxy_client = client


def _get_proxy_client() -> httpx.AsyncClient:
    global _proxy_client
    if _proxy_client is None or _proxy_client.is_closed:
        _proxy_client = httpx.AsyncClient(base_url=_PROXY_BASE_URL)
    return _proxy_client


# Strip hop-by-hop headers that must not be forwarded.
_HOP_BY_HOP = frozenset([
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
    # The caller's own Authorization header (the dashboard login, HTTP
    # Basic) is stripped before forwarding -- it is unconditionally replaced
    # with the real bridge token below regardless, this just makes that
    # intent explicit rather than relying solely on the later overwrite.
    "authorization",
])


@app.api_route(
    "/dashboard/api/proxy/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
async def dashboard_proxy(
    path: str,
    request: Request,
    _: None = Depends(_require_dashboard_login),
):
    """Phase 17D: server-side proxy for the standalone dashboard.

    Validates the caller is logged in (dashboard username/password), then
    forwards the request to the backend with the real bridge token injected.
    The token never travels to the browser -- only the dashboard login does,
    and that login only guards access to this proxy, not to the backend
    directly.
    """
    token = _load_api_token()
    client = _get_proxy_client()

    # Build forwarded headers: drop hop-by-hop + our own passphrase header,
    # inject the real bridge token.
    fwd_headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in _HOP_BY_HOP
    }
    fwd_headers["authorization"] = f"Bearer {token}"
    fwd_headers["host"] = f"{_PROXY_TARGET_HOST}:{_PROXY_TARGET_PORT}"

    # Preserve query string.
    qs = request.url.query
    target_path = f"/{path}" + (f"?{qs}" if qs else "")

    body = await request.body()

    try:
        proxy_response = await client.request(
            method=request.method,
            url=target_path,
            headers=fwd_headers,
            content=body,
        )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Backend unreachable: {exc}")

    # Strip hop-by-hop from the response before forwarding back.
    resp_headers = {
        k: v
        for k, v in proxy_response.headers.items()
        if k.lower() not in _HOP_BY_HOP
    }

    return StreamingResponse(
        content=iter([proxy_response.content]),
        status_code=proxy_response.status_code,
        headers=resp_headers,
    )


class ApprovalAction(BaseModel):
    note: str | None = None


class CreateBuildRequest(BaseModel):
    name: str
    description: str
    project_path: str
    template: str | None = None


class AnswerAction(BaseModel):
    answer: str


class LoginRequest(BaseModel):
    username: str
    password: str


# ── Phase 15A: capability-based auth (login/logout + viewer sessions) ────────
# Every write endpoint below calls require_write_capability(cap) to gate access.
# Bridge-token callers (the CloudCLI plugin bridge) continue to work unchanged
# with full operator capabilities.  Viewer accounts authenticate via
# username/password to get a session token with read-only access.

_SESSION_HEADER_NAME = "X-Kai-Session"


def _require_session_token(x_kai_session: str | None = Header(default=None)) -> str:
    """Extract the session token from the X-Kai-Session header.  Returns the
    raw token for downstream capability resolution — does NOT validate on its
    own, since read endpoints don't gate on sessions at all."""
    return x_kai_session or ""


def _require_write_capability(capability: str):
    """FastAPI dependency factory.  Returns a callable that checks the caller
    has *capability* — either via the bridge token (always operator) or via
    a valid session token with the matching role capability."""

    def checker(
        authorization: str | None = Header(default=None),
        x_kai_session: str | None = Header(default=None),
    ) -> str:
        session_token = x_kai_session or ""

        # Bridge-token path: the existing mechanism, always operator
        expected = f"Bearer {_load_api_token()}"
        if authorization and hmac.compare_digest(authorization.encode(), expected.encode()):
            return BRIDGE_OPERATOR

        # Session-token path: resolve role and check capability
        if session_token:
            if authz.check_capability(session_token, capability):
                return session_token
            # Authenticated but not authorized → 403
            raise HTTPException(status_code=403, detail="Insufficient permissions")

        # Neither path — no valid credentials at all → 401
        raise HTTPException(status_code=401, detail="Missing or invalid credentials")

    return checker


# ── Admin rate limiting (WI-03) ────────────────────────────────────────────
# In-memory sliding window: max ADMIN_RATE_LIMIT requests per ADMIN_RATE_WINDOW
# per client IP.  Lost on restart (acceptable for human-driven admin actions).

import time as _time_admin
from collections import defaultdict as _defaultdict

ADMIN_RATE_LIMIT = 10       # max requests per window
ADMIN_RATE_WINDOW = 60      # window size in seconds
_admin_rate_state: dict[str, list[float]] = _defaultdict(list)


def _check_admin_rate_limit(request: Request, operator: str = ""):
    """Raise HTTP 429 if the caller exceeds the admin rate limit.

    Called from juris admin endpoints AFTER the capability check passes,
    so we only count requests from authenticated operators.
    """
    client = request.client.host if request.client else "unknown"
    key = f"{client}:{operator}" if operator else client
    now = _time_admin.monotonic()
    window = ADMIN_RATE_WINDOW

    # Clean expired entries
    timestamps = _admin_rate_state[key]
    _admin_rate_state[key] = [t for t in timestamps if now - t < window]

    if len(_admin_rate_state[key]) >= ADMIN_RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Too many admin requests")

    _admin_rate_state[key].append(now)


@app.post("/auth/login")
def auth_login(body: LoginRequest, response: Response):
    """Authenticate with username/password, return a session token.
    The caller receives a Bearer-style token scoped to their role.
    A http-only cookie is also set so the SPA doesn't need to manage tokens."""
    token = authz.authenticate(body.username, body.password)
    if token is None:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    response.set_cookie(
        key="kai_session",
        value=token,
        httponly=True,
        secure=False,  # LAN-only, no TLS
        samesite="lax",
        max_age=86400,  # 24h
        path="/",
    )
    return {"token": token, "token_type": "session", "role": authz.resolve_role(token)}

class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


@app.post("/auth/change-password")
def auth_change_password(
    body: ChangePasswordRequest,
    x_kai_session: str | None = Header(default=None),
):
    """Change the current user's password. Requires valid session + old password."""
    session = authz._resolve_session(x_kai_session or "")
    if session is None:
        raise HTTPException(status_code=401, detail="Valid session required")
    username = session["username"]
    accounts = authz._read_accounts()
    entry = accounts.get(username)
    if entry is None:
        raise HTTPException(status_code=404, detail="Account not found")
    if not authz._verify_password(body.old_password, entry["password_hash"]):
        raise HTTPException(status_code=403, detail="Current password is incorrect")
    if len(body.new_password) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters")
    accounts[username]["password_hash"] = authz._hash_password(body.new_password)
    authz._write_accounts(accounts)
    authz.invalidate_session(x_kai_session or "")
    return {"ok": True}



@app.post("/auth/logout")
def auth_logout(x_kai_session: str | None = Header(default=None)):
    """Invalidate a session token."""
    authz.invalidate_session(x_kai_session or "")
    return {"status": "ok"}


@app.get("/auth/status")
def auth_status(
    authorization: str | None = Header(default=None),
    x_kai_session: str | None = Header(default=None),
):
    """Return the current caller's role and capabilities."""
    session_token = x_kai_session or ""
    expected = f"Bearer {_load_api_token()}"
    if authorization and hmac.compare_digest(authorization.encode(), expected.encode()):
        return {"role": "operator", "auth_method": "bridge_token"}
    role = authz.resolve_role(session_token)
    if role:
        caps = sorted(authz.ROLE_CAPABILITIES.get(role, set()))
        return {"role": role, "auth_method": "session", "capabilities": caps}
    return {"role": "anonymous", "auth_method": "none", "capabilities": []}


# ── Phase 15C: Audit log ─────────────────────────────────────────────────

AUDIT_SOURCES = {
    "build_history": "build_history.json",
    "approval_queue": "approval_queue.json",
    "decisions": "decisions.json",
    "incidents": "incidents.json",
    "gateway_audit": "gateway_audit.json",
    "secret_access_audit": "secret_access_audit.json",
    "ai_usage_history": "ai_usage_history.json",
    "remediation_history": "remediation_history.json",
    "verification_history": "verification_history.json",
}


def _normalize_build(entry: dict) -> dict | None:
    if not entry.get("build_id"):
        return None
    return {
        "timestamp": entry.get("timestamp", entry.get("created", "")),
        "source": "build",
        "action": f"build.{entry.get('status', 'unknown').lower()}",
        "actor": entry.get("generated_by", entry.get("operator", "system")),
        "summary": entry.get("name", entry.get("build_id", "")),
        "status": entry.get("status", "unknown"),
        "detail": _trim_detail(entry),
    }


def _normalize_approval(entry: dict) -> dict | None:
    if not entry.get("id"):
        return None
    return {
        "timestamp": entry.get("created", ""),
        "source": "approval",
        "action": f"approval.{entry.get('status', 'pending').lower()}",
        "actor": _last_actor(entry.get("history", [])),
        "summary": entry.get("action", entry.get("reason", "")),
        "status": entry.get("status", "pending"),
        "detail": {"id": entry["id"], "trace_id": entry.get("trace_id", ""), "service": entry.get("service", "")},
    }


def _normalize_decision(entry: dict) -> dict | None:
    if not entry.get("id"):
        return None
    return {
        "timestamp": entry.get("created", ""),
        "source": "decision",
        "action": f"decision.{entry.get('status', 'pending').lower()}",
        "actor": _last_actor(entry.get("history", [])),
        "summary": entry.get("recommended_action", entry.get("problem", "")),
        "status": entry.get("status", "pending"),
        "detail": {"id": entry["id"], "cause_probability": entry.get("cause_probability")},
    }


def _normalize_incident(entry: dict) -> dict | None:
    if not entry.get("id"):
        return None
    return {
        "timestamp": entry.get("created", ""),
        "source": "incident",
        "action": f"incident.{entry.get('status', 'open').lower()}",
        "actor": _last_actor(entry.get("history", [])),
        "summary": entry.get("issue", entry.get("id", "")),
        "status": entry.get("status", "open"),
        "detail": {"id": entry["id"], "severity": entry.get("severity"), "service": entry.get("service", "")},
    }


def _normalize_gateway(entry: dict) -> dict | None:
    if not entry.get("trace_id"):
        return None
    status = entry.get("status_code", 0)
    return {
        "timestamp": entry.get("timestamp", ""),
        "source": "gateway",
        "action": f"gateway.{'success' if 200 <= status < 300 else 'error'}",
        "actor": entry.get("consumer", "system"),
        "summary": f"{entry.get('provider', '?')} — {entry.get('model', 'auto')} ({entry.get('duration_ms', 0)}ms)",
        "status": str(status),
        "detail": {"trace_id": entry["trace_id"], "model": entry.get("model"), "error": entry.get("error")},
    }


def _normalize_secret(entry: dict) -> dict | None:
    if not entry.get("timestamp"):
        return None
    return {
        "timestamp": entry["timestamp"],
        "source": "secret",
        "action": f"secret.{entry.get('action', 'access')}",
        "actor": "system",
        "summary": f"{entry.get('provider', '?')}: {entry.get('success', False)}",
        "status": "success" if entry.get("success") else "failure",
        "detail": {"provider": entry.get("provider"), "detail": entry.get("detail")},
    }


def _normalize_ai_usage(entry: dict) -> dict | None:
    if not entry.get("timestamp"):
        return None
    return {
        "timestamp": entry["timestamp"],
        "source": "ai",
        "action": "ai.delegate",
        "actor": entry.get("operator", "system"),
        "summary": f"{entry.get('provider', '?')} — {entry.get('task_type', '?')} ({'success' if entry.get('success') else 'failed'})",
        "status": "success" if entry.get("success") else "error",
        "detail": {"provider": entry.get("provider"), "task_type": entry.get("task_type"), "duration_ms": entry.get("duration_ms")},
    }


def _normalize_remediation(entry: dict) -> dict | None:
    if not entry.get("timestamp"):
        return None
    return {
        "timestamp": entry["timestamp"],
        "source": "remediation",
        "action": f"remediation.{entry.get('action', 'unknown')}",
        "actor": "system",
        "summary": entry.get("incident", entry.get("action", "")),
        "status": entry.get("result", "unknown"),
        "detail": {"incident": entry.get("incident"), "action": entry.get("action")},
    }


def _normalize_verification(entry: dict) -> dict | None:
    if not entry.get("timestamp"):
        return None
    return {
        "timestamp": entry["timestamp"],
        "source": "verification",
        "action": f"verification.{entry.get('status', 'unknown')}",
        "actor": "system",
        "summary": f"{entry.get('service', '?')}: {entry.get('remaining_findings', '?')} remaining",
        "status": entry.get("status", "unknown"),
        "detail": {"service": entry.get("service"), "remaining_findings": entry.get("remaining_findings")},
    }


def _last_actor(history: list) -> str:
    """Extract the last human actor from a status-change history."""
    if not history:
        return "system"
    for entry in reversed(history):
        actor = entry.get("operator") or entry.get("approved_by") or entry.get("actor", "")
        if actor and actor not in ("system", "kai", "auto"):
            return actor
    return history[-1].get("operator", "system") if history else "system"


def _trim_detail(entry: dict) -> dict:
    """Return a subset of entry fields safe for the audit detail column."""
    skip = {"timestamp", "source", "action", "actor", "summary", "status", "history"}
    return {k: v for k, v in entry.items() if k not in skip and not isinstance(v, (list, dict))}


def _load_audit_source(filename: str) -> list[dict]:
    try:
        data = load(filename)
        if isinstance(data, dict):
            return data.get("records", []) or data.get("history", []) or []
        if isinstance(data, list):
            return data
        return []
    except Exception:
        return []


_NORMALIZERS = {
    "build_history": _normalize_build,
    "approval_queue": _normalize_approval,
    "decisions": _normalize_decision,
    "incidents": _normalize_incident,
    "gateway_audit": _normalize_gateway,
    "secret_access_audit": _normalize_secret,
    "ai_usage_history": _normalize_ai_usage,
    "remediation_history": _normalize_remediation,
    "verification_history": _normalize_verification,
}


@app.get("/audit")
def get_audit_log(
    request: Request,
    actor: str | None = None,
    source: str | None = None,
    action: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 200,
    format: str = "json",
):
    """Merged chronological audit log from all Kai data sources (9 total).

    Query params:
      actor      — filter by operator/username
      source     — filter by source: build, approval, decision, incident,
                   gateway, secret, ai, remediation, verification
      action     — filter by action prefix (e.g. 'build.failed')
      date_from  — ISO date string, inclusive
      date_to    — ISO date string, inclusive
      limit      — max entries (default 200, max 1000)
      format     — 'json' (default) or 'csv'

    Requires dashboard login (Basic Auth).
    """
    limit = min(limit, 1000)
    entries: list[dict] = []

    # Real client IP from forwarded headers, or the direct client.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        client_ip = forwarded.split(",")[0].strip()
    elif request.headers.get("x-real-ip"):
        client_ip = request.headers["x-real-ip"].strip()
    elif request.client and request.client.host:
        client_ip = request.client.host
    else:
        client_ip = "127.0.0.1"

    for source_key, filename in AUDIT_SOURCES.items():
        if source and source_key != source:
            continue
        normalizer = _NORMALIZERS.get(source_key)
        if normalizer is None:
            continue
        for raw in _load_audit_source(filename):
            entry = normalizer(raw)
            if entry is None:
                continue
            entry["source_ip"] = client_ip
            entries.append(entry)

    # Filter
    if actor:
        entries = [e for e in entries if actor.lower() in e.get("actor", "").lower()]
    if action:
        entries = [e for e in entries if e.get("action", "").startswith(action)]
    if date_from:
        entries = [e for e in entries if e.get("timestamp", "") >= date_from]
    if date_to:
        entries = [e for e in entries if e.get("timestamp", "") <= date_to]

    # Sort newest first
    entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    total = len(entries)
    entries = entries[:limit]

    if format == "csv":
        import io, csv as _csv
        output = io.StringIO()
        writer = _csv.DictWriter(output, fieldnames=["timestamp", "source", "action", "actor", "summary", "status", "source_ip"])
        writer.writeheader()
        for e in entries:
            writer.writerow({k: e.get(k, "") for k in ["timestamp", "source", "action", "actor", "summary", "status", "source_ip"]})
        return Response(content=output.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": "attachment; filename=kai_audit.csv"})

    return {"total": total, "returned": len(entries), "entries": entries}


# ── Phase 15D: SSE endpoint ──────────────────────────────────────────────


@app.get("/events")
async def sse_events(request: Request):
    """SSE endpoint for real-time dashboard updates. Clients receive push
    events for build status transitions, new pending approvals, and roadmap
    phase completions. Falls back gracefully to poll if SSE unavailable."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    _SSE_CLIENTS.append(queue)

    async def event_stream():
        try:
            yield f"event: connected\ndata: {_sse_json.dumps({'status': 'connected'})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield data
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            if queue in _SSE_CLIENTS:
                _SSE_CLIENTS.remove(queue)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── Phase 19A: Module auto-registry ──────────────────────────────────────


@app.get("/api/modules")
def modules_endpoint():
    """Return auto-discovered modules registered with Kai Command Center."""
    return {"modules": list(get_registered_modules().values())}


@app.get("/api/modules/{name}/config")
def module_config_get(name: str):
    """Return runtime config for a specific module (loaded from memory file)."""
    try:
        from core.memory import load_memory
        cfg = load_memory("module_config", default={})
        return {"name": name, "config": cfg.get(name, {})}
    except Exception as e:
        return {"name": name, "config": {}, "error": str(e)}


@app.put("/api/modules/{name}/config")
def module_config_put(
    name: str,
    body: dict = Body(...),
    operator: str = Depends(_require_write_capability("delegate.use")),
):
    """Persist runtime config for a specific module."""
    try:
        from core.memory import load_memory, save_memory
        cfg = load_memory("module_config", default={})
        cfg[name] = body
        save_memory("module_config", cfg)
        return {"name": name, "config": cfg[name], "saved": True}
    except Exception as e:
        return {"name": name, "error": str(e), "saved": False}


# ═══════════════════════════════════════════════════════════════════════════
# Juris Kai Admin API — Account management, referrals, payments
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/api/juris-kai/stats")
def juris_kai_stats(
    _: str = Depends(require_bridge_token),
):
    """Aggregate Juris Kai stats for admin dashboard."""
    try:
        from core.juris_kai.dashboard import get_dashboard_stats
        return get_dashboard_stats()
    except Exception as e:
        return {"error": str(e), "juris_kai": {}}


@app.get("/api/juris-kai/accounts")
def juris_kai_accounts(
    q: str = "", tier: str = "", active_only: bool = False,
    page: int = 1, per_page: int = 50,
    _: str = Depends(require_bridge_token),
):
    """List/search Juris Kai accounts."""
    try:
        from core.juris_kai.accounts import get_account_manager
        mgr = get_account_manager()
        if q or tier or active_only:
            return mgr.find_accounts(query=q, tier=tier, active_only=active_only,
                                     page=page, per_page=per_page)
        from core.juris_kai.dashboard import list_accounts
        return list_accounts(page=page, per_page=per_page, active_only=active_only)
    except Exception as e:
        return {"error": str(e), "accounts": []}


@app.get("/api/juris-kai/accounts/{account_id}")
def juris_kai_account_detail(
    account_id: str,
    _: str = Depends(require_bridge_token),
):
    """Get detailed account info for admin view."""
    try:
        from core.juris_kai.dashboard import get_account_detail
        detail = get_account_detail(account_id)
        if not detail:
            return JSONResponse(status_code=404, content={"error": "Account not found"})
        return detail
    except Exception as e:
        return {"error": str(e)}


def _log_juris_admin(operator: str, account_id: str, action: str, meta: dict = None):
    """Record an admin action in the Juris Kai security log. Fire-and-forget."""
    try:
        import json as _json
        from core.juris_kai.accounts import get_account_manager as _gam
        _mgr = _gam()
        _mgr.db.execute(
            """INSERT INTO juris_security_log
               (telegram_id, event_type, details, ip_address)
               VALUES (?, 'admin_action', ?, ?)""",
            (operator, _json.dumps({
                "account_id": account_id,
                "action": action,
                **({"meta": meta} if meta else {}),
            }), "api"),
        )
        _mgr.db.commit()
    except Exception:
        pass  # auditing must not break the endpoint


@app.post("/api/juris-kai/accounts/{account_id}/subscription")
def juris_kai_set_subscription(
    account_id: str,
    body: dict = Body(...),
    operator: str = Depends(_require_write_capability("juris.admin")),
    request: Request = None,
):
    """Change an account's subscription tier."""
    _check_admin_rate_limit(request, operator)
    try:
        from core.juris_kai.dashboard import update_subscription
        new_tier = body.get("tier", "")
        result = update_subscription(account_id, new_tier)
        if result.get("success"):
            _log_juris_admin(operator, account_id, "set_subscription", {"tier": new_tier})
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/juris-kai/accounts/{account_id}/deactivate")
def juris_kai_deactivate(
    account_id: str,
    body: dict = Body(...),
    operator: str = Depends(_require_write_capability("juris.admin")),
    request: Request = None,
):
    """Ban/deactivate an account."""
    _check_admin_rate_limit(request, operator)
    try:
        from core.juris_kai.accounts import get_account_manager
        mgr = get_account_manager()
        reason = body.get("reason", "admin_action")
        result = mgr.ban_account(account_id, reason)
        if result.get("success"):
            _log_juris_admin(operator, account_id, "deactivate", {"reason": reason})
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/juris-kai/accounts/{account_id}/activate")
def juris_kai_activate(
    account_id: str,
    operator: str = Depends(_require_write_capability("juris.admin")),
    request: Request = None,
):
    """Reactivate/unban an account."""
    _check_admin_rate_limit(request, operator)
    try:
        from core.juris_kai.accounts import get_account_manager
        mgr = get_account_manager()
        result = mgr.unban_account(account_id)
        if result.get("success"):
            _log_juris_admin(operator, account_id, "activate")
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/juris-kai/accounts/{account_id}/grant-days")
def juris_kai_grant_days(
    account_id: str,
    body: dict = Body(...),
    operator: str = Depends(_require_write_capability("juris.admin")),
    request: Request = None,
):
    """Grant N free days to an account."""
    _check_admin_rate_limit(request, operator)
    try:
        from core.juris_kai.accounts import get_account_manager
        mgr = get_account_manager()
        days = body.get("days", 1)
        reason = body.get("reason", "admin_grant")
        result = mgr.grant_free_days(account_id, days, reason)
        if result.get("success"):
            _log_juris_admin(operator, account_id, "grant_days", {"days": days, "reason": reason})
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/juris-kai/referrals")
def juris_kai_referrals(
    _: str = Depends(require_bridge_token),
):
    """List all referrals (admin view)."""
    try:
        from core.juris_kai.accounts import get_account_manager
        mgr = get_account_manager()
        return {"referrals": mgr.get_all_referrals()}
    except Exception as e:
        return {"error": str(e), "referrals": []}


@app.post("/api/juris-kai/referrals/generate")
def juris_kai_referral_generate(
    body: dict = Body(...),
    operator: str = Depends(_require_write_capability("juris.admin")),
    request: Request = None,
):
    """Generate a new invite code for an account."""
    _check_admin_rate_limit(request, operator)
    try:
        from core.juris_kai.accounts import get_account_manager
        mgr = get_account_manager()
        inviter_id = body.get("account_id", "")
        result = mgr.generate_invite_code(inviter_id)
        if result.get("success"):
            _log_juris_admin(operator, inviter_id, "generate_referral",
                             {"invite_code": result.get("invite_code")})
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/juris-kai/payments")
def juris_kai_payments(
    account_id: str = "", limit: int = 50,
    _: str = Depends(require_bridge_token),
):
    """Get payment history."""
    try:
        from core.juris_kai.dashboard import get_payment_history
        return {"payments": get_payment_history(account_id=account_id or None, limit=limit)}
    except Exception as e:
        return {"error": str(e), "payments": []}


@app.get("/api/juris-kai/security-log")
def juris_kai_security_log(
    event_type: str = "", limit: int = 100,
    _: str = Depends(require_bridge_token),
):
    """Get security event log."""
    try:
        from core.juris_kai.accounts import get_account_manager
        mgr = get_account_manager()
        return {"events": mgr.get_security_logs(event_type=event_type, limit=limit)}
    except Exception as e:
        return {"error": str(e), "events": []}


# ═══════════════════════════════════════════════════════════════════════════
# SUSU Admin API — User & group management
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/api/susu/stats")
def susu_stats(
    _: str = Depends(require_bridge_token),
):
    """Aggregate SUSU stats."""
    try:
        from core.susu import models as susu_models
        users = susu_models._load("users", default={"index": {}, "list": []})
        groups = susu_models._load("groups", default={"index": {}, "list": []})
        transactions = susu_models._load("transactions", default={"index": {}, "list": []})
        fees = susu_models._load("fees", default={"index": {}, "list": []})

        tx_list = transactions.get("list", [])
        total_deposits = sum(float(t.get("amount", 0)) for t in tx_list
                            if t.get("tx_type") == "DEPOSIT" and t.get("status") == "COMPLETED")
        total_withdrawals = sum(float(t.get("amount", 0)) for t in tx_list
                               if t.get("tx_type") == "WITHDRAWAL" and t.get("status") == "COMPLETED")
        total_fees = sum(float(f.get("amount", 0)) for f in fees.get("list", [])
                        if f.get("status") == "PAID")

        return {
            "total_users": len(users.get("list", [])),
            "total_groups": len(groups.get("list", [])),
            "active_groups": len([g for g in groups.get("list", []) if g.get("status") == "ACTIVE"]),
            "total_deposits_ghs": round(total_deposits, 2),
            "total_withdrawals_ghs": round(total_withdrawals, 2),
            "total_fees_collected_ghs": round(total_fees, 2),
            "pending_transactions": len([t for t in tx_list if t.get("status") == "PENDING"]),
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/susu/users")
def susu_users(
    _: str = Depends(require_bridge_token),
):
    """List all SUSU users."""
    try:
        from core.susu import models as susu_models
        users = susu_models._load("users", default={"index": {}, "list": []})
        # Enrich with balance
        enriched = []
        for u in users.get("list", []):
            uid = u.get("id") or u.get("telegram_id")
            balance = susu_models.get_user_balance(uid) if uid else 0
            enriched.append({**u, "balance_ghs": balance})
        return {"users": sorted(enriched, key=lambda u: u.get("created_at", ""), reverse=True)}
    except Exception as e:
        return {"error": str(e), "users": []}


@app.get("/api/susu/groups")
def susu_groups(
    _: str = Depends(require_bridge_token),
):
    """List all SUSU groups with member counts."""
    try:
        from core.susu import models as susu_models
        groups = susu_models._load("groups", default={"index": {}, "list": []})
        enriched = []
        for g in groups.get("list", []):
            members = susu_models.get_members(g["id"])
            fees = susu_models.get_fees_for_group(g["id"])
            enriched.append({
                **g,
                "member_count": len(members),
                "fees_collected": sum(float(f.get("amount", 0)) for f in fees if f.get("status") == "PAID"),
                "fees_pending": len([f for f in fees if f.get("status") == "PENDING"]),
            })
        return {"groups": sorted(enriched, key=lambda g: g.get("created_at", ""), reverse=True)}
    except Exception as e:
        return {"error": str(e), "groups": []}


@app.get("/api/susu/transactions")
def susu_transactions(
    group_id: str = "", user_id: str = "", limit: int = 100,
    _: str = Depends(require_bridge_token),
):
    """List SUSU transactions with optional filters."""
    try:
        from core.susu import models as susu_models
        tx = susu_models._load("transactions", default={"index": {}, "list": []})
        result = tx.get("list", [])
        if group_id:
            result = [t for t in result if t.get("group_id") == group_id]
        if user_id:
            result = [t for t in result if t.get("user_id") == user_id]
        result.sort(key=lambda t: t.get("created_at", ""), reverse=True)
        return {"transactions": result[:limit]}
    except Exception as e:
        return {"error": str(e), "transactions": []}


# ═══════════════════════════════════════════════════════════════════════════
# Legal Brain Admin API
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/api/legal-brain/stats")
def legal_brain_stats(
    _: str = Depends(require_bridge_token),
):
    """Get Legal Brain knowledge engine stats."""
    try:
        from core.legal_brain.permanent import get_connection
        stats = {}
        with get_connection() as conn:
            # Document count
            try:
                r = conn.execute("SELECT COUNT(*) as c FROM klaus_documents").fetchone()
                stats["documents"] = r["c"] if r else 0
            except Exception:
                stats["documents"] = "n/a"

            # Tier counts
            try:
                r = conn.execute(
                    "SELECT tier_id, COUNT(*) as c FROM klaus_documents GROUP BY tier_id ORDER BY tier_id"
                ).fetchall()
                stats["by_tier"] = {str(row["tier_id"]): row["c"] for row in r}
            except Exception:
                stats["by_tier"] = {}

            # Source count
            try:
                r = conn.execute("SELECT COUNT(*) as c FROM klaus_sources").fetchone()
                stats["sources"] = r["c"] if r else 0
            except Exception:
                stats["sources"] = "n/a"

            # Research sessions
            try:
                r = conn.execute("SELECT COUNT(*) as c FROM research_sessions").fetchone()
                stats["sessions"] = r["c"] if r else 0
            except Exception:
                stats["sessions"] = "n/a"

            # Jurisdictions covered
            try:
                r = conn.execute(
                    "SELECT jurisdiction, COUNT(*) as c FROM research_sessions GROUP BY jurisdiction"
                ).fetchall()
                stats["by_jurisdiction"] = {row["jurisdiction"]: row["c"] for row in r}
            except Exception:
                stats["by_jurisdiction"] = {}

        return stats
    except Exception as e:
        return {"error": str(e)}


@app.get("/health")
def health():

    findings = analyze()

    status = "degraded" if any(f.get("severity") == "critical" for f in findings) else "ok"

    return {
        "status": status,
        "findings": findings,
        "last_scan": load("system_state.json").get("last_scan")
    }


@app.get("/incidents")
def incidents():
    return load_incidents()


@app.get("/decisions")
def decisions():
    return load_decisions()


@app.get("/approvals")
def approvals():
    return load_requests()


@app.get("/actions")
def actions():
    return load_remediations()


@app.get("/verifications")
def verifications():
    return load_verification_history()


@app.get("/learning")
def learning():
    return summarize()


@app.get("/learning/builds")
def build_learning_endpoint():
    return {
        "templates": summarize_templates(),
        "history": get_build_history(),
    }


@app.get("/learning/lessons")
def learning_lessons_endpoint():
    """Phase 13F lesson store (preferred architectures, common failures,
    successful solutions, avoided approaches), aggregated per-subject the
    same way /learning/builds aggregates per-template. Consumed by the
    CloudCLI plugin's Kai Control Center "Lessons learned" card (13G)."""
    return summarize_lessons()


@app.post("/approvals/{request_id}/approve")
def approve_request(
    request_id: str,
    action: ApprovalAction = ApprovalAction(),
    operator: str = Depends(_require_write_capability("approvals.approve")),
):

    try:
        result = approve(request_id, note=action.note, operator=operator)
    except InvalidTransition as error:
        raise HTTPException(status_code=409, detail=str(error))
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error))

    if result is None:
        raise HTTPException(status_code=404, detail="Approval request not found")

    return result


@app.post("/approvals/{request_id}/reject")
def reject_request(
    request_id: str,
    action: ApprovalAction = ApprovalAction(),
    operator: str = Depends(_require_write_capability("approvals.reject")),
):

    try:
        result = reject(request_id, note=action.note, operator=operator)
    except InvalidTransition as error:
        raise HTTPException(status_code=409, detail=str(error))

    if result is None:
        raise HTTPException(status_code=404, detail="Approval request not found")

    return result


@app.get("/enterprise/dashboard")
def enterprise_dashboard_endpoint():
    """15E: Consolidated enterprise dashboard — health, providers, usage,
    approvals, roadmap, builds. One call for the overview tab."""
    from core.enterprise_dashboard import get_enterprise_snapshot
    return get_enterprise_snapshot()


@app.get("/providers")
def providers_endpoint():
    return list_providers()


@app.get("/providers/dashboard")
def providers_dashboard_endpoint():
    return get_provider_dashboard()


@app.get("/providers/config")
def get_provider_config():
    """Return operator overrides with validation context (Phase 17U).

    Shape: {schema_version, overrides: {fallback_order, max_concurrent_builds},
    validation: {valid, errors, warnings}}.
    """
    return provider_config_editor.get_full_config()


@app.put("/providers/config")
def update_provider_config(
    body: dict,
    operator: str = Depends(_require_write_capability("delegate.use")),
):
    """Save operator overrides. Body: {fallback_order: {role: [provider, ...]},
    max_concurrent_builds: N}.  Validates every provider name against the
    registry and rejects unknown names with 422.
    """
    success, errors, warnings = provider_config_editor.save_overrides(body)
    if not success:
        raise HTTPException(status_code=422, detail={"errors": errors, "warnings": warnings})

    overrides = provider_config_editor.load_overrides().get("overrides", {})
    return {"saved": True, "overrides": overrides}


@app.delete("/providers/config")
def delete_provider_config(
    operator: str = Depends(_require_write_capability("delegate.use")),
):
    """Reset all operator overrides back to the hardcoded defaults."""
    from core.memory import save as mem_save

    mem_save(provider_config_editor.OVERRIDES_FILE, {"schema_version": 1, "overrides": {}})
    return {"reset": True}


@app.get("/providers/chains")
def get_all_chains():
    """Convenience endpoint for the Provider Chains dashboard tab.
    Returns a flat dict mapping module name -> ordered provider list,
    with defaults filled in for any module not present in the overrides file.
    Unlike /providers/config (which returns the raw overrides), this merges
    with ROLE_PROVIDERS defaults so the UI has a complete picture.
    Also returns a `default_chains` map so the frontend can reset individual
    modules back to defaults without needing to hardcode ROLE_PROVIDERS.
    """
    overrides = provider_config_editor.load_overrides().get("overrides", {})
    fallback_order = overrides.get("fallback_order", {})
    chains = {}
    default_chains = {}
    for module, default_chain in ROLE_PROVIDERS.items():
        default_chains[module] = default_chain
        chains[module] = fallback_order.get(module, default_chain)
    return {"chains": chains, "default_chains": default_chains}


# ---- V3: GPU & Pipeline endpoints ----

@app.get("/api/gpu/status")
def api_gpu_status():
    """2026-08-07: GPU metrics removed — RunPod pods decommissioned."""
    return {"status": "decommissioned", "message": "RunPod GPU pods decommissioned 2026-08-07. DeepSeek Native now serves all AI workloads."}


@app.get("/api/vpn/status")
def api_vpn_status():
    """TK-176d6efe: WireGuard VPN tunnel health for Proxmox B."""
    try:
        import core.vpn_failover as _vf
        import core.proxmox_monitor as _pm
        vpn = _vf.check_tunnel_health()
        vpn["nodes"] = _pm.get_vpn_status()
        return vpn
    except Exception as e:
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════
# Docker Container Management (Phase 19B)
# ═══════════════════════════════════════════════════════════════════════════

DOCKER_SOCK = "/var/run/docker.sock"
_docker_client: httpx.AsyncClient | None = None


def _get_docker_client() -> httpx.AsyncClient:
    global _docker_client
    if _docker_client is None:
        _docker_client = httpx.AsyncClient(
            transport=httpx.HTTPTransport(uds=DOCKER_SOCK),
            base_url="http://localhost",
            timeout=10.0,
        )
    return _docker_client


@app.get("/api/docker/containers")
async def docker_containers():
    """List all Docker containers with status, image, ports, and health."""
    try:
        client = _get_docker_client()
        resp = await client.get("/containers/json?all=true")
        resp.raise_for_status()
        containers = resp.json()
        result = []
        for c in containers:
            name = (c.get("Names") or ["unknown"])[0].lstrip("/")
            state = c.get("State", "unknown")
            status = c.get("Status", "")
            result.append({
                "name": name,
                "id": c.get("Id", "")[:12],
                "image": c.get("Image", ""),
                "state": state,
                "status": status,
                "ports": [p.get("PublicPort") for p in c.get("Ports", []) if p.get("PublicPort")],
                "created": datetime.fromtimestamp(c.get("Created", 0), tz=timezone.utc).isoformat(),
            })
        return {"containers": result}
    except Exception as e:
        return {"error": str(e), "containers": []}


@app.post("/api/docker/containers/{name}/start")
async def docker_container_start(
    name: str,
    operator: str = Depends(_require_write_capability("delegate.use")),
):
    """Start a stopped container by name."""
    try:
        client = _get_docker_client()
        resp = await client.post(f"/containers/{name}/start")
        if resp.status_code == 204:
            return {"ok": True, "action": "start", "container": name}
        return {"ok": False, "error": f"HTTP {resp.status_code}", "detail": resp.text[:200]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/docker/containers/{name}/stop")
async def docker_container_stop(
    name: str,
    operator: str = Depends(_require_write_capability("delegate.use")),
):
    """Stop a running container by name."""
    try:
        client = _get_docker_client()
        resp = await client.post(f"/containers/{name}/stop")
        if resp.status_code == 204:
            return {"ok": True, "action": "stop", "container": name}
        return {"ok": False, "error": f"HTTP {resp.status_code}", "detail": resp.text[:200]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/docker/containers/{name}/restart")
async def docker_container_restart(
    name: str,
    operator: str = Depends(_require_write_capability("delegate.use")),
):
    """Restart a container by name."""
    try:
        client = _get_docker_client()
        resp = await client.post(f"/containers/{name}/restart")
        if resp.status_code == 204:
            return {"ok": True, "action": "restart", "container": name}
        return {"ok": False, "error": f"HTTP {resp.status_code}", "detail": resp.text[:200]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/docker/containers/{name}/logs")
async def docker_container_logs(name: str, tail: int = 100):
    """Get container logs (stdout + stderr), last N lines."""
    try:
        client = _get_docker_client()
        resp = await client.get(f"/containers/{name}/logs?stdout=true&stderr=true&tail={tail}")
        resp.raise_for_status()
        # Docker log stream includes 8-byte header per frame; strip it
        raw = resp.content
        # Simple approach: skip the Docker stream header bytes
        text = ""
        i = 0
        while i < len(raw):
            if i + 8 > len(raw):
                break
            stream_type = raw[i]
            frame_size = int.from_bytes(raw[i+4:i+8], 'big')
            i += 8
            if i + frame_size > len(raw):
                break
            text += raw[i:i+frame_size].decode('utf-8', errors='replace')
            i += frame_size
        lines = text.strip().split('\n')[-tail:]
        return {"ok": True, "container": name, "logs": lines}
    except Exception as e:
        return {"ok": False, "error": str(e), "logs": []}


@app.get("/api/pipeline")
def api_pipeline_overview():
    """Pipeline overview: counts by build status."""
    from core.build_manager import load_builds
    builds = load_builds(include_terminal=True)
    statuses = {}
    for b in builds:
        s = b.get("status", "?")
        statuses[s] = statuses.get(s, 0) + 1

    return {
        "pipeline": {
            "planning": statuses.get("PLANNING", 0),
            "generating": statuses.get("GENERATING", 0),
            "review": statuses.get("CODE_REVIEW", 0) + statuses.get("SECURITY_REVIEW", 0),
            "deploying": statuses.get("DEPLOYING", 0),
            "completed": statuses.get("COMPLETED", 0),
            "failed": statuses.get("FAILED", 0),
            "blocked": statuses.get("ARCHITECTURE_APPROVED", 0),
            "waiting_approval": (
                statuses.get("WAITING_FOR_ARCHITECTURE_APPROVAL", 0) +
                statuses.get("WAITING_FOR_DEPLOY_APPROVAL", 0)
            ),
            "total_active": len([b for b in builds if b.get("status") not in ("COMPLETED", "FAILED", "ROLLED_BACK")]),
        },
        "by_status": statuses,
    }


@app.get("/api/budget")
def api_budget_dashboard():
    """AI-5: Cost tracking dashboard — real usage data with pricing."""
    from core.ai.cost_tracker import get_cost_summary, get_monthly_summary

    monthly = get_monthly_summary()
    summary_30d = get_cost_summary(days=30)
    summary_7d = get_cost_summary(days=7)

    return {
        "monthly": monthly,
        "trailing_30d": summary_30d,
        "trailing_7d": summary_7d,
    }


@app.get("/api/costs/providers/{provider}")
def api_cost_provider_detail(provider: str, days: int = 30):
    """AI-5: Per-provider cost breakdown."""
    from core.ai.cost_tracker import get_provider_cost_detail

    return get_provider_cost_detail(provider, days=days)


@app.get("/api/costs/trend")
def api_cost_trend(days: int = 14):
    """AI-5: Daily cost trend."""
    from core.ai.cost_tracker import get_daily_trend

    return {"trend": get_daily_trend(days=days)}


@app.get("/api/costs/monthly")
def api_cost_monthly(year: int = None, month: int = None):
    """AI-5: Monthly cost summary (defaults to current month)."""
    from core.ai.cost_tracker import get_monthly_summary

    return get_monthly_summary(year=year, month=month)


@app.get("/api/costs/export")
def api_cost_export(days: int = 30, format: str = "json"):
    """AI-5: Cost history export (JSON or CSV)."""
    from core.ai.cost_tracker import get_cost_export
    from fastapi.responses import PlainTextResponse

    records = get_cost_export(days=days)

    if format == "csv":
        import csv
        from io import StringIO

        buf = StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "timestamp", "provider", "task_type", "cost_usd",
            "cost_source", "duration_ms", "success", "description",
        ])
        for r in records:
            writer.writerow([
                r["timestamp"], r["provider"], r["task_type"],
                r["cost_usd"], r["cost_source"], r["duration_ms"],
                r["success"], r["description"],
            ])
        return PlainTextResponse(
            buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=cost_export_{days}d.csv"},
        )

    return {"records": records, "count": len(records), "days": days}

# ---- end V3 ----


@app.get("/self-healing/log")
def self_healing_log_endpoint():
    """16B: Return the last self-healing actions taken."""
    from core.self_healing import run_self_healing
    return {"actions": run_self_healing()}


@app.get("/improvement/report")
def improvement_report_endpoint():
    """16C: Continuous improvement analysis from real data."""
    from core.continuous_improvement import get_improvement_report
    return get_improvement_report()


@app.get("/stuck-phases")
def stuck_phases_endpoint():
    """14A: Detect stuck phases and question loops."""
    from core.stuck_detector import get_stuck_report
    return get_stuck_report()


@app.post("/stuck-phases/{build_id}/resolve")
def resolve_stuck_endpoint(
    build_id: str,
    operator: str = Depends(_require_write_capability("builds.answer")),
):
    """14A: Auto-resolve a question loop."""
    from core.stuck_detector import auto_resolve_question_loop
    return auto_resolve_question_loop(build_id)


@app.get("/roadmap/proposals")
def roadmap_proposals_endpoint():
    """13I: AI-generated roadmap phase proposals from build patterns + chat."""
    from core.roadmap_generator import generate_roadmap_proposals
    return generate_roadmap_proposals()


@app.get("/koa/status")
def koa_status_endpoint():
    """17Q: Kai Operations Appliance — full system status snapshot."""
    from core.koa import get_appliance_status
    return get_appliance_status()


@app.get("/providers/free")
def free_providers_endpoint():
    """17M: Free-tier provider status with evaluation checklist."""
    from core.free_providers import get_free_provider_status
    return get_free_provider_status()


@app.get("/routing/weights")
def routing_weights_endpoint():
    """13L: Provider performance weights used for routing."""
    from core.weighted_routing import get_weighted_routing_report
    return get_weighted_routing_report()


@app.get("/proxmox/registry")
def proxmox_registry_endpoint():
    """17E: Full multi-node Proxmox inventory."""
    from core.proxmox_registry import get_registry_summary
    return get_registry_summary()


@app.get("/portfolio")
def portfolio_endpoint():
    """17I: Application portfolio with maintenance proposals."""
    from core.app_portfolio import get_portfolio_report
    return get_portfolio_report()


@app.get("/apps")
def list_apps_endpoint():
    """17L: List existing applications with deployment status."""
    from core.existing_apps import list_apps
    return list_apps()


@app.post("/apps/{app_name}/deploy")
def deploy_app_endpoint(
    app_name: str,
    operator: str = Depends(_require_write_capability("builds.approve_deploy")),
):
    """17L: Deploy to an existing application."""
    from core.existing_apps import deploy_to_app
    return deploy_to_app(app_name, "main", operator=operator)


@app.get("/proxmox/nodes")
def proxmox_nodes_endpoint():
    """17F: Multi-node Proxmox health snapshot."""
    from core.proxmox_monitor import collect_all_nodes
    return collect_all_nodes()


@app.get("/workers")
def workers_endpoint():
    """15G: Per-worker detail view with performance trends and current tasks."""
    return get_worker_details()


class ProviderToggle(BaseModel):
    enabled: bool


@app.put("/providers/{name}/toggle")
def toggle_provider_endpoint(
    name: str,
    body: ProviderToggle,
    operator: str = Depends(_require_write_capability("delegate.use")),
):
    """Enable or disable a provider.  Disabled providers are never tried
    by the router, regardless of availability.  Use to manually take a
    provider offline (e.g. out of credit) or bring it back."""
    if not set_provider_enabled(name, body.enabled):
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")
    return {"name": name, "enabled": body.enabled}


@app.delete("/providers/{name}")
def delete_provider_endpoint(
    name: str,
    operator: str = Depends(_require_write_capability("delegate.use")),
):
    """Permanently remove a provider from Kai.

    Deletes the provider from the registry, persisted state, and all
    routing chains.  Returns 409 if this is the last provider of its
    capability type (e.g. last coding_agent provider)."""
    from core.ai_provider import get_provider
    info = get_provider(name)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")

    # Guard: don't let the user delete the last coding-capable provider
    caps = info.get("capabilities") or []
    if "coding_agent" in caps:
        from core.ai_provider import _PROVIDERS as _all
        other_coding = any(
            n != name and "coding_agent" in (e.get("capabilities") or [])
            for n, e in _all.items()
        )
        if not other_coding:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot delete '{name}': it is the last coding_agent provider. "
                       "Add another coding provider first.",
            )

    roles_removed = remove_provider_from_roles(name)
    deregister_provider(name)
    return {
        "deleted": True,
        "name": name,
        "roles_removed": roles_removed,
    }


class ProviderRegisterBody(BaseModel):
    name: str
    kind: str = "cloud"               # cloud | local | api
    description: str = ""
    cost_tier: str = "free_or_low_cost"  # free_or_low_cost | medium_cost | high_cost | custom
    capabilities: list[str] = []       # e.g. ["coding_agent", "text_task", "file_access"]


@app.post("/providers")
def register_provider_endpoint(
    body: ProviderRegisterBody,
    operator: str = Depends(_require_write_capability("delegate.use")),
):
    """Register a new AI provider.

    Creates a lightweight provider entry with the given capabilities.
    The provider starts with no run_* callbacks — it will be a
    placeholder until a proper worker is configured. This endpoint is
    designed for admin UI use, letting operators add providers that
    can later be fully configured.

    If a provider with the same name already exists, returns 409.
    """
    from core.ai_provider import get_provider
    from core.ai.ai_router import ROLE_PROVIDERS
    existing = get_provider(body.name)
    if existing is not None:
        raise HTTPException(
            status_code=409, detail=f"Provider '{body.name}' already exists"
        )

    # Register with None callbacks — placeholder until full config
    caps = set(body.capabilities)
    has_coding = "coding_agent" in caps
    has_text = "text_task" in caps

    register_provider(
        name=body.name,
        run_coding_task=(lambda *a, **kw: None) if has_coding else None,
        run_text_task=(lambda *a, **kw: None) if has_text else None,
        kind=body.kind,
        description=body.description,
        cost_tier=body.cost_tier,
    )

    # Optionally add to text_task routing if requested
    if has_text:
        ROLE_PROVIDERS.setdefault("classification", []).append(body.name)
        ROLE_PROVIDERS.setdefault("documentation", []).append(body.name)

    return {
        "registered": True,
        "name": body.name,
        "capabilities": sorted(caps),
        "kind": body.kind,
        "cost_tier": body.cost_tier,
    }


# ── AI Agent Registry endpoints ─────────────────────────────────────────
# CRUD + enable/disable + test + stats for agents (model + version + GPU +
# cost + benchmarks).  Write endpoints require bridge token or admin session.

class AgentRegisterRequest(BaseModel):
    agent_id: str
    name: str
    provider_key: str
    model_name: str
    model_version: str = ""
    gpu_type: str = ""
    gpu_memory_gb: int = 0
    cost_per_hour: float = 0.0
    capabilities: list[str] = []
    fallback_chain: list[str] = []
    description: str = ""


class AgentBenchmarkRequest(BaseModel):
    latency_ms: int
    accuracy_score: float = 0.0
    tool_use_success_rate: float = 0.0


class AgentStatusRequest(BaseModel):
    enabled: bool


class AgentTestRequest(BaseModel):
    timeout: int = 30


@app.get("/kai/agents")
def agents_list_endpoint(status: str | None = None):
    """List all registered AI agents, optionally filtered by status."""
    return {"agents": list_agents(status=status)}


@app.get("/kai/agents/{agent_id}")
def agents_get_endpoint(agent_id: str):
    """Get a single agent by ID."""
    agent = get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
    return agent


@app.post("/kai/agents")
def agents_register_endpoint(
    body: AgentRegisterRequest,
    operator: str = Depends(_require_write_capability("delegate.use")),
):
    """Register (or update) an AI agent."""
    return register_agent(
        agent_id=body.agent_id,
        name=body.name,
        provider_key=body.provider_key,
        model_name=body.model_name,
        model_version=body.model_version,
        gpu_type=body.gpu_type,
        gpu_memory_gb=body.gpu_memory_gb,
        cost_per_hour=body.cost_per_hour,
        capabilities=body.capabilities,
        fallback_chain=body.fallback_chain,
        description=body.description,
    )


@app.post("/kai/agents/{agent_id}/enable")
def agents_enable_endpoint(
    agent_id: str,
    operator: str = Depends(_require_write_capability("delegate.use")),
):
    """Enable an agent (sets status to 'active', syncs with provider)."""
    if not enable_agent(agent_id):
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
    agent = get_agent(agent_id)
    return {"ok": True, "agent_id": agent_id, "status": agent["status"] if agent else "active"}


@app.post("/kai/agents/{agent_id}/disable")
def agents_disable_endpoint(
    agent_id: str,
    operator: str = Depends(_require_write_capability("delegate.use")),
):
    """Disable an agent (sets status to 'disabled', syncs with provider)."""
    if not disable_agent(agent_id):
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
    agent = get_agent(agent_id)
    return {"ok": True, "agent_id": agent_id, "status": agent["status"] if agent else "disabled"}


@app.post("/kai/agents/{agent_id}/test")
def agents_test_endpoint(
    agent_id: str,
    body: AgentTestRequest | None = None,
    operator: str = Depends(_require_write_capability("delegate.use")),
):
    """Run a quick health test against the agent's provider."""
    timeout = body.timeout if body else 30
    result = test_agent(agent_id, timeout=timeout)
    if not result.get("ok"):
        status_code = 404 if "not found" in str(result.get("error", "")).lower() else 503
        raise HTTPException(status_code=status_code, detail=result.get("error", "Test failed"))
    return result


@app.get("/kai/agents/{agent_id}/stats")
def agents_stats_endpoint(agent_id: str):
    """Aggregate stats: successful runs, avg latency, total cost."""
    stats = get_agent_stats(agent_id)
    if not stats:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
    return stats


@app.get("/kai/agents/{agent_id}/costs")
def agents_costs_endpoint(agent_id: str, limit: int = 50):
    """Recent cost entries, newest first."""
    if not get_agent(agent_id):
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
    return {"agent_id": agent_id, "costs": get_cost_history(agent_id, limit=limit)}


@app.get("/kai/agents/{agent_id}/performance")
def agents_performance_endpoint(agent_id: str, limit: int = 50):
    """Recent performance data points, newest first."""
    if not get_agent(agent_id):
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
    return {"agent_id": agent_id, "performance": get_performance_history(agent_id, limit=limit)}


@app.post("/kai/agents/{agent_id}/benchmarks")
def agents_benchmark_endpoint(
    agent_id: str,
    body: AgentBenchmarkRequest,
    operator: str = Depends(_require_write_capability("delegate.use")),
):
    """Record benchmark results for an agent."""
    if not get_agent(agent_id):
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
    record_benchmark(
        agent_id,
        latency_ms=body.latency_ms,
        accuracy_score=body.accuracy_score,
        tool_use_success_rate=body.tool_use_success_rate,
    )
    return {"ok": True, "agent_id": agent_id}


@app.post("/kai/agents/bootstrap")
def agents_bootstrap_endpoint(
    operator: str = Depends(_require_write_capability("delegate.use")),
):
    """Seed the agent registry with agents from existing providers. Idempotent."""
    count = bootstrap_default_agents()
    return {"ok": True, "created": count, "total_agents": len(list_agents())}


# ── AI-7: Credential Vault endpoints ─────────────────────────────────────
# Write-gated endpoints for managing encrypted provider credentials.
# All responses exclude the actual api_key — keys are never returned
# through any API; they are only decrypted internally at call time.

from core.ai.credential_vault import (
    store_credential,
    retrieve_credential,
    retrieve_api_key as vault_get_api_key,
    list_vault_entries,
    delete_vault_entry,
    check_health as vault_check_health,
    check_rotation_needed,
    mark_rotated,
    migrate_plaintext_keys,
    get_vault_status,
    ROTATION_DAYS,
    OVERLAP_DAYS,
)


class VaultCredentialRequest(BaseModel):
    api_key: str
    api_base: str = ""
    models: list[str] = []


@app.get("/kai/vault")
def vault_list_endpoint():
    """List all credential vault entries (metadata only, no keys)."""
    return {
        "entries": list_vault_entries(),
        "status": get_vault_status(),
    }


@app.get("/kai/vault/rotation")
def vault_rotation_endpoint():
    """Check which credentials are due for rotation."""
    return {
        "rotation_due": check_rotation_needed(),
        "rotation_days": ROTATION_DAYS,
        "overlap_days": OVERLAP_DAYS,
    }


@app.post("/kai/vault/{provider}")
def vault_store_endpoint(
    provider: str,
    body: VaultCredentialRequest,
    operator: str = Depends(_require_write_capability("delegate.use")),
):
    """Store or update an encrypted credential. The key is never logged."""
    store_credential(provider, body.api_key, body.api_base, body.models)
    mark_rotated(provider)
    return {"ok": True, "provider": provider}


@app.delete("/kai/vault/{provider}")
def vault_delete_endpoint(
    provider: str,
    operator: str = Depends(_require_write_capability("delegate.use")),
):
    """Delete a credential from the vault."""
    if not delete_vault_entry(provider):
        raise HTTPException(status_code=404, detail=f"No credential for '{provider}'")
    return {"ok": True, "provider": provider}


@app.post("/kai/vault/{provider}/rotate")
def vault_rotate_endpoint(
    provider: str,
    body: VaultCredentialRequest,
    operator: str = Depends(_require_write_capability("delegate.use")),
):
    """Rotate a provider's API key. Old key is logged for audit."""
    store_credential(provider, body.api_key, body.api_base, body.models)
    mark_rotated(provider)
    return {"ok": True, "provider": provider, "rotated": True}


@app.post("/kai/vault/{provider}/health")
def vault_health_endpoint(provider: str):
    """Test a credential by calling the provider's /v1/models."""
    result = vault_check_health(provider)
    return {"provider": provider, **result}


@app.post("/kai/vault/migrate")
def vault_migrate_endpoint(
    operator: str = Depends(_require_write_capability("delegate.use")),
):
    """One-time: encrypt existing plaintext keys with AES-256-GCM."""
    result = migrate_plaintext_keys()
    return result


# ── AI Circuit Breaker endpoints ─────────────────────────────────────────
# Per-provider circuit breaker management: list, inspect, reset, configure.

class CircuitBreakerConfig(BaseModel):
    threshold: int = 3
    cooldown_seconds: int = 300


class CircuitBreakerTrip(BaseModel):
    detail: str = "manual trip"


@app.get("/kai/circuit-breakers")
def circuit_breakers_list_endpoint():
    """List all circuit breakers with their state, failures, and config."""
    breakers = circuit_breaker.list_all_breakers()
    now = datetime.now(timezone.utc)
    enriched = []
    for b in breakers:
        cooldown = b.get("cooldown_seconds", 300)
        tripped_at = b.get("tripped_at")
        remaining = None
        if tripped_at and b.get("state") == "open":
            try:
                elapsed = (now - datetime.fromisoformat(tripped_at)).total_seconds()
                remaining = max(0, int(cooldown - elapsed))
            except (ValueError, TypeError):
                pass
        enriched.append({**b, "cooldown_remaining_seconds": remaining})
    return {"circuit_breakers": enriched}


@app.get("/kai/circuit-breakers/{provider}")
def circuit_breaker_get_endpoint(provider: str):
    """Get a single provider's circuit breaker state."""
    snapshot = circuit_breaker.get_breaker_snapshot(provider)
    if not snapshot:
        return {"provider": provider, "state": "closed", "consecutive_failures": 0}
    return {"provider": provider, **snapshot}


@app.post("/kai/circuit-breakers/{provider}/reset")
def circuit_breaker_reset_endpoint(
    provider: str,
    operator: str = Depends(_require_write_capability("delegate.use")),
):
    """Reset a provider's circuit breaker (clear all failures)."""
    circuit_breaker.clear_breaker(provider)
    return {"ok": True, "provider": provider, "state": "closed"}


@app.post("/kai/circuit-breakers/reset-all")
def circuit_breaker_reset_all_endpoint(
    operator: str = Depends(_require_write_capability("delegate.use")),
):
    """Reset every circuit breaker."""
    count = circuit_breaker.reset_all_breakers()
    return {"ok": True, "cleared": count}


@app.post("/kai/circuit-breakers/{provider}/trip")
def circuit_breaker_trip_endpoint(
    provider: str,
    body: CircuitBreakerTrip | None = None,
    operator: str = Depends(_require_write_capability("delegate.use")),
):
    """Force-trip a provider's circuit breaker (for testing/admin)."""
    detail = body.detail if body else "manual trip"
    entry = circuit_breaker.trip_breaker_manually(provider, detail=detail)
    return {"ok": True, "provider": provider, **entry}


@app.put("/kai/circuit-breakers/{provider}/config")
def circuit_breaker_config_endpoint(
    provider: str,
    body: CircuitBreakerConfig,
    operator: str = Depends(_require_write_capability("delegate.use")),
):
    """Set per-provider failure threshold and cooldown."""
    circuit_breaker.set_threshold(provider, body.threshold)
    circuit_breaker.set_cooldown(provider, body.cooldown_seconds)
    return {
        "ok": True,
        "provider": provider,
        "threshold": body.threshold,
        "cooldown_seconds": body.cooldown_seconds,
    }


@app.get("/scheduler/snapshot")
def scheduler_snapshot_endpoint():
    """13J: read-only scheduler state for the Kai Control Center."""
    return get_scheduler_snapshot()


@app.get("/api/command-center/summary")
def command_center_summary_endpoint():
    """13O: consolidated read-only payload for the Kai Command Center tab.

    Aggregates every section the Command Center renders from existing
    backend services/registries/workflows -- no duplicated business logic,
    no parallel data sources.  Fetch-on-open, no polling responsibility."""
    from datetime import datetime, timezone

    from core.ai.ai_router import get_usage_history, ROLE_PROVIDERS
    from core.ai.provider_health import get_all_quota_snapshots
    from core.build_manager import load_builds as _load_builds
    from core.build_manager import _RUNNING_STATUSES, _WAITING_STATUSES
    from core.build_learning import summarize_lessons, get_build_history
    from core.approval import load_requests
    from core.learning import summarize as learning_summarize

    now = datetime.now(timezone.utc)

    # ── 1. AI Workforce ────────────────────────────────────────────────
    pd = get_provider_dashboard()
    providers = list_providers()
    history = get_usage_history()

    workforce = {}
    for name, info in providers.items():
        d = pd.get(name, {})
        roles = []
        for role, candidates in ROLE_PROVIDERS.items():
            if name in candidates:
                priority = candidates.index(name) + 1
                roles.append({"role": role, "priority": priority})
        workforce[name] = {
            "name": name,
            "kind": info.get("kind"),
            "model": info.get("description"),
            "roles": roles,
            "capabilities": info.get("capabilities", []),
            "coding_agent": "coding_agent" in (info.get("capabilities") or []),
            "available": info.get("available", False),
            "cost_tier": info.get("cost_tier"),
            "status": d.get("status", "unknown"),
            "health": d.get("health", "unknown"),
            "current_build": d.get("current_job"),
            "current_build_name": d.get("current_job_name"),
            "routing_priority": roles[0]["priority"] if roles else None,
            "success_rate": d.get("success_rate"),
            "average_duration_ms": d.get("average_duration_ms"),
            "queue_depth": d.get("queue_depth", 0),
            "total_attempts": d.get("total_attempts", 0),
            "total_successes": d.get("total_successes", 0),
        }

    # ── 2. Live Build Queue ────────────────────────────────────────────
    all_builds = []
    try:
        all_builds = _load_builds() or []
    except Exception:
        pass

    running = []
    waiting = []
    waiting_for_approval = []
    failed = []
    recently_completed = []

    _APPROVAL_STATUSES = {"WAITING_FOR_ARCHITECTURE_APPROVAL", "WAITING_FOR_DEPLOY_APPROVAL"}

    for b in all_builds:
        status = b.get("status", "")
        created_at = b.get("created_at")
        try:
            elapsed_s = int((now - datetime.fromisoformat(created_at.replace("Z", "+00:00"))).total_seconds()) if created_at else None
        except (ValueError, TypeError):
            elapsed_s = None

        entry = {
            "id": b.get("id"),
            "name": b.get("name"),
            "status": status,
            "phase": b.get("template"),
            "assigned_worker": b.get("generated_by"),
            "start_time": created_at,
            "elapsed_seconds": elapsed_s,
            "elapsed_display": _fmt_duration(elapsed_s) if elapsed_s is not None else None,
            "failure_reason": b.get("failure_reason"),
        }

        if status in _RUNNING_STATUSES:
            running.append(entry)
        elif status in _WAITING_STATUSES:
            waiting.append(entry)
        elif status in _APPROVAL_STATUSES:
            waiting_for_approval.append(entry)
        elif status == "FAILED":
            failed.append(entry)
        elif status == "COMPLETED":
            recently_completed.append(entry)

    recently_completed = recently_completed[-10:]

    # ── 3. Provider Health (extended) ──────────────────────────────────
    quota_snapshots = get_all_quota_snapshots()
    provider_health_data = {}
    for name, info in providers.items():
        d = pd.get(name, {})
        qs = quota_snapshots.get(name, {})

        # consecutive failures & last failure from usage history
        provider_entries = [e for e in history if e["provider"] == name]
        last_failure = None
        consecutive_failures = 0
        for e in reversed(provider_entries):
            if not e.get("success"):
                if last_failure is None:
                    last_failure = e.get("timestamp")
                consecutive_failures += 1
            else:
                break

        # health score: 100-based, -20 per consecutive failure, -10 if quota_exceeded, capped at 0
        hscore = 100
        if consecutive_failures:
            hscore -= consecutive_failures * 20
        if qs.get("status") == "quota_exceeded":
            hscore -= 10
        health_score = max(hscore, 0)

        provider_health_data[name] = {
            "name": name,
            "health": d.get("health", "unknown"),
            "health_score": health_score,
            "last_failure": last_failure,
            "consecutive_failures": consecutive_failures,
            "quota_status": qs.get("status"),
            "quota_detail": qs.get("detail"),
            "percent_remaining": d.get("percent_remaining"),
            "average_latency_ms": d.get("average_duration_ms"),
            "cooldown_until": None,  # no cooldown mechanism yet
        }

    # ── 4. Kai Status ──────────────────────────────────────────────────
    roadmap_progress = get_progress_summary()
    scheduler = get_scheduler_snapshot()
    identity = {
        "name": "Kai",
        "identity": kai_identity.get_identity(),
        "mission": kai_mission.get_mission(),
        "capabilities": kai_goals.get_capabilities(),
        "restrictions": kai_policies.get_restrictions(),
    }
    kai_status = {
        "name": identity["name"],
        "identity": identity["identity"],
        "current_objective": identity["mission"],
        "active_builds": scheduler["running_builds"][0]["name"] if scheduler["running_builds"] else None,
        "active_build_id": scheduler["running_builds"][0]["id"] if scheduler["running_builds"] else None,
        "roadmap_phase": None,
        "task": None,
        "waiting_on": None,
        "next_planned_action": None,
        "last_completed_action": None,
    }

    # 13O: fill Kai Status from active build context when one exists
    active_running = scheduler["running_builds"]
    if active_running:
        rb = active_running[0]
        kai_status["roadmap_phase"] = rb.get("phase")
        kai_status["task"] = rb.get("status")
        # If there are waiting builds, Kai is also waiting on them
        if scheduler["waiting_builds"]:
            kai_status["waiting_on"] = "build queue: " + ", ".join(
                wb["name"] for wb in scheduler["waiting_builds"][:3]
            )

    # last completed build
    completed_builds = [b for b in all_builds if b.get("status") == "COMPLETED"]
    if completed_builds:
        kai_status["last_completed_action"] = completed_builds[-1].get("name")

    # next planned: check for next roadmap phase
    try:
        next_phase = get_next_phase()
        if next_phase and next_phase.get("id"):
            kai_status["next_planned_action"] = f"Phase {next_phase['id']}: {next_phase.get('name', '')}"
    except Exception:
        pass

    # ── 5. Human Approval Feed ─────────────────────────────────────────
    try:
        approval_requests = load_requests() or []
    except Exception:
        approval_requests = []

    approval_feed = []
    for ar in approval_requests:
        approval_feed.append({
            "id": ar.get("id"),
            "build_id": ar.get("build_id"),
            "approval_type": ar.get("approval_type"),
            "title": ar.get("title"),
            "status": ar.get("status"),
            "description": ar.get("description", "")[:200],
            "risk": ar.get("risk"),
            "created_at": ar.get("created_at") if ar.get("created_at") else None,
        })

    # ── 6 & 7: Workforce Utilization & Worker Performance ──────────────
    # computed from execution history -- no separate data store
    utilization = {}
    performance = {}
    for name in providers:
        provider_entries = [e for e in history if e["provider"] == name]
        total = len(provider_entries)
        successes = sum(1 for e in provider_entries if e.get("success"))
        failures = total - successes
        durations = [e["duration_ms"] for e in provider_entries if e.get("duration_ms") is not None]

        utilization[name] = {
            "name": name,
            "total_tasks": total,
            "max_tasks": max(total, 1),
        }

        performance[name] = {
            "name": name,
            "tasks_completed": successes,
            "total_tasks": total,
            "success_rate": (successes / total) if total else None,
            "failure_rate": (failures / total) if total else None,
            "avg_runtime_ms": (sum(durations) / len(durations)) if durations else None,
            "avg_retries": None,  # retries not tracked per-worker
            "last_execution": provider_entries[-1]["timestamp"] if provider_entries else None,
        }

    # ── 8. Build Timelines ─────────────────────────────────────────────
    build_timelines = []
    for b in all_builds:
        timeline_entry = {
            "build_id": b.get("id"),
            "name": b.get("name"),
            "status": b.get("status"),
            "phase": b.get("template"),
            "created_at": b.get("created_at"),
            "stages": {},
        }
        build_timelines.append(timeline_entry)

    # ── 9. Expanded Learning Summary ───────────────────────────────────
    try:
        lessons = summarize_lessons()
    except Exception:
        lessons = {}
    try:
        actions = learning_summarize()
    except Exception:
        actions = {}

    preferred_architectures = []
    successful_patterns = []
    common_failures = []
    rejected_trends = []
    avoided_approaches = []

    for subject, data in (lessons or {}).items():
        cat = data.get("category", "")
        item = {
            "subject": subject,
            "attempts": data.get("attempts", 0),
            "success_rate": data.get("success_rate"),
            "recommendation": data.get("recommendation"),
        }
        if cat == "preferred_architecture":
            preferred_architectures.append(item)
        elif cat == "successful_solution":
            successful_patterns.append(item)
        elif cat == "common_failure":
            common_failures.append(item)
        elif cat == "avoided_approach":
            avoided_approaches.append(item)

    learning_summary = {
        "preferred_architectures": preferred_architectures,
        "successful_patterns": successful_patterns,
        "common_failures": common_failures,
        "avoided_approaches": avoided_approaches,
        "action_categories": [],
    }

    if isinstance(actions, dict):
        for k, v in actions.items():
            if isinstance(v, dict) and "total" in v:
                learning_summary["action_categories"].append({
                    "name": k,
                    "total": v.get("total", 0),
                })

    return {
        "workforce": workforce,
        "build_queue": {
            "running": running,
            "waiting": waiting,
            "waiting_for_approval": waiting_for_approval,
            "failed": failed[-10:],
            "recently_completed": recently_completed,
        },
        "provider_health": provider_health_data,
        "kai_status": kai_status,
        "approval_feed": approval_feed,
        "utilization": utilization,
        "performance": performance,
        "build_timelines": build_timelines,
        "learning_summary": learning_summary,
        "modules": get_registered_modules(),
    }


def _fmt_duration(seconds):
    if seconds is None:
        return None
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


# ME-13: read-only money ecosystem view for the Command Center. Proxies
# Money Center's own endpoints with a dedicated viewer token (read-only by
# construction — every write guard in money-center checks role 'user'/'kai').
# No secret values are ever returned; the token lives server-side only.
_MONEY_CENTER_URL = os.environ.get("MONEY_CENTER_URL", "http://192.168.1.118:8095")
_MONEY_VIEWER_TOKEN_FILE = os.environ.get(
    "MONEY_VIEWER_TOKEN_FILE", "/root/.credentials/money-viewer-token"
)


def _money_viewer_token() -> str | None:
    try:
        tok = Path(_MONEY_VIEWER_TOKEN_FILE).read_text(encoding="utf-8").strip()
        return tok or None
    except OSError:
        return None


@app.get("/api/command-center/money")
def command_center_money():
    """ME-13: consolidated READ-ONLY money ecosystem payload for the CC panel."""
    import requests as _rq

    tok = _money_viewer_token()
    if not tok:
        raise HTTPException(status_code=503, detail="money viewer token not provisioned")
    headers = {"authorization": f"Bearer {tok}"}
    out: dict = {}
    for key, path in (
        ("treasury", "/treasury/summary"),
        ("operations", "/operations"),
        ("kai_position", "/kai/position"),
        ("kai_account", "/kai/account"),
        ("kai_audit", "/kai/audit/reports"),
        ("risk_events", "/risk/events?status=open&limit=20"),
        ("decisions", "/decisions?limit=15"),
    ):
        try:
            r = _rq.get(f"{_MONEY_CENTER_URL}{path}", headers=headers, timeout=8)
            out[key] = r.json() if r.ok else {"error": r.status_code}
        except Exception as exc:
            # §54 honesty: an unreachable section is reported, never fabricated.
            out[key] = {"error": str(exc)[:120]}
    # Notifications (admin token on kai-notify) — recent operational alerts.
    notify_url = os.environ.get("KAI_NOTIFY_URL", "http://192.168.1.118:8094")
    try:
        ntok_file = os.environ.get("KAI_NOTIFY_TOKEN_FILE", "")
        ntok = Path(ntok_file).read_text(encoding="utf-8").strip() if ntok_file else None
        r = _rq.get(
            f"{notify_url}/notifications?limit=10",
            headers={"authorization": f"Bearer {ntok}"} if ntok else {},
            timeout=8,
        )
        out["notifications"] = r.json() if r.ok else {"error": r.status_code}
    except Exception as exc:
        out["notifications"] = {"error": str(exc)[:120]}
    return out


@app.get("/command-center/summary")
def command_center_summary():
    """13G: Fetches all data for the Kai Command Center dashboard."""
    # Build status data
    builds = list_builds()
    active_builds = [b for b in builds if b.get("status") in ("generating", "building", "waiting")]
    completed_builds = [b for b in builds if b.get("status") == "completed"]
    
    # Provider data
    providers = list_providers()
    
    # Approval requests
    pending_approvals = list_pending()
    
    # Learning data
    learning_summary = summarize()
    
    # Incident data
    incidents = load_incidents()
    open_incidents = [i for i in incidents if i.get("status") == "open"]
    
    # Verification history
    verification_history = load_verification_history()
    
    # Roadmap data
    roadmap_data = {
        "phases": get_remaining_work(),
        "next_phase": get_next_phase(),
        "progress": get_progress_summary(),
    }
    
    return {
        "build_status": {
            "active_count": len(active_builds),
            "completed_count": len(completed_builds),
            "active_builds": active_builds[:5],  # Limit to 5 for performance
            "completed_builds": completed_builds[:5],
        },
        "provider_status": {
            "total_count": len(providers),
            "providers": providers,
        },
        "approval_requests": {
            "pending_count": len(pending_approvals),
            "requests": pending_approvals[:10],  # Limit to 10 for performance
        },
        "learning_summary": learning_summary,
        "incident_status": {
            "open_count": len(open_incidents),
            "incidents": open_incidents[:10],  # Limit to 10 for performance
        },
        "verification_history": {
            "recent_count": len(verification_history),
            "history": verification_history[:10],  # Limit to 10 for performance
        },
        "roadmap_status": roadmap_data,
    }


class DelegateRequest(BaseModel):
    description: str
    task_type: str | None = None
    project_path: str | None = None


@app.post("/delegate")
def delegate_endpoint(
    body: DelegateRequest,
    operator: str = Depends(_require_write_capability("delegate.use")),
):
    try:
        return delegate(body.description, task_type=body.task_type, project_path=body.project_path)
    except AllProvidersFailed as error:
        raise HTTPException(status_code=502, detail=str(error))


class KaiCommandRequest(BaseModel):
    text: str


class KaiChatRequest(BaseModel):
    text: str
    project_id: str | None = None
    conversation_id: str | None = None


@app.post("/kai/command")
def kai_command_endpoint(
    body: KaiCommandRequest,
    operator: str = Depends(_require_write_capability("kai.command")),
):
    return kai_dispatch(body.text)


@app.get("/kai/identity")
def kai_identity_endpoint():
    """Assembles Kai's identity/mission/capabilities/restrictions statements
    (core/kai/identity.py, mission.py, goals.py, policies.py -- each already
    covered by tests/test_kai_identity.py individually) plus the live
    autonomy state into the single object the CloudCLI plugin's Kai
    Control Center identity card (13G) renders. Read-only and ungated, same
    as the sibling /learning and /roadmap/progress endpoints it sits next to
    on that tab -- nothing here is sensitive beyond what those already
    expose.

    13H: the ``autonomous_mode`` boolean is preserved for backward compat
    (it now maps to ``level >= 4`` -- the exact pre-13H semantics). The
    new ``autonomy_level`` / ``autonomy_set_by`` fields carry the full
    6-level state; the plugin's Overview tab renders those to draw the
    6-position control (replacing the old on/off toggle)."""
    autonomy_record = get_autonomy_level()
    return {
        "name": "Kai",
        "identity": kai_identity.get_identity(),
        "mission": kai_mission.get_mission(),
        "capabilities": kai_goals.get_capabilities(),
        "restrictions": kai_policies.get_restrictions(),
        "autonomous_mode": is_autonomous_mode_enabled(),
        "autonomy_level": autonomy_record["level"],
        "autonomy_set_by": autonomy_record["set_by"],
        "autonomy_updated_at": autonomy_record["updated_at"],
    }


@app.get("/kai/proposals")
def kai_proposals_endpoint():
    """Return all improvement proposals synthesized by the Kai planner.
    Read-only, ungated — same access policy as /kai/identity."""
    return list_proposals()


@app.get("/audit/v2")
def audit_endpoint(
    user: str | None = None,
    action: str | None = None,
    project: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    format: str = "json",
    limit: int = 100,
    offset: int = 0,
    authorization: str | None = Header(default=None),
    x_kai_session: str | None = Header(default=None),
):
    """Phase 15C: Audit trail aggregation endpoint.

    Merges existing build, approval, decision, and incident history into a
    chronological feed with filtering and export capabilities. Read-only
    for all roles including 'viewer' -- an audit log editable by any user
    defeats its purpose.

    Query parameters:
    - user: Filter by user/operator
    - action: Filter by action type
    - project: Filter by project name
    - start_date: Inclusive start date (ISO format)
    - end_date: Inclusive end date (ISO format)
    - format: 'json' (default) or 'csv'
    - limit: Number of entries to return (default: 100)
    - offset: Starting offset for pagination (default: 0)
    """
    session_token = x_kai_session or ""

    if session_token:
        if not authz.check_capability(session_token, "view"):
            raise HTTPException(status_code=403, detail="Insufficient permissions")

    entries = get_audit_entries(user, action, project, start_date, end_date)

    paginated_entries = entries[offset:offset + limit]

    if format.lower() == "csv":
        csv_output = format_audit_entries_as_csv(paginated_entries)
        return Response(content=csv_output, media_type="text/csv")

    metadata = {
        "total_count": len(entries),
        "returned_count": len(paginated_entries),
        "offset": offset,
        "limit": limit,
        "filters": {
            "user": user,
            "action": action,
            "project": project,
            "start_date": start_date,
            "end_date": end_date,
        },
    }
    return format_audit_entries_as_json(paginated_entries, metadata)



_APPROVE_INTENT_RE = re.compile(
    r"approve\s+(architecture|deploy)\s*(?:plan)?\s*(?:#?(request-?\d*|[a-f0-9-]{8,}))?\s*\.?$",
    re.I,
)

_REJECT_INTENT_RE = re.compile(
    r"reject\s+(architecture|deploy)\s*(?:plan)?\s*(?:#?(request-?\d*|[a-f0-9-]{8,}))?\s*\.?$",
    re.I,
)


# 17V: chat history is now managed by core.kai.conversation (session envelopes,
# long-term operator store, guarded compression).  The legacy flat-array
# load/save helpers are gone — add_message() and get_session() replace them.

def _append_chat_message(role, content):
    """Append a message to the session envelope (17V)."""
    from core.kai.conversation import add_message
    return add_message(role, content)


def _get_chat_messages():
    """Return recent messages from the session envelope (17V)."""
    from core.kai.conversation import get_session
    envelope = get_session()
    return envelope.get("recent_messages", [])


def _resolve_approval_request(scope, request_id_hint):
    pending = [r for r in list_pending() if r.get("approval_type") == scope]

    if not pending:
        return None, f"No pending {scope} approval requests found."

    if request_id_hint:
        for r in pending:
            rid = r.get("id", "")
            if request_id_hint.lower() in rid.lower():
                return r, None
        return None, f"No pending {scope} approval matches request id containing {request_id_hint!r}."

    if len(pending) == 1:
        return pending[0], None

    lines = []
    for r in pending:
        lines.append(
            f"  - {r.get('title') or r.get('id')} "
            f"(id: {r['id']}, build: {r.get('build_id')})"
        )
    detail = "Multiple pending " + scope + " approvals:\n" + "\n".join(lines)
    return None, detail


# 17J: cheap pre-filter before ever spending an AI call on intent
# extraction -- most chat messages are plain questions, not build requests.
# Keyword-based, deliberately generous (false positives just cost one extra
# extraction call that comes back is_build_request=false; false negatives
# would silently drop a real request into the open-ended chat fallback).
_BUILD_INTENT_VERBS = ("build", "create", "make", "scaffold", "generate", "start")
_BUILD_INTENT_NOUNS = (
    "app", "application", "website", "site", "api", "service",
    "backend", "frontend", "project",
)


def _looks_like_build_request(text):
    lowered = text.lower()
    return any(v in lowered for v in _BUILD_INTENT_VERBS) and any(n in lowered for n in _BUILD_INTENT_NOUNS)


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name):
    slug = _SLUG_RE.sub("-", name.lower()).strip("-")
    return slug or "app"


# Module-level (not inlined) so tests can point it at a tmp_path instead of
# monkeypatching pathlib.Path.exists globally.
APPLICATION_BUILD_BASE_DIR = Path("/project/src")


def _unique_project_path(slug):
    base = APPLICATION_BUILD_BASE_DIR / slug
    if not base.exists():
        return str(base)
    n = 2
    while (APPLICATION_BUILD_BASE_DIR / f"{slug}-{n}").exists():
        n += 1
    return str(APPLICATION_BUILD_BASE_DIR / f"{slug}-{n}")


_BUILD_EXTRACTION_PROMPT = (
    "The operator sent this message to Kai, an AI orchestrator that can build "
    "new applications on request:\n\n{text}\n\n"
    "Does this message ask Kai to build/create a new application? Respond with "
    "ONLY a JSON object, no other text, matching exactly this shape:\n"
    '{{"is_build_request": true or false, "name": "short-lowercase-hyphenated-slug", '
    '"description": "a concrete one-to-two sentence description of what to build", '
    '"template": one of {templates} or null}}\n\n'
    "If is_build_request is false, name/description/template may be empty/null. "
    "Pick template only if the request clearly matches one of the listed options "
    "(e.g. \"react\" for a general website/frontend, \"fastapi\"/\"django\"/\"node-api\" "
    "for a backend/API); use null if genuinely ambiguous so the build agent decides."
)


def _extract_build_intent(text):
    """Returns {"name", "description", "template"} if `text` expresses a
    request to build a new application, else None. Uses the AI router for
    extraction rather than brittle regex -- free-form requests vary too much
    to pattern-match reliably. Fails closed: any parsing/provider problem is
    treated as "not a build request" so a malformed extraction never blocks
    the normal open-ended chat fallback."""
    if not _looks_like_build_request(text):
        return None

    prompt = _BUILD_EXTRACTION_PROMPT.format(text=text, templates=list(TEMPLATES.keys()))

    try:
        # This is intent classification (does this message ask for a build?
        # extract a name/description/template), not architectural planning --
        # task_type="classification" routes it to fast, structured-output
        # providers (groq once configured) instead of paying for gemini's
        # long-context planning strength on a task that doesn't need it.
        result = delegate(prompt, task_type="classification", capability="text_task")
        raw = result.get("response", "")
    except AllProvidersFailed:
        return None

    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        return None

    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None

    if not parsed.get("is_build_request"):
        return None

    name = str(parsed.get("name") or "").strip()
    description = str(parsed.get("description") or "").strip()
    if not name or not description:
        return None

    template = parsed.get("template")
    if template not in TEMPLATES:
        template = None

    return {"name": name, "description": description, "template": template}


def _reply_content(reply):
    """The assistant turn as it belongs in a chat transcript.

    The response envelope this endpoint returns ({matched, response} for
    open-ended chat, {matched, description, result, error} for a matched
    command) is the wire contract for programmatic callers. The transcript,
    though, is read by a human in the plugin's chat panel, which renders
    content verbatim -- so store the prose, not str() of the envelope, which
    showed the operator "{'matched': False, 'response': '...'}" instead of
    the answer. Structured command results keep their description and are
    dumped as JSON (not a Python repr, whose True/False/None aren't JSON)."""

    if reply.get("error"):
        return str(reply["error"])

    if reply.get("response") is not None:
        return str(reply["response"])

    result = reply.get("result")
    description = reply.get("description") or ""

    if isinstance(result, str):
        return result
    if result is None:
        return description or str(reply)

    rendered = json.dumps(result, indent=2, default=str)
    return f"{description}\n{rendered}" if description else rendered


@app.get("/kai/chat")
def kai_chat_history_endpoint(operator: str = Depends(_require_write_capability("kai.chat.send"))):
    """17B/17V: session envelope — returns the full conversation state
    including active_goal, recent_messages, ephemeral_context, and compressed
    history, not just a flat message array."""
    return _get_chat_messages()


class KaiChatAllProvidersFailed(Exception):
    """Raised by handle_kai_chat when all AI providers fail (502-worthy)."""


def handle_kai_chat(text: str, operator: str) -> dict:
    """Shared Kai chat logic used by both POST /kai/chat and the Telegram bridge.

    Loads/saves conversation history (kai_chat_history.json), dispatches Kai
    commands, resolves approval intents, triggers build creation (17J), and
    falls back to open-ended AI chat -- in exactly this priority order.

    Returns the same reply dict that POST /kai/chat returns.
    Raises KaiChatAllProvidersFailed when all AI providers are unavailable.
    Raises core.lifecycle.InvalidTransition on illegal approval state transitions.
    """
    _append_chat_message("user", text)
    history = _get_chat_messages()

    dispatch_result = kai_dispatch(text)
    if dispatch_result.get("matched"):
        reply = dispatch_result
    else:
        approve_match = _APPROVE_INTENT_RE.match(text)
        reject_match = _REJECT_INTENT_RE.match(text)

        if approve_match or reject_match:
            match = approve_match or reject_match
            scope = match.group(1).lower()
            request_id_hint = match.group(2) if match.lastindex and match.lastindex >= 2 else None
            request, disambiguation = _resolve_approval_request(scope, request_id_hint)

            if request is None:
                reply = {"matched": True, "description": f"{scope} approval", "result": disambiguation, "error": None}
            else:
                if approve_match:
                    approval_result = approve(request["id"], operator=operator)
                    verb = "approved"
                else:
                    approval_result = reject(request["id"], operator=operator)
                    verb = "rejected"

                reply_text = (
                    f"{scope.capitalize()} approval request "
                    f"({request.get('title') or request['id']}) "
                    f"{verb} by operator {operator}."
                )
                reply = {"matched": True, "description": f"{scope} approval intent", "result": reply_text, "error": None}
        elif (build_intent := _extract_build_intent(text)) is not None:
            # 17J: create_build() only inserts a REQUESTED build record --
            # it does not itself plan/generate/deploy anything. The
            # scheduler's own background cycle (advance_builds) does that
            # work on its normal cadence, so this call returns immediately;
            # the chat response must never block on up to
            # GENERATION_TIMEOUT (1200s) of real generation work. Every gate
            # this build passes through (architecture/deploy approval) is
            # the exact same one every other build uses -- create_build()
            # never approves anything, and nothing under core/kai/ ever
            # calls approve_architecture/approve_deploy (see
            # tests/test_kai_identity.py's structural guarantee).
            project_path = _unique_project_path(_slugify(build_intent["name"]))
            build = create_build(
                build_intent["name"], build_intent["description"], project_path,
                template=build_intent["template"],
                priority=True,  # 2026-08-06: Telegram-chat builds get top priority
            )
            reply_text = (
                f"Started a new build: \"{build_intent['name']}\" (id: {build['id']}) "
                f"at {project_path}. I'll let you know here (and via Telegram) once "
                "it needs an architecture or deploy decision from you."
            )
            reply = {"matched": True, "description": "build request", "result": reply_text, "error": None}
        else:
            try:
                signals = gather_signals()
                response_text = ai_chat(history, signals)
            except AllProvidersFailed as error:
                raise KaiChatAllProvidersFailed(str(error)) from error
            reply = {"matched": False, "response": response_text}

    _append_chat_message("assistant", _reply_content(reply))

    return reply


# ═══════════════════════════════════════════════════════════════════════════
# Network Topology Discovery (Tasks 7-9)
# ═══════════════════════════════════════════════════════════════════════════

from core.network_knowledge import load_graph, load_prior
from core.topology_engine import detect_changes, get_natural_summary
from core.network_discovery_cycle import run_network_discovery_cycle


@app.get("/network/topology")
def network_topology(_: str = Depends(require_bridge_token)):
    """Full topology graph JSON — sites, tailscale peers, subnet routes, tunnel status."""
    return load_graph()


@app.get("/network/topology/summary")
def network_topology_summary(_: str = Depends(require_bridge_token)):
    """Human-readable natural language summary of the current topology."""
    graph = load_graph()
    return {"summary": get_natural_summary(graph)}


@app.get("/network/topology/sites")
def network_topology_sites(_: str = Depends(require_bridge_token)):
    """Sites summary — name, LAN subnet, gateway, Proxmox node, LXC/VM counts."""
    graph = load_graph()
    sites = graph.get("sites", {})
    return {"sites": sites}


@app.get("/network/topology/peers")
def network_topology_peers(_: str = Depends(require_bridge_token)):
    """Tailscale peer list — all peers across all nodes."""
    graph = load_graph()
    peers = graph.get("tailscale", {}).get("peers", {})
    return {"peers": peers}


@app.get("/network/topology/routes")
def network_topology_routes(_: str = Depends(require_bridge_token)):
    """Subnet route table — subnet → {advertiser, accepted}."""
    graph = load_graph()
    routes = graph.get("tailscale", {}).get("subnet_routes", {})
    return {"routes": routes}


@app.get("/network/connectivity")
def network_connectivity(_: str = Depends(require_bridge_token)):
    """Last connectivity test results — A→B/B→A latency, packet loss, tunnel status."""
    graph = load_graph()
    return {
        "connectivity": graph.get("connectivity", {}),
        "tunnel": graph.get("tunnel", {}),
    }


@app.post("/network/connectivity/test")
def network_connectivity_test(_: str = Depends(_require_write_capability("network.admin"))):
    """Trigger immediate connectivity test — runs full discovery cycle synchronously."""
    graph = run_network_discovery_cycle()
    return {"ok": True, "connectivity": graph.get("connectivity", {}), "tunnel": graph.get("tunnel", {})}


@app.get("/network/changes")
def network_changes(limit: int = Query(50, ge=1, le=500), _: str = Depends(require_bridge_token)):
    """Last N network change events detected vs the prior graph snapshot."""
    prior = load_prior()
    current = load_graph()
    if not prior:
        return {"changes": [], "total": 0}
    changes = detect_changes(prior, current)
    changes.sort(key=lambda c: c.get("at", ""), reverse=True)
    return {"changes": changes[:limit], "total": len(changes)}


@app.post("/network/discover")
def network_discover(_: str = Depends(_require_write_capability("network.admin"))):
    """Trigger immediate full network discovery — tailscale + proxmox + topology + connectivity."""
    graph = run_network_discovery_cycle()
    return {"ok": True, "graph": graph}


# ── JARVIS P6: Voice surface (STT/TTS router, local-first) ─────────────────
from fastapi import File as _File, UploadFile as _UploadFile
from fastapi.responses import Response as _Response


@app.post("/kai/voice/transcribe")
async def kai_voice_transcribe(file: _UploadFile = _File(...)):
    """Audio → text via the voice router (local-first provider selection)."""
    from core.voice_router import transcribe
    data = await file.read()
    result = transcribe(data, filename=file.filename or "audio.wav")
    if not result.get("ok"):
        raise HTTPException(status_code=503, detail=result.get("error", "voice unavailable"))
    return result


class _SpeakBody(BaseModel):
    text: str


@app.post("/kai/voice/speak")
def kai_voice_speak(body: _SpeakBody):
    """Text → WAV audio via the voice router."""
    from core.voice_router import speak
    result = speak(body.text)
    if not result.get("ok"):
        raise HTTPException(status_code=503, detail=result.get("error", "voice unavailable"))
    return _Response(content=result["audio"], media_type="audio/wav")


class _VoiceChatBody(BaseModel):
    # full voice round: transcribe → chat → speak; returns text + audio
    audio: str  # base64-encoded wav


@app.post("/kai/voice/chat")
async def kai_voice_chat(body: _VoiceChatBody):
    import base64 as _b64
    from core.voice_router import transcribe, speak
    try:
        audio = _b64.b64decode(body.audio)
    except Exception:
        raise HTTPException(400, "audio must be base64 wav")
    tr = transcribe(audio)
    if not tr.get("ok"):
        raise HTTPException(503, tr.get("error", "transcribe unavailable"))
    text = tr.get("text", "")
    if not text:
        return {"transcript": "", "response": "", "audio": None}
    reply = handle_kai_chat(text, operator="voice")
    response_text = str(reply.get("response") or reply.get("result") or "")
    sp = speak(response_text[:1000])
    return {
        "transcript": text,
        "stt_provider": tr.get("provider"),
        "response": response_text,
        "audio_base64": (_b64.b64encode(sp["audio"]).decode() if sp.get("ok") else None),
        "tts_ok": sp.get("ok", False),
    }


@app.post("/kai/chat")
def kai_chat_endpoint(
    body: KaiChatRequest,
    operator: str = Depends(_require_write_capability("kai.chat.send")),
):
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Message text is required")

    try:
        return handle_kai_chat(text, operator)
    except KaiChatAllProvidersFailed as error:
        raise HTTPException(status_code=502, detail=str(error))
    except InvalidTransition as error:
        raise HTTPException(status_code=409, detail=str(error))


# Streaming chat endpoint for 15F
@app.post("/kai/chat/stream")
async def kai_chat_stream_endpoint(
    body: KaiChatRequest,
    operator: str = Depends(_require_write_capability("kai.chat.send")),
):
    """
    15F: POST /kai/chat/stream - Stream responses using Server-Sent Events (SSE).
    This extends the existing POST /kai/chat by enabling real-time streaming of responses.
    """
    import asyncio
    import json
    from starlette.responses import EventSourceResponse
    from typing import AsyncGenerator
    from core.kai.conversation import create_conversation, create_message, get_messages, update_conversation_title, get_conversation
    
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Message text is required")

    # Use existing conversation or create a new one
    if body.conversation_id:
        existing = get_conversation(body.conversation_id)
        if not existing or existing.get("user_id") != operator:
            raise HTTPException(status_code=404, detail="Conversation not found")
        conversation_id = body.conversation_id
    else:
        conversation_id = create_conversation(operator, body.project_id)
    
    # Save user message
    create_message(conversation_id, "user", text)
    
    # Update conversation title from first message
    if len(get_messages(conversation_id)) <= 1:
        title = text[:50] + "..." if len(text) > 50 else text
        if not title.strip():
            title = "New Conversation"
        update_conversation_title(conversation_id, title)

    # Create generator for streaming response
    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            # First yield the conversation ID
            yield f"data: {{\"event\": \"conversation_started\", \"conversation_id\": \"{conversation_id}\"}}\n\n"
            
            # Process the chat message like the original
            history = get_messages(conversation_id)
            # Convert message list to format expected by ai_chat
            formatted_history = [
                {"role": msg["role"], "content": msg["content"]}
                for msg in history if msg["role"] != "system"  # Exclude system messages
            ]
            
            # Extract approval intent if there is one
            approval_intent = None
            approve_match = _APPROVE_INTENT_RE.match(text)
            reject_match = _REJECT_INTENT_RE.match(text)
            
            if approve_match or reject_match:
                match = approve_match or reject_match
                scope = match.group(1).lower()
                request_id_hint = match.group(2) if match.lastindex and match.lastindex >= 2 else None
                request, disambiguation = _resolve_approval_request(scope, request_id_hint)
                
                if request is not None:
                    approval_intent = {
                        "intent_type": "approval",
                        "scope": scope,
                        "request_id": request["id"]
                    }
            
            # Send progress updates
            yield f"data: {{\"event\": \"processing\", \"message\": \"Analyzing request...\"}}\n\n"
            
            # Simulate some processing delay to show streaming
            yield f"data: {{\"event\": \"processing\", \"message\": \"Generating response...\"}}\n\n"
            
            # Get the response from the AI (simplified for now)
            signals = gather_signals()
            response_text = ai_chat(formatted_history, signals)
            
            # Send the response in chunks
            chunk_size = 20  # Characters per chunk
            for i in range(0, len(response_text), chunk_size):
                chunk = response_text[i:i+chunk_size]
                yield f"data: {{\"event\": \"response_chunk\", \"chunk\": \"{chunk}\"}}\n\n"
                await asyncio.sleep(0.01)  # Small delay to simulate streaming
            
            # Final indicator
            yield f"data: {{\"event\": \"response_complete\", \"approval_intent\": {json.dumps(approval_intent)}}}\n\n"
            
        except Exception as e:
            yield f"data: {{\"event\": \"error\", \"message\": \"Stream error: {str(e)}\"}}\n\n"
            return
    
    # Return the streaming response
    return EventSourceResponse(event_generator())


@app.get("/kai/conversations")
def kai_list_conversations(
    project_id: str | None = None,
    operator: str = Depends(_require_write_capability("kai.chat.send")),
):
    """15F: GET /kai/conversations - List conversations for the authenticated user."""
    from core.kai.conversation import get_conversations
    
    conversations = get_conversations(operator, project_id)
    return {"conversations": conversations}


@app.get("/kai/conversations/{conversation_id}")
def kai_get_conversation(
    conversation_id: str,
    operator: str = Depends(_require_write_capability("kai.chat.send")),
):
    """15F: GET /kai/conversations/{conversation_id} - Get conversation messages."""
    from core.kai.conversation import get_messages, get_conversation
    
    conversation = get_conversation(conversation_id)
    if not conversation or conversation.get("user_id") != operator:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    messages = get_messages(conversation_id)
    return {"conversation": conversation, "messages": messages}


@app.delete("/kai/conversations/{conversation_id}")
def kai_delete_conversation(
    conversation_id: str,
    operator: str = Depends(_require_write_capability("kai.chat.send")),
):
    """15F: DELETE /kai/conversations/{conversation_id} - Delete a conversation."""
    from core.kai.conversation import delete_conversation
    
    conversation = delete_conversation(conversation_id)
    return {"deleted": True}


@app.get("/roadmap")
def roadmap_endpoint():
    return load_roadmap()


@app.get("/roadmap/next")
def roadmap_next_endpoint():
    return get_next_phase() or {}


@app.get("/roadmap/remaining")
def roadmap_remaining_endpoint():
    return get_remaining_work()


@app.get("/roadmap/progress")
def roadmap_progress_endpoint():
    return get_progress_summary()


class PhaseStatusUpdate(BaseModel):
    status: str


@app.get("/roadmap/autonomous/status")
def roadmap_autonomous_status_endpoint():
    return {"enabled": is_autonomous_mode_enabled()}


# ── 13H: 6-level autonomy control ─────────────────────────────────────────
# GET is read-only and matches the ungated policy of /kai/identity (the
# level shows up there too, for the plugin's Overview tab). PUT is a
# real write and unambiguously requires the bridge token, same as the
# deprecated enable/disable endpoints below (which are now shims around
# set_autonomy_level).

class AutonomyLevelUpdate(BaseModel):
    level: int


@app.get("/api/autonomy")
def autonomy_get_endpoint():
    return get_autonomy_level()


@app.put("/api/autonomy/level")
def autonomy_set_level_endpoint(
    body: AutonomyLevelUpdate,
    operator: str = Depends(_require_write_capability("autonomy.configure")),
):
    try:
        return set_autonomy_level(body.level, operator)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


@app.post("/roadmap/autonomous/enable")
def roadmap_autonomous_enable_endpoint(operator: str = Depends(_require_write_capability("roadmap.autonomy"))):
    # Deprecated: preserved as a wrapper that maps the old binary
    # "enable" call to Level 4 (the exact pre-13H "enabled: true"
    # semantics). New callers should use PUT /api/autonomy/level.
    enable_autonomous_mode(operator)
    return {"enabled": True}


@app.post("/roadmap/autonomous/disable")
def roadmap_autonomous_disable_endpoint(operator: str = Depends(_require_write_capability("roadmap.autonomy"))):
    # Deprecated: preserved as a wrapper that maps the old binary
    # "disable" call to Level 1 (observe + report), NOT Level 0.
    # Level 0 (fully manual) must be selected explicitly through the
    # level API -- see core/autonomy.py's disable_autonomous_mode
    # docstring for why "disabled" pre-13H is not the same idea as
    # Level 0's "nothing automatic at all".
    disable_autonomous_mode(operator)
    return {"enabled": False}


class NewPhaseRequest(BaseModel):
    id: str
    name: str
    description: str
    dependencies: list[str] = []
    priority: int
    status: str = "proposed"


@app.post("/roadmap/phases")
def add_roadmap_phase_endpoint(
    body: NewPhaseRequest,
    operator: str = Depends(_require_write_capability("roadmap.create")),
):
    try:
        return add_phase(
            id=body.id, name=body.name, description=body.description,
            dependencies=body.dependencies, priority=body.priority, status=body.status,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


@app.post("/roadmap/{phase_id}/status")
def roadmap_status_endpoint(
    phase_id: str,
    body: PhaseStatusUpdate,
    operator: str = Depends(_require_write_capability("roadmap.modify")),
):
    if get_phase(phase_id) is None:
        raise HTTPException(status_code=404, detail="Phase not found")

    try:
        return mark_phase_status(phase_id, body.status)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


@app.get("/templates")
def templates_endpoint():
    return {name: {"label": t["label"]} for name, t in TEMPLATES.items()}


@app.post("/builds")
def create_build_endpoint(
    body: CreateBuildRequest,
    operator: str = Depends(_require_write_capability("builds.create")),
):
    try:
        return create_build(body.name, body.description, body.project_path, template=body.template)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


@app.get("/builds")
def builds_endpoint():
    return list_builds()


@app.get("/builds/{build_id}")
def build_endpoint(build_id: str):
    result = get_build(build_id, include_terminal=True)

    if result is None:
        raise HTTPException(status_code=404, detail="Build not found")

    return result


@app.post("/builds/{build_id}/answer")
def answer_build_endpoint(
    build_id: str,
    body: AnswerAction,
    operator: str = Depends(_require_write_capability("builds.answer")),
):
    try:
        result = submit_answer(build_id, body.answer)
    except InvalidTransition as error:
        raise HTTPException(status_code=409, detail=str(error))

    if result is None:
        raise HTTPException(status_code=404, detail="Build not found")

    return result


@app.post("/builds/{build_id}/approve-architecture")
def approve_architecture_endpoint(
    build_id: str,
    action: ApprovalAction = ApprovalAction(),
    operator: str = Depends(_require_write_capability("builds.approve_architecture")),
):
    from core.build_manager import get_build
    build = get_build(build_id)

    if build and build.get("risk") == "security-critical":
        from core import authz
        if not authz.is_bridge_token_operator(operator):
            role = authz.resolve_role(operator)
            if role != "operator":
                raise HTTPException(status_code=403, detail="Security-critical build approvals require operator role")

    try:
        result = approve_architecture(build_id, operator=operator, note=action.note)
    except InvalidTransition as error:
        raise HTTPException(status_code=409, detail=str(error))
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error))

    if result is None:
        raise HTTPException(status_code=404, detail="Build not found")

    return result


@app.post("/builds/{build_id}/generate")
def generate_build_endpoint(
    build_id: str,
    operator: str = Depends(_require_write_capability("builds.generate")),
):
    try:
        result = start_generation(build_id)
    except InvalidTransition as error:
        raise HTTPException(status_code=409, detail=str(error))

    if result is None:
        raise HTTPException(status_code=404, detail="Build not found")

    return result


@app.post("/builds/{build_id}/approve-deploy")
def approve_deploy_endpoint(
    build_id: str,
    action: ApprovalAction = ApprovalAction(),
    operator: str = Depends(_require_write_capability("builds.approve_deploy")),
):
    from core.build_manager import get_build
    build = get_build(build_id)

    if build and build.get("risk") == "security-critical":
        from core import authz
        if not authz.is_bridge_token_operator(operator):
            role = authz.resolve_role(operator)
            if role != "operator":
                raise HTTPException(status_code=403, detail="Security-critical build approvals require operator role")

    try:
        result = approve_deploy(build_id, operator=operator, note=action.note)
    except InvalidTransition as error:
        raise HTTPException(status_code=409, detail=str(error))
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error))

    if result is None:
        raise HTTPException(status_code=404, detail="Build not found")

    return result


@app.post("/builds/{build_id}/rollback")
def rollback_build_endpoint(
    build_id: str,
    operator: str = Depends(_require_write_capability("builds.rollback")),
):
    try:
        result = rollback_deployment(build_id)
    except InvalidTransition as error:
        raise HTTPException(status_code=409, detail=str(error))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))

    if result is None:
        raise HTTPException(status_code=404, detail="Build not found")

    return result


# ── Law library documents (Kai Dashboard upload, 2026-07-31) ────────────────
# Operator-facing upload of legal textbooks/notes/case judgments that feed
# the Law Tutor bot's context. Admin-only (require_bridge_token), same as
# every other write endpoint in this file -- distinct from the Law Tutor
# bot's own Telegram surface, which has no upload capability of its own.

@app.post("/kai/law-documents")
async def upload_law_document_endpoint(
    file: UploadFile = File(...),
    category: str | None = Form(default=None),
    jurisdiction: str | None = Form(default=None),
    operator: str = Depends(_require_write_capability("law.manage")),
):
    content = await file.read()
    try:
        record = save_document(
            file.filename or "untitled", content,
            category=category, jurisdiction=jurisdiction, uploaded_by=operator,
        )
    except DocumentTooLarge as error:
        raise HTTPException(status_code=413, detail=str(error))
    except UnsupportedFileType as error:
        raise HTTPException(status_code=400, detail=str(error))

    return record


@app.get("/kai/law-documents")
def list_law_documents_endpoint():
    return {"documents": list_documents()}


@app.delete("/kai/law-documents/{doc_id}")
def delete_law_document_endpoint(doc_id: str, operator: str = Depends(_require_write_capability("law.manage"))):
    existed = delete_document(doc_id)
    if not existed:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"ok": True}


# ── SSE Event Streaming for Real-Time Dashboard Updates ─────────────────────────────

# Global variables for SSE connections



# ---- TK-b7614289: Emergency Controls API ----

# Scheduler pause file (same file checked by core/scheduler.py)
SCHEDULER_PAUSE_FILE = Path(os.environ.get(
    "SCHEDULER_PAUSE_FILE",
    "/project/ai-orchestrator/memory/scheduler_paused.json",
))


def _get_pause_state() -> dict:
    """Return current scheduler pause state."""
    if SCHEDULER_PAUSE_FILE.exists():
        try:
            data = json.loads(SCHEDULER_PAUSE_FILE.read_text())
            return {
                "paused": data.get("paused", True),
                "paused_at": data.get("paused_at", "unknown"),
                "reason": data.get("reason", ""),
                "paused_by": data.get("paused_by", "unknown"),
            }
        except Exception:
            pass
    return {"paused": False, "paused_at": None, "reason": "", "paused_by": None}


def _set_pause_state(paused: bool, reason: str = "", operator: str = "dashboard"):
    """Set scheduler pause state. Creates or removes the pause file."""
    if paused:
        data = {
            "paused": True,
            "paused_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "paused_by": operator,
        }
        SCHEDULER_PAUSE_FILE.parent.mkdir(parents=True, exist_ok=True)
        SCHEDULER_PAUSE_FILE.write_text(json.dumps(data, indent=2))
    else:
        if SCHEDULER_PAUSE_FILE.exists():
            SCHEDULER_PAUSE_FILE.unlink()


class AdminAction(BaseModel):
    """Request body for admin actions that need extra params."""
    reason: str = ""
    build_id: str = ""
    provider_name: str = ""
    target_status: str = ""


@app.get("/api/admin/status")
def api_admin_status():
    """TK-b7614289: Return scheduler pause state and provider health."""
    scheduler = _get_pause_state()

    providers = []
    try:
        from core.ai.provider_health import load_provider_health
        health = load_provider_health()
        for name, h in health.items():
            providers.append({
                "name": name,
                "healthy": h.get("healthy", True),
                "quota_remaining": h.get("quota_remaining"),
                "last_error": h.get("last_error", ""),
            })
    except Exception:
        pass

    return {
        "scheduler": scheduler,
        "providers": providers,
    }


@app.post("/api/admin/pause-scheduler")
def api_pause_scheduler(
    body: AdminAction,
    operator: str = Depends(_require_write_capability("delegate.use")),
):
    """TK-b7614289: Pause the scheduler loop. Safe — current cycle completes."""
    _set_pause_state(True, reason=body.reason, operator="dashboard")
    return {"ok": True, "scheduler": _get_pause_state()}


@app.post("/api/admin/resume-scheduler")
def api_resume_scheduler(
    operator: str = Depends(_require_write_capability("delegate.use")),
):
    """TK-b7614289: Resume the scheduler loop."""
    _set_pause_state(False)
    return {"ok": True, "scheduler": _get_pause_state()}


@app.post("/api/admin/retry-build")
def api_retry_build(
    body: AdminAction,
    operator: str = Depends(_require_write_capability("delegate.use")),
):
    """TK-b7614289: Reset a failed build to PENDING for retry."""
    if not body.build_id:
        raise HTTPException(400, "build_id is required")

    from core.build_manager import load_builds, save_builds

    builds = load_builds()
    for b in builds:
        if b.get("id") == body.build_id:
            old_status = b.get("status", "?")
            b["status"] = "PENDING"
            b["failure_reason"] = None
            b["_retried_at"] = datetime.now(timezone.utc).isoformat()
            b["_retry_reason"] = body.reason or "manual retry via dashboard"
            save_builds(builds)
            logging.getLogger(__name__).info(
                f"build {body.build_id} retried: {old_status} → PENDING"
            )
            return {"ok": True, "build_id": body.build_id, "old_status": old_status, "new_status": "PENDING"}

    raise HTTPException(404, f"Build {body.build_id} not found")


@app.post("/api/admin/cancel-build")
def api_cancel_build(
    body: AdminAction,
    operator: str = Depends(_require_write_capability("delegate.use")),
):
    """TK-b7614289: Cancel an active build (mark as FAILED)."""
    if not body.build_id:
        raise HTTPException(400, "build_id is required")

    from core.build_manager import load_builds, save_builds

    builds = load_builds()
    for b in builds:
        if b.get("id") == body.build_id:
            old_status = b.get("status", "?")
            if old_status in ("COMPLETED", "FAILED", "ROLLED_BACK"):
                raise HTTPException(400, f"Build {body.build_id} is already terminal ({old_status})")
            b["status"] = "FAILED"
            b["failure_reason"] = body.reason or "cancelled via dashboard"
            b["_cancelled_at"] = datetime.now(timezone.utc).isoformat()
            save_builds(builds)
            logging.getLogger(__name__).info(
                f"build {body.build_id} cancelled: {old_status} → FAILED"
            )
            return {"ok": True, "build_id": body.build_id, "old_status": old_status, "new_status": "FAILED"}

    raise HTTPException(404, f"Build {body.build_id} not found")


@app.post("/api/admin/disable-provider")
def api_disable_provider(
    body: AdminAction,
    operator: str = Depends(_require_write_capability("delegate.use")),
):
    """TK-b7614289: Temporarily disable an AI provider."""
    if not body.provider_name:
        raise HTTPException(400, "provider_name is required")

    from core.ai.provider_health import load_provider_health, save_provider_health

    health = load_provider_health()
    if body.provider_name not in health:
        raise HTTPException(404, f"Provider '{body.provider_name}' not found in health tracking")

    health[body.provider_name]["healthy"] = False
    health[body.provider_name]["disabled_reason"] = body.reason or "disabled via dashboard"
    health[body.provider_name]["disabled_at"] = datetime.now(timezone.utc).isoformat()
    save_provider_health(health)

    logging.getLogger(__name__).warning(
        f"provider {body.provider_name} disabled via dashboard: {body.reason or 'no reason given'}"
    )
    return {"ok": True, "provider": body.provider_name, "healthy": False}


@app.post("/api/admin/enable-provider")
def api_enable_provider(
    body: AdminAction,
    operator: str = Depends(_require_write_capability("delegate.use")),
):
    """TK-b7614289: Re-enable a disabled AI provider."""
    if not body.provider_name:
        raise HTTPException(400, "provider_name is required")

    from core.ai.provider_health import load_provider_health, save_provider_health

    health = load_provider_health()
    if body.provider_name not in health:
        raise HTTPException(404, f"Provider '{body.provider_name}' not found in health tracking")

    health[body.provider_name]["healthy"] = True
    health[body.provider_name].pop("disabled_reason", None)
    health[body.provider_name].pop("disabled_at", None)
    save_provider_health(health)

    logging.getLogger(__name__).info(f"provider {body.provider_name} re-enabled via dashboard")
    return {"ok": True, "provider": body.provider_name, "healthy": True}


# Simple SSE endpoint for dashboard updates
@app.get("/events")
async def sse_endpoint(request: Request):
    """SSE endpoint that streams real-time dashboard updates to connected clients"""
    
    # Generate a unique connection ID
    connection_id = secrets.token_urlsafe(16)
    
    # Add connection to tracking set
    with _sse_connections_lock:
        _sse_connections.add(connection_id)
    
    try:
        # Send initial connection confirmation
        async def event_generator() -> AsyncGenerator[str, None]:
            # Send initial data to establish connection
            yield f"data: {json.dumps({'type': 'connection_established', 'connection_id': connection_id})}\n\n"
            
            # Keep connection alive with heartbeat
            while True:
                yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
                await asyncio.sleep(30)  # Heartbeat every 30 seconds
        
        # Use StreamingResponse as fallback
        return StreamingResponse(event_generator(), media_type="text/event-stream")
        
    except Exception as e:
        # Remove connection on error
        with _sse_connections_lock:
            if connection_id in _sse_connections:
                _sse_connections.remove(connection_id)
        raise HTTPException(status_code=500, detail=f"Failed to establish SSE connection: {str(e)}")
    
    finally:
        # Remove connection when client disconnects
        with _sse_connections_lock:
            if connection_id in _sse_connections:
                _sse_connections.remove(connection_id)
