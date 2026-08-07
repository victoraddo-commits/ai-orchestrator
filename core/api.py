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
from fastapi import FastAPI, HTTPException, Header, Depends, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse
import httpx
from pydantic import BaseModel
from starlette.responses import HTMLResponse, RedirectResponse
import logging

from core.memory import save, load

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
from core.ai_provider import list_providers, set_provider_enabled, deregister_provider
from core.ai.agent_registry import (
    list_agents, get_agent, register_agent, enable_agent, disable_agent,
    test_agent, get_agent_stats, get_cost_history, get_performance_history,
    record_benchmark, bootstrap_default_agents,
)
from core.ai.ai_router import delegate, get_provider_dashboard, get_worker_details, AllProvidersFailed, chat as ai_chat, remove_provider_from_roles
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


_MODULE_REGISTRY: dict[str, dict] = {}

from core.klaus.api_endpoints import klaus_router as klaus_api_router
from core.klaus.scheduler import start_scheduler as start_klaus_scheduler


app = FastAPI(title="AI Orchestrator Observability API")

app.include_router(klaus_api_router)

# AI Gateway — OpenAI-compatible /v1 endpoints for external consumers
from core.ai_gateway.gateway import router as gateway_router
app.include_router(gateway_router)

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


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return _DASHBOARD_HTML


@app.get("/")
def root_redirect():
    return RedirectResponse(url="/dashboard")


API_TOKEN_PATH = Path(
    os.environ.get("AI_ORCHESTRATOR_API_TOKEN_PATH", str(Path.home() / ".ai-orchestrator" / "api_token"))
)

BRIDGE_OPERATOR = "cloudcli-plugin"


def _load_api_token():
    """Shared secret between core/api.py and the trusted caller (the CloudCLI
    plugin's server-side bridge, the only thing that should ever call the
    write endpoints below). Generated on first use; never derived from or
    trusted from client-supplied request data."""

    if not API_TOKEN_PATH.exists():
        API_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        API_TOKEN_PATH.parent.chmod(0o700)  # mkdir's mode is umask-affected; force it

        # Create with the final 0600 mode from the very first syscall -- no
        # window where the file exists with looser (e.g. default 0644)
        # permissions. O_EXCL also means this raises rather than silently
        # overwriting if another process won the race to create it first --
        # in that case just fall through and read what it wrote.
        try:
            fd = os.open(API_TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            try:
                os.write(fd, secrets.token_urlsafe(32).encode())
            finally:
                os.close(fd)

    return API_TOKEN_PATH.read_text().strip()


_load_api_token()  # ensure the token file exists as soon as the API starts,
# not lazily on the first write request -- the plugin bridge needs to be
# able to read it before it ever makes that first call.


def require_bridge_token(authorization: str | None = Header(default=None)) -> str:
    """Verifies the caller presented the shared secret and returns the
    identity to record as the operator -- this is the ONLY source of
    operator identity for write endpoints; it is never read from the
    request body, so a caller cannot forge who performed an action."""

    expected = f"Bearer {_load_api_token()}"
    presented = authorization or ""

    if not hmac.compare_digest(presented.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Missing or invalid API token")

    return BRIDGE_OPERATOR


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
DEFAULT_DASHBOARD_PASSWORD = "Kai-Enzo"


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


@app.post("/auth/login")
def auth_login(body: LoginRequest):
    """Authenticate with username/password, return a session token.
    The caller receives a Bearer-style token scoped to their role."""
    token = authz.authenticate(body.username, body.password)
    if token is None:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return {"token": token, "token_type": "session"}


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


def _discover_modules():
    """Scan config/modules/ for *.json descriptors and populate registry."""
    import glob as _glob
    modules_dir = Path(__file__).resolve().parent / "config" / "modules"
    if not modules_dir.is_dir():
        return
    for desc_path in _glob.glob(str(modules_dir / "*.json")):
        try:
            desc = _sse_json.loads(Path(desc_path).read_text())
            name = desc.get("name", Path(desc_path).stem)
            _MODULE_REGISTRY[name] = {
                "name": name,
                "description": desc.get("description", ""),
                "endpoints": desc.get("endpoints", []),
                "capabilities": desc.get("capabilities", []),
                "dependencies": desc.get("dependencies", []),
                "version": desc.get("version", "0.1.0"),
                "registered_at": _datetime.now(_timezone.utc).isoformat(),
            }
        except Exception:
            pass


_discover_modules()


@app.get("/api/modules")
def modules_endpoint():
    """Return auto-discovered modules registered with Kai Command Center."""
    return {"modules": list(_MODULE_REGISTRY.values())}


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


# ---- V3: GPU & Pipeline endpoints ----

@app.get("/api/gpu/status")
def api_gpu_status():
    """Per-pod GPU metrics: state, runtime, cost, tasks, health."""
    try:
        import core.gpu_lifecycle as _gl
        return _gl.get_gpu_dashboard()
    except Exception as e:
        return {"error": str(e)}


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
    """Cost tracking: GPU spend, per-pod breakdown."""
    try:
        import core.gpu_lifecycle as _gl
        metrics = _gl.get_gpu_dashboard()
        return {
            "gpu": metrics,
            "summary": {
                "total_gpu_cost": metrics.get("summary", {}).get("total_cost", 0),
                "combined_hourly": metrics.get("summary", {}).get("combined_hourly_cost", 0),
            },
        }
    except Exception as e:
        return {"error": str(e)}

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


@app.get("/api/modules")
def modules_endpoint():
    return {"modules": get_registered_modules()}


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


@app.get("/audit")
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
    from core.kai.conversation import create_conversation, create_message, get_messages, update_conversation_title
    
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Message text is required")

    # Create or get conversation ID
    conversation_id = None
    # Try to get conversation_id from request (if available) or create new
    # For now, we'll create a new conversation for simplicity
    conversation_id = create_conversation(operator, None)
    
    # Save user message
    create_message(conversation_id, "user", text)
    
    # Update conversation title from first message
    if len(get_messages(conversation_id)) <= 1:
        # Generate title from first message content (truncate to 50 chars)
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
    result = get_build(build_id)

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
def api_pause_scheduler(body: AdminAction):
    """TK-b7614289: Pause the scheduler loop. Safe — current cycle completes."""
    _set_pause_state(True, reason=body.reason, operator="dashboard")
    return {"ok": True, "scheduler": _get_pause_state()}


@app.post("/api/admin/resume-scheduler")
def api_resume_scheduler():
    """TK-b7614289: Resume the scheduler loop."""
    _set_pause_state(False)
    return {"ok": True, "scheduler": _get_pause_state()}


@app.post("/api/admin/retry-build")
def api_retry_build(body: AdminAction):
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
def api_cancel_build(body: AdminAction):
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
def api_disable_provider(body: AdminAction):
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
def api_enable_provider(body: AdminAction):
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
