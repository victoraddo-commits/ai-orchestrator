import base64
import hmac
import json
import os
import re
import secrets
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Header, Depends, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse
import httpx
from pydantic import BaseModel
from starlette.responses import HTMLResponse, RedirectResponse

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
)
from core.project_templates import TEMPLATES
from core.build_learning import summarize_templates, get_build_history, summarize_lessons
from core.ai_provider import list_providers
from core.ai.ai_router import delegate, get_provider_dashboard, AllProvidersFailed, chat as ai_chat
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


app = FastAPI(title="AI Orchestrator Observability API")


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
    operator: str = Depends(require_bridge_token),
):

    try:
        result = approve(request_id, note=action.note, operator=operator)
    except InvalidTransition as error:
        raise HTTPException(status_code=409, detail=str(error))

    if result is None:
        raise HTTPException(status_code=404, detail="Approval request not found")

    return result


@app.post("/approvals/{request_id}/reject")
def reject_request(
    request_id: str,
    action: ApprovalAction = ApprovalAction(),
    operator: str = Depends(require_bridge_token),
):

    try:
        result = reject(request_id, note=action.note, operator=operator)
    except InvalidTransition as error:
        raise HTTPException(status_code=409, detail=str(error))

    if result is None:
        raise HTTPException(status_code=404, detail="Approval request not found")

    return result


@app.get("/providers")
def providers_endpoint():
    return list_providers()


@app.get("/providers/dashboard")
def providers_dashboard_endpoint():
    return get_provider_dashboard()


class DelegateRequest(BaseModel):
    description: str
    task_type: str | None = None
    project_path: str | None = None


@app.post("/delegate")
def delegate_endpoint(
    body: DelegateRequest,
    operator: str = Depends(require_bridge_token),
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
    operator: str = Depends(require_bridge_token),
):
    return kai_dispatch(body.text)


@app.get("/kai/identity")
def kai_identity_endpoint():
    """Assembles Kai's identity/mission/capabilities/restrictions statements
    (core/kai/identity.py, mission.py, goals.py, policies.py -- each already
    covered by tests/test_kai_identity.py individually) plus the live
    autonomous-mode flag into the single object the CloudCLI plugin's Kai
    Control Center identity card (13G) renders. Read-only and ungated, same
    as the sibling /learning and /roadmap/progress endpoints it sits next to
    on that tab -- nothing here is sensitive beyond what those already
    expose."""
    return {
        "name": "Kai",
        "identity": kai_identity.get_identity(),
        "mission": kai_mission.get_mission(),
        "capabilities": kai_goals.get_capabilities(),
        "restrictions": kai_policies.get_restrictions(),
        "autonomous_mode": is_autonomous_mode_enabled(),
    }


@app.get("/kai/proposals")
def kai_proposals_endpoint():
    """13C improvement proposals (core/kai/planner.py), for the Kai Control
    Center's proposals card. Read-only, ungated like /approvals and
    /learning -- proposals are advisory text, not a write surface."""
    return list_proposals()


CHAT_HISTORY_FILE = "kai_chat_history.json"
CHAT_HISTORY_MAX_MESSAGES = 40

_APPROVE_INTENT_RE = re.compile(
    r"approve\s+(architecture|deploy)\s*(?:plan)?\s*(?:#?(request-?\d*|[a-f0-9-]{8,}))?\s*\.?$",
    re.I,
)

_REJECT_INTENT_RE = re.compile(
    r"reject\s+(architecture|deploy)\s*(?:plan)?\s*(?:#?(request-?\d*|[a-f0-9-]{8,}))?\s*\.?$",
    re.I,
)


def _chat_records(data):
    """Normalizes whatever is on disk into the message list.

    core.memory_manager already supplies the one {schema_version, records}
    envelope every memory file uses, so load() hands us the bare list. Files
    written before that was true carry a second, redundant envelope inside
    records; unwrap it so no operator loses their transcript to the fix."""

    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return _chat_records(data.get("records", []))
    return []


def _load_chat_history():
    return _chat_records(load(CHAT_HISTORY_FILE))


def _save_chat_history(history):
    # mutate receives (and must return) the bare records list -- memory_manager
    # adds the schema_version envelope on write.
    update(CHAT_HISTORY_FILE, lambda data: list(history))


def _trim_history(history):
    return history[-CHAT_HISTORY_MAX_MESSAGES:]


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
def kai_chat_history_endpoint(operator: str = Depends(require_bridge_token)):
    """Phase 17B: read-only conversation history for the CloudCLI plugin's
    chat panel. Bridge-token gated like POST /kai/chat -- the history can
    contain operational detail (approvals, failures, roadmap state) that
    must not be readable by anything that doesn't hold the shared secret.
    Returns exactly what POST /kai/chat persists, so the panel's display
    always matches memory/kai_chat_history.json."""
    return _load_chat_history()


@app.post("/kai/chat")
def kai_chat_endpoint(
    body: KaiChatRequest,
    operator: str = Depends(require_bridge_token),
):
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Message text is required")

    history = _load_chat_history()
    history.append({"role": "user", "content": text})

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
                try:
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
                except InvalidTransition as error:
                    raise HTTPException(status_code=409, detail=str(error))
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
                raise HTTPException(status_code=502, detail=str(error))
            reply = {"matched": False, "response": response_text}

    history.append({"role": "assistant", "content": _reply_content(reply)})
    _save_chat_history(_trim_history(history))

    return reply


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


@app.post("/roadmap/autonomous/enable")
def roadmap_autonomous_enable_endpoint(operator: str = Depends(require_bridge_token)):
    enable_autonomous_mode()
    return {"enabled": True}


@app.post("/roadmap/autonomous/disable")
def roadmap_autonomous_disable_endpoint(operator: str = Depends(require_bridge_token)):
    disable_autonomous_mode()
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
    operator: str = Depends(require_bridge_token),
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
    operator: str = Depends(require_bridge_token),
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
    operator: str = Depends(require_bridge_token),
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
    operator: str = Depends(require_bridge_token),
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
    operator: str = Depends(require_bridge_token),
):
    try:
        result = approve_architecture(build_id, operator=operator, note=action.note)
    except InvalidTransition as error:
        raise HTTPException(status_code=409, detail=str(error))

    if result is None:
        raise HTTPException(status_code=404, detail="Build not found")

    return result


@app.post("/builds/{build_id}/generate")
def generate_build_endpoint(
    build_id: str,
    operator: str = Depends(require_bridge_token),
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
    operator: str = Depends(require_bridge_token),
):
    try:
        result = approve_deploy(build_id, operator=operator, note=action.note)
    except InvalidTransition as error:
        raise HTTPException(status_code=409, detail=str(error))

    if result is None:
        raise HTTPException(status_code=404, detail="Build not found")

    return result


@app.post("/builds/{build_id}/rollback")
def rollback_build_endpoint(
    build_id: str,
    operator: str = Depends(require_bridge_token),
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
    operator: str = Depends(require_bridge_token),
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
def list_law_documents_endpoint(operator: str = Depends(require_bridge_token)):
    return {"documents": list_documents()}


@app.delete("/kai/law-documents/{doc_id}")
def delete_law_document_endpoint(doc_id: str, operator: str = Depends(require_bridge_token)):
    existed = delete_document(doc_id)
    if not existed:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"ok": True}
