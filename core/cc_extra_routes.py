"""Command Center — extra routes missing on older runner api.py builds.

Provides the two endpoints the Telegram control commands need but that the
runner's api.py lacks (drift): ``/api/network/overview`` and ``/kai/missions``.
Add-only; included by api.py via the same append-anchor as cc_modules.
"""
from __future__ import annotations

import json
import os
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from pydantic import BaseModel

cc_extra_router = APIRouter(tags=["command-center-extra"])


def _req_op(request: Request) -> None:
    """Require an operator: bridge token, a valid CC session, or the identity
    headers injected by the auth proxy. Used to gate write/trigger endpoints.

    The identity headers are honoured only from a trusted peer
    (``core.auth.trusted_proxy``) -- an untrusted client cannot forge them."""
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


def _dispatch_control(command: str, params: dict, *, domain_map: dict | None = None):
    """Dispatch a mutating control action through the Command Bus (build 23A).

    Returns the handler's data on success. A policy refusal (DENY or
    REQUIRE_APPROVAL) is surfaced as HTTP 403 — never silently bypassed.
    Domain errors raised by the handler are mapped back to their original HTTP
    semantics via ``domain_map``; anything else keeps the legacy
    ``{"error": ...}`` 200 shape so existing panels keep rendering.
    """
    from core.command_bus import get_bus
    result = get_bus().dispatch(command, params=params, source="cc_web", user="operator")
    if result.get("status") == "success":
        return result.get("data")
    message = result.get("message", "command failed")
    if result.get("decision") in ("deny", "require_approval"):
        raise HTTPException(status_code=403, detail=message)
    err = result.get("error_type")
    if domain_map and err in domain_map:
        raise HTTPException(status_code=domain_map[err], detail=message)
    return {"error": err or "command_failed", "detail": message}


_MEM = "/opt/ai-orchestrator/memory"


@cc_extra_router.get("/api/network/overview")
def network_overview():
    """Minimal live network overview (sites/peers/tunnels) for the /network cmd."""
    return {
        "sites": [{"id": "site-b", "name": "Proxmox B", "status": "online"}],
        "peers": [],
        "tunnels": [{"name": "cloudflared", "status": "active"},
                    {"name": "reverse-ssh (Proxmox A)", "status": "unknown"}],
        "note": "minimal overview (runner lacked /api/network/overview)",
    }


@cc_extra_router.get("/api/infra/usage")
def infra_usage(refresh: bool = False):
    """Per-host/CT/VM data usage: disk used/total/% and network rx/tx + rates."""
    from core.infra_usage import collect_usage
    try:
        return JSONResponse(content=collect_usage(force=refresh),
                            headers={"Cache-Control": "no-store"})
    except Exception as e:  # noqa: BLE001
        return JSONResponse(
            {"error": f"{type(e).__name__}: {e}", "hosts": [], "mounts": [], "totals": {}},
            status_code=502)


@cc_extra_router.get("/api/wg/status")
def wg_status():
    """Live WireGuard peer status for the Kai exit node (kaidash on CT103),
    proxied through the PVE-A/PVE-B socat bridges."""
    import urllib.request
    _url = os.environ.get("WG_DASH_URL", "http://192.168.1.110:51880/api")
    try:
        with urllib.request.urlopen(_url, timeout=6) as r:
            return JSONResponse(content=json.load(r), headers={"Cache-Control": "no-store"})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"wg dashboard unreachable: {type(e).__name__}: {e}", "ifaces": []},
                            status_code=502)


@cc_extra_router.get("/api/wg/raw")
def wg_raw():
    """Raw wg dump text from the exit node (for diagnostics)."""
    import urllib.request
    _url = os.environ.get("WG_DASH_URL", "http://192.168.1.110:51880/api")
    try:
        with urllib.request.urlopen(_url, timeout=6) as r:
            j = json.load(r)
        return JSONResponse({"raw": _wg_dump_text(j)})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=502)


def _wg_dump_text(j):
    lines = []
    for f in j.get("ifaces", []):
        lines.append(f"interface {f['iface']} pub={f.get('pub','')} port={f.get('port','')} fwmark={f.get('fwmark','')}")
        for pe in f.get("peers", []):
            lines.append(
                f"  peer {pe['pub']} endpoint={pe.get('endpoint','-')} allowed={pe.get('allowed','')} "
                f"since={pe.get('since','never')} rx={pe.get('rx',0)} tx={pe.get('tx',0)}")
    return "\n".join(lines)


# ── Kai Directives (proxy to local :8099 upload service) ────────────────────
# The CC browser doesn't need the directive token: the backend reads the token
# file (same host) and injects it, gated by the operator's CC session.

_DIRECTIVES_BASE = os.environ.get("DIRECTIVES_BASE", "http://127.0.0.1:8099")
_DIRECTIVES_TOKEN_FILE = os.environ.get(
    "KAI_DIRECTIVES_TOKEN_FILE", "/opt/ai-orchestrator/directives/.token")


def _directives_token() -> str:
    try:
        with open(_DIRECTIVES_TOKEN_FILE) as fh:
            return fh.read().strip()
    except Exception:  # noqa: BLE001
        return ""


def _directives_proxy(path: str, method: str = "GET", body: bytes = b"",
                      ctype: str = "application/json") -> Response:
    import urllib.error
    import urllib.request
    token = _directives_token()
    url = _DIRECTIVES_BASE + path
    req = urllib.request.Request(url, data=body or None, method=method)
    req.add_header("X-Directive-Token", token)
    if body:
        req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
        ctype_out = r.headers.get("Content-Type", "application/json")
        return Response(content=raw, media_type=ctype_out)
    except urllib.error.HTTPError as e:
        # Propagate the upstream status (e.g. 409 verification refusal) so the
        # CC can show the honest verdict instead of a generic 502.
        raw = e.read()
        ctype_out = e.headers.get("Content-Type", "application/json")
        return Response(content=raw, media_type=ctype_out, status_code=e.code)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"directives service unreachable: {type(e).__name__}: {e}"},
                            status_code=502)


@cc_extra_router.get("/api/directives/list")
def directives_list():
    """List uploaded directives (proxies kai-directives :8099)."""
    return _directives_proxy("/api/directives")


@cc_extra_router.post("/api/directives/upload")
async def directives_upload(request: Request, _: None = Depends(_req_op)):
    """Upload a directive (raw body; X-Filename header, optional)."""
    body = await request.body()
    fn = request.headers.get("x-filename", "pasted-directive.md")
    import urllib.request
    token = _directives_token()
    url = _DIRECTIVES_BASE + "/api/upload"
    req = urllib.request.Request(url, data=body or None, method="POST")
    req.add_header("X-Directive-Token", token)
    req.add_header("X-Filename", fn)
    req.add_header("Content-Type", "application/octet-stream")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            out = json.load(r)
        return JSONResponse(out)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"directives service unreachable: {type(e).__name__}: {e}"},
                            status_code=502)


@cc_extra_router.get("/api/directives/get/{directive_id}")
def directives_get(directive_id: str):
    """Fetch a single directive's content."""
    import urllib.parse
    return _directives_proxy("/api/directives/" + urllib.parse.quote(directive_id))


@cc_extra_router.post("/api/directives/ack/{directive_id}")
def directives_ack(directive_id: str, _: None = Depends(_req_op)):
    """Ack a directive (mark as read/followed)."""
    import urllib.parse
    return _directives_proxy("/api/ack/" + urllib.parse.quote(directive_id, safe=""), "POST")


@cc_extra_router.get("/kai/directives/health")
def directives_health():
    """Health of the kai-directives upload service."""
    try:
        return _directives_proxy("/health")
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)})


@cc_extra_router.get("/api/directives/completed")
def directives_completed():
    """List directives moved to directives/completed/ (with verdict metadata)."""
    return _directives_proxy("/api/directives/completed")


@cc_extra_router.post("/api/directives/complete/{directive_id}")
async def directives_complete(directive_id: str, request: Request,
                              _: None = Depends(_req_op)):
    """Verify + complete a directive; refusal (409) carries the verdict."""
    import urllib.parse
    body = await request.body() or b"{}"
    return _directives_proxy(
        "/api/directives/" + urllib.parse.quote(directive_id, safe="") + "/complete",
        "POST", body, "application/json")


@cc_extra_router.get("/api/directives/docs/list")
def directives_docs_list():
    """List files under the repo docs/ folder."""
    return _directives_proxy("/api/directives/docs")


@cc_extra_router.get("/api/directives/docs/download/{rel_path:path}")
def directives_docs_download(rel_path: str):
    """Download a file from the repo docs/ folder."""
    import urllib.parse
    return _directives_proxy(
        "/api/directives/docs/" + urllib.parse.quote(rel_path, safe="/"))


# ── Reports (KAI 2.0 Phase 3) — proxies the directives service reports API ──
# Same token-gated backend proxy as directives; the CC session gates the UI.

def _reports_quote(report_id: str) -> str:
    import urllib.parse
    return urllib.parse.quote(report_id, safe="")


@cc_extra_router.get("/api/reports/list")
def reports_list():
    """List discoverable markdown reports (docs/, reports/, directives/)."""
    return _directives_proxy("/api/reports")


@cc_extra_router.get("/api/reports/get/{report_id}")
def reports_get(report_id: str):
    """Fetch one report with its markdown rendered to sanitized HTML."""
    return _directives_proxy("/api/reports/" + _reports_quote(report_id))


@cc_extra_router.get("/api/reports/download/{report_id}")
def reports_download(report_id: str):
    """Download the raw markdown of a report."""
    return _directives_proxy("/api/reports/" + _reports_quote(report_id) + "/download")


# ── Service Directory (proxy to kai-directory :8097, CT114) ─────────────────
DIRECTORY_BASE = os.environ.get("KAI_DIRECTORY_BASE", "http://192.168.1.114:8097")


@cc_extra_router.get("/api/directory/services")
def directory_services(category: str = "", q: str = ""):
    """List catalogued services (proxies kai-directory :8097)."""
    import urllib.parse
    import urllib.request
    qs = urllib.parse.urlencode({"category": category, "q": q})
    try:
        with urllib.request.urlopen(f"{DIRECTORY_BASE}/services?{qs}", timeout=10) as r:
            return JSONResponse(json.loads(r.read()))
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"directory unreachable: {type(e).__name__}: {e}"}, status_code=502)


@cc_extra_router.get("/api/directory/conformance")
def directory_conformance():
    """Directory coverage check (proxies kai-directory :8097)."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"{DIRECTORY_BASE}/conformance", timeout=20) as r:
            return JSONResponse(json.loads(r.read()))
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"directory unreachable: {type(e).__name__}: {e}"}, status_code=502)


@cc_extra_router.get("/kai/missions")
def kai_missions():
    try:
        with open(os.path.join(_MEM, "missions.json")) as fh:
            data = json.load(fh)
        recs = data.get("records", data) if isinstance(data, dict) else data
        if isinstance(recs, dict):
            recs = list(recs.values())
        return {"missions": recs}
    except Exception as e:  # noqa: BLE001
        return {"missions": [], "error": type(e).__name__}


class _SteerBody(BaseModel):
    action: str = ""
    objective: str = ""
    note: str = ""


# Domain errors the Mission Engine raises, mapped back to their HTTP meaning
# (roadmap 20D): unknown mission 404, forbidden transition 409, malformed 422.
_MISSION_DOMAIN_STATUS = {"LookupError": 404, "InvalidTransition": 409, "ValueError": 422}


@cc_extra_router.post("/kai/missions/{mission_id}/steer")
def kai_mission_steer(mission_id: str, body: _SteerBody = None,
                      _: None = Depends(_req_op)):
    """Pause / resume / redirect a Mission Engine mission (operator-gated)."""
    return _dispatch_control(
        "control.mission.steer",
        {"mission_id": mission_id,
         "action": (body.action if body else "") or "",
         "objective": (body.objective if body else "") or None,
         "note": (body.note if body else "") or None},
        domain_map=_MISSION_DOMAIN_STATUS)


@cc_extra_router.post("/kai/missions/{mission_id}/execute")
def kai_mission_execute(mission_id: str, body: _SteerBody = None,
                        _: None = Depends(_req_op)):
    """Stop a Mission Engine mission (the steering module's /stop target)."""
    return _dispatch_control(
        "control.mission.stop",
        {"mission_id": mission_id,
         "action": (body.action if body else "") or "stop",
         "objective": (body.objective if body else "") or None,
         "note": (body.note if body else "") or None},
        domain_map=_MISSION_DOMAIN_STATUS)


def _diag_part(fn, default):
    """Run one diagnostics collector; never let a single section 500 the page."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        if isinstance(default, dict):
            return {**default, "error": f"{type(e).__name__}: {e}"}
        return default


@cc_extra_router.get("/api/diagnostics")
def api_diagnostics(_: None = Depends(_req_op)):
    """Operator-gated diagnostics roll-up.

    Aggregates the observability snapshot, enriched circuit breakers, the
    unified telemetry snapshot and provider health into the single shape the
    Command Center diagnostics panel renders. Reuses the existing collectors
    (observability, telemetry, ``/kai/circuit-breakers``, provider_health) —
    no new systems and no duplicated breaker-enrichment logic.
    """
    from datetime import datetime, timezone

    from core import observability as _observability
    from core import telemetry as _telemetry

    def _objects():
        from core.api import _load_audit_source
        return {
            "incident": _load_audit_source("incidents.json"),
            "decision": _load_audit_source("decisions.json"),
            "approval": _load_audit_source("approval_queue.json"),
            "remediation": _load_audit_source("remediation_history.json"),
            "verification": _load_audit_source("verification_history.json"),
        }

    def _health():
        from core.api import health as _health_endpoint
        return _health_endpoint()

    def _breakers():
        from core.api import circuit_breakers_list_endpoint
        return circuit_breakers_list_endpoint().get("circuit_breakers", [])

    def _providers():
        from core.ai import provider_health
        return provider_health.get_all_quota_snapshots() or {}

    telemetry = _diag_part(_telemetry.snapshot, {})
    observability = _diag_part(
        lambda: _observability.snapshot(
            objects=_diag_part(_objects, {}),
            health=_diag_part(_health, {}),
            metrics=telemetry,
            traces=[],
        ),
        {},
    )
    return {
        "schema": "diagnostics/1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "observability": observability,
        "circuit_breakers": _diag_part(_breakers, []),
        "telemetry": telemetry,
        "providers": _diag_part(_providers, {}),
    }


@cc_extra_router.get("/kai/world")
def kai_world():
    """Full world-model snapshot for the Command Center World panel.

    Read-only, same access policy as the sibling ``/kai/tools/world``. Entities
    are returned as a sorted list (the store keys them by id). Reuses
    ``core.world_model`` — it does not collect or invent state itself.
    """
    from core import world_model
    snap = world_model.get_snapshot()
    entities = [{"id": eid, **(entity or {})}
                for eid, entity in (snap.get("entities") or {}).items()]
    entities.sort(key=lambda e: str(e.get("id", "")))
    return {
        "schema_version": snap.get("schema_version", 1),
        "updated_at": snap.get("updated_at"),
        "counts": snap.get("counts") or {},
        "entities": entities,
        "edges": snap.get("edges") or [],
        "changes": snap.get("changes_since_previous") or [],
    }


@cc_extra_router.get("/kai/doctor")
def kai_doctor():
    """Full system self-diagnostic (§23)."""
    try:
        from core.kai_doctor import summary as _summary
        return _summary()
    except Exception as e:  # noqa: BLE001
        return {"error": type(e).__name__}


class _StopBody(BaseModel):
    reason: str = ""


@cc_extra_router.get("/api/emergency/status")
def emergency_status():
    try:
        from core.kai_emergency import stopped_info
        return stopped_info()
    except Exception as e:  # noqa: BLE001
        return {"stopped": False, "error": type(e).__name__}


@cc_extra_router.post("/api/emergency/stop")
def emergency_stop(body: _StopBody = None, _: None = Depends(_req_op)):
    """Emergency stop — dispatched through the Command Bus (AgentGuard + audit)."""
    return _dispatch_control("control.emergency.stop",
                             {"reason": (body.reason if body else "")})


@cc_extra_router.post("/api/emergency/resume")
def emergency_resume(_: None = Depends(_req_op)):
    """Emergency resume — dispatched through the Command Bus (AgentGuard + audit)."""
    return _dispatch_control("control.emergency.resume", {})


# -- Architecture Guardian (§24) -------------------------------------------
_KNOWN_SERVICES = [
    ("ai-orchestrator-api", 8000, "ai-orchestrator-api"),
    ("kai-scheduler", None, "kai-scheduler"),
    ("juris-kai", None, "juris-kai"),
    ("kai-directives", 8099, "kai-directives"),
    ("kai-auto-approval", None, "kai-auto-approval"),
]


def _svc_active(svc: str) -> bool:
    try:
        import subprocess
        return subprocess.run(["systemctl", "is-active", f"{svc}.service"],
                              capture_output=True, text=True, timeout=3).stdout.strip() == "active"
    except Exception:  # noqa: BLE001
        return False


@cc_extra_router.get("/kai/guardian")
def kai_guardian():
    try:
        from core.kai.architecture_guardian import ArchitectureGuardian
        inv = [{"name": n, "port": p, "enabled": True, "active": _svc_active(s)}
               for n, p, s in _KNOWN_SERVICES]
        findings = ArchitectureGuardian().scan(inv)
        return {"inventory": inv, "findings": [f.to_dict() for f in findings]}
    except Exception as e:  # noqa: BLE001
        return {"error": type(e).__name__}


# -- PWA icons (Kai Mobile) ------------------------------------------------
@cc_extra_router.get("/kai/{icon}")
def kai_icon(icon: str):
    if icon not in ("icon-192.png", "icon-512.png"):
        raise HTTPException(status_code=404, detail="not found")
    from pathlib import Path
    p = Path("/opt/ai-orchestrator/core/kai") / icon
    try:
        return Response(p.read_bytes(), media_type="image/png")
    except OSError:
        raise HTTPException(status_code=404, detail="icon missing")


# -- WebAuthn ceremony proxy (27A) — forwards to the vault host ------------
_VAULT_URL = os.environ.get("VAULT_URL", "https://192.168.1.107:8443")


def _vault_token() -> str:
    for tf in ("/root/.credentials/ai-orchestrator-vault-token", "/etc/kai/vault_mp_token"):
        try:
            with open(tf) as fh:
                return fh.read().strip()
        except OSError:
            continue
    return ""


def _vault_ssl_context(url: str):
    """TLS context for the vault machine plane (CA bundle / scoped internal)."""
    try:
        from core.ai.kai_vault_client import ssl_context_for
        return ssl_context_for(url)
    except Exception:  # noqa: BLE001
        return None


def _vault_call(path: str, method: str = "GET", body=None):
    import json as _json
    import urllib.error
    import urllib.request
    data = _json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        _VAULT_URL + path, data=data, method=method,
        headers={"Authorization": "Bearer " + _vault_token(), "content-type": "application/json"})
    try:
        with urllib.request.urlopen(
                req, timeout=8, context=_vault_ssl_context(_VAULT_URL)) as r:
            return _json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return _json.load(e)
        except Exception:  # noqa: BLE001
            return {"error": f"vault HTTP {e.code}"}
    except Exception as e:  # noqa: BLE001
        return {"error": type(e).__name__}


@cc_extra_router.get("/kai/webauthn/challenge")
def wa_challenge():
    return _vault_call("/api/v1/webauthn/challenge")


@cc_extra_router.get("/kai/webauthn/credentials")
def wa_credentials():
    return _vault_call("/api/v1/webauthn/credentials")


@cc_extra_router.post("/kai/webauthn/register")
async def wa_register(request: Request, _: None = Depends(_req_op)):
    return _vault_call("/api/v1/webauthn/register", "POST", await request.json())


@cc_extra_router.post("/kai/webauthn/authenticate")
async def wa_authenticate(request: Request, _: None = Depends(_req_op)):
    return _vault_call("/api/v1/webauthn/authenticate", "POST", await request.json())


# -- Duo Push approval (27S) -----------------------------------------------
@cc_extra_router.get("/kai/duo/status")
def duo_status():
    from core.vault.duo import config_from_env, is_configured
    c = config_from_env()
    return {"configured": is_configured(c), "username": c.get("username", "")}


@cc_extra_router.post("/kai/duo/approve")
def duo_approve(username: str = "", _: None = Depends(_req_op)):
    from core.vault.duo import DuoClient, config_from_env, is_configured
    c = config_from_env()
    if not is_configured(c):
        return JSONResponse(status_code=503, content={
            "ok": False, "error": "Duo not configured (set DUO_IKEY/DUO_SKEY/DUO_API_HOST)"})
    user = username or c.get("username", "")
    if not user:
        return {"ok": False, "error": "username required"}
    try:
        out = DuoClient(c["ikey"], c["skey"], c["host"]).approve(user, timeout=60)
        return {"ok": True, **out}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# -- Hubtel billing config (17P) — vault-first, masked reads ----------------
_HUBTEL_PATHS = {
    "client_id": "secrets/external/hubtel-client-id",
    "client_secret": "secrets/external/hubtel-client-secret",
    "merchant_number": "secrets/external/hubtel-merchant-number",
    "callback_url": "secrets/external/hubtel-callback-url",
}
_HUBTEL_REQUIRED = ("client_id", "client_secret", "merchant_number")


def _mask(name: str, v: str) -> str:
    if not v:
        return ""
    if name == "client_secret":
        return "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022" + v[-2:]
    if len(v) <= 8:
        return v[:2] + "\u2026"
    return v[:4] + "\u2026" + v[-2:]


def _operator(request: Request) -> bool:
    tok = request.headers.get("x-kai-session", "")
    if tok:
        from core import authz
        try:
            if authz._resolve_session(tok):
                return True
        except Exception:  # noqa: BLE001
            pass
    return bool(request.headers.get("authorization", ""))


@cc_extra_router.get("/hubtel/status")
def hubtel_status():
    """Configured = path present in the vault. Never reveals values
    (Hubtel paths are high-risk and Duo-gated for reveal)."""
    r = _vault_call("/api/v1/machine/paths")
    paths = set(r.get("paths") or [])
    out, masked = {}, {}
    for name, path in _HUBTEL_PATHS.items():
        present = path in paths
        out[name] = present
        masked[name] = "set (encrypted)" if present else ""
    return {"backend": "vault", "configured": out, "masked": masked,
            "ready": all(out.get(k) for k in _HUBTEL_REQUIRED)}


class _HubtelBody(BaseModel):
    client_id: str = ""
    client_secret: str = ""
    merchant_number: str = ""
    callback_url: str = ""


@cc_extra_router.put("/hubtel/config")
def hubtel_config(body: _HubtelBody, request: Request):
    if not _operator(request):
        return JSONResponse(status_code=401, content={"ok": False, "error": "operator session required"})
    provided = {k: v for k, v in body.model_dump().items() if v}
    if not provided:
        return {"ok": False, "error": "no fields provided"}
    saved, errors = [], {}
    for name, value in provided.items():
        r = _vault_call("/api/v1/machine/secret/set", "POST",
                        {"path": _HUBTEL_PATHS[name], "value": value,
                         "reason": "hubtel config set via Command Center"})
        if r.get("ok"):
            saved.append(name)
        else:
            errors[name] = r.get("error", "failed")
    return {"ok": not errors, "saved": saved, "errors": errors}


# -- Duo-first SSO (central auth plane) ------------------------------------
class _DuoLoginBody(BaseModel):
    username: str = ""
    scopes: list | None = None
    passcode: str = ""


@cc_extra_router.get("/auth/duo/status")
def duo_sso_status():
    from core.auth import duo_sso
    from core.vault.duo import config_from_env, is_configured
    return {"enabled": duo_sso.enabled(), "configured": is_configured(),
            "username": config_from_env().get("username", "")}


@cc_extra_router.post("/auth/duo/sms")
def duo_sso_sms(body: _DuoLoginBody):
    """Send an SMS passcode (fallback when no Duo Push device is enrolled)."""
    from core.auth import duo_sso
    from core.vault.duo import config_from_env
    user = body.username or config_from_env().get("username", "")
    if not user:
        return JSONResponse(status_code=422, content={"ok": False, "error": "username required"})
    out = duo_sso.send_sms(user)
    return out if out.get("ok") else JSONResponse(status_code=502, content=out)


@cc_extra_router.post("/auth/duo/session")
def duo_sso_session(body: _DuoLoginBody):
    """Duo push (or SMS passcode) -> short-lived SSO + Command Center session."""
    from core.auth import duo_sso
    from core.vault.duo import config_from_env
    user = body.username or config_from_env().get("username", "")
    if not user:
        return JSONResponse(status_code=422, content={"ok": False, "error": "username required"})
    scopes = tuple(body.scopes or ["login"])
    if body.passcode:
        out = duo_sso.login_with_passcode(user, body.passcode, scopes=scopes)
    else:
        out = duo_sso.login(user, scopes=scopes)
    return out if out.get("ok") else JSONResponse(status_code=401, content=out)


@cc_extra_router.get("/auth/duo/verify")
def duo_sso_verify(token: str = ""):
    from core.auth import duo_sso
    claims = duo_sso.verify_session(token)
    return {"valid": bool(claims), **(claims or {})}


class _DuoTokenBody(BaseModel):
    token: str = ""


@cc_extra_router.post("/auth/duo/logout")
def duo_sso_logout(body: _DuoTokenBody):
    from core.auth import duo_sso
    return {"revoked": duo_sso.revoke(body.token)}


# -- Telegram Mini App (§31) -----------------------------------------------
@cc_extra_router.get("/miniapp", response_class=HTMLResponse)
def miniapp():
    from pathlib import Path
    p = Path("/opt/ai-orchestrator/core/kai/miniapp.html")
    try:
        return p.read_text()
    except OSError:
        return "<h1>Kai</h1><p>miniapp not found</p>"


class _WebAppAuthBody(BaseModel):
    init_data: str = ""


@cc_extra_router.post("/api/telegram/webapp-auth")
def telegram_webapp_auth(body: _WebAppAuthBody):
    """Validate Telegram WebApp initData (§31) and return the verified identity."""
    token = os.environ.get("KAI_TELEGRAM_BOT_TOKEN", "")
    if not token:
        return JSONResponse(status_code=503,
                            content={"ok": False, "error": "bot token not configured"})
    from core.telegram.webapp_auth import validate_init_data
    try:
        info = validate_init_data(body.init_data, token)
    except ValueError as e:
        return JSONResponse(status_code=401, content={"ok": False, "error": str(e)})
    return {"ok": True, "user": info["user"], "auth_date": info["auth_date"]}


def _roadmap_summary() -> dict:
    try:
        import json as _json
        with open("/opt/ai-orchestrator/roadmap.json") as fh:
            phases = _json.load(fh).get("phases", [])
        done = sum(1 for p in phases if p.get("status") == "completed")
        return {"total": len(phases), "completed": done}
    except Exception:  # noqa: BLE001
        return {}


def _miniapp_data(action: str, q: str = "") -> dict:
    """Server-side data for the authenticated Mini App. Values-free, read-only."""
    try:
        if action == "status":
            from core import backup_manager as bm
            bks = bm.list_backups()
            return {"roadmap": _roadmap_summary(),
                    "backups": {"count": len(bks),
                                "latest": (bks[0]["name"] if bks else None)}}
        if action == "network":
            return network_overview()
        if action == "missions":
            return kai_missions()
        if action == "doctor":
            return kai_doctor()
        if action == "approvals":
            try:
                from core.approval import list_pending
                return {"pending": list_pending()}
            except Exception as e:  # noqa: BLE001
                return {"pending": [], "error": type(e).__name__}
        if action == "backups":
            from core import backup_manager as bm
            items = bm.list_backups()
            return {"count": len(items),
                    "backups": [{"name": b["name"], "created_at": b.get("created_at"),
                                 "verified": b.get("verified"), "size_bytes": b.get("size_bytes")}
                                for b in items]}
        return {"error": "unknown action"}
    except Exception as e:  # noqa: BLE001
        return {"error": type(e).__name__}


@cc_extra_router.get("/miniapp/api/{action}")
@cc_extra_router.post("/miniapp/api/{action}")
async def miniapp_api(action: str, request: Request):
    """Token-gated Mini App API. Authenticates Telegram initData on EVERY call
    (X-Telegram-Init-Data header), so only a real Telegram WebApp session can
    read data. The admin Command Center is NOT exposed."""
    token = os.environ.get("KAI_TELEGRAM_BOT_TOKEN", "")
    if not token:
        return JSONResponse(status_code=503, content={"error": "bot token not configured"})
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    from core.telegram.webapp_auth import validate_init_data
    try:
        validate_init_data(init_data, token)
    except ValueError as e:
        return JSONResponse(status_code=401, content={"error": str(e)})
    q = ""
    if action == "knowledge":
        try:
            q = (await request.json()).get("q", "")
        except Exception:  # noqa: BLE001
            q = ""
    return _miniapp_data(action, q)


# -- Vault Workspace (§24) — metadata only, never values -------------------
@cc_extra_router.get("/kai/vault/metadata")
def vault_metadata():
    """List machine-plane secret PATHS + status. Never returns values."""
    import json as _json

    def _health_and_paths():
        """Query the machine plane: /health (count) + /api/v1/machine/paths."""
        token = ""
        for tf in ("/root/.credentials/ai-orchestrator-vault-token",
                   "/etc/kai/vault_mp_token"):
            try:
                with open(tf) as fh:
                    token = fh.read().strip()
                if token:
                    break
            except OSError:
                continue
        base = os.environ.get("VAULT_URL", "https://192.168.1.107:8443")
        try:
            import urllib.request
            context = _vault_ssl_context(base)
            with urllib.request.urlopen(f"{base}/health", timeout=2, context=context) as r:
                seeded = _json.load(r).get("seeded_paths", 0)
            paths = []
            if token:
                req = urllib.request.Request(
                    f"{base}/api/v1/machine/paths",
                    headers={"Authorization": f"Bearer {token}"})
                with urllib.request.urlopen(req, timeout=2, context=context) as r:
                    paths = _json.load(r).get("paths", [])
            return True, seeded, paths
        except Exception:  # noqa: BLE001
            return False, 0, []

    reachable, seeded, paths = _health_and_paths()
    if not paths:  # fall back to the capability manifest if present
        try:
            with open("/opt/ai-orchestrator/config/vault_capabilities.json") as fh:
                caps = _json.load(fh).get("capabilities", {})
            paths = list(caps.get("read", [])) + list(caps.get("write", []))
        except Exception:  # noqa: BLE001
            paths = []

    secrets = [{"name": p.rsplit("/", 1)[-1].upper(), "path": p,
                "value": "\u25cf\u25cf\u25cf\u25cf\u25cf\u25cf",
                "status": "healthy" if reachable else "blocked",
                "rotation_days": None, "used_by": "Kai"} for p in sorted(paths)]
    count = len(secrets) or seeded
    return {"backend": "kai-vault-machine-plane (:8120 TLS :8443)",
            "reachable": reachable,
            "status": "healthy" if reachable else "blocked",
            "count": count, "seeded_paths": seeded, "secrets": secrets,
            "remediation": "" if reachable else "start kai-vault-machine-plane on CT107"}


# -- Self-healing (§22) + resiliency ---------------------------------------
@cc_extra_router.get("/kai/resiliency")
def kai_resiliency():
    try:
        from core.resiliency import get_resiliency_status
        return get_resiliency_status()
    except Exception as e:  # noqa: BLE001
        return {"error": type(e).__name__}


@cc_extra_router.post("/kai/self-healing")
def kai_self_healing():
    try:
        from core.self_healing import run_self_healing
        return run_self_healing()
    except Exception as e:  # noqa: BLE001
        return {"error": type(e).__name__}


# -- Session Center (§32) ---------------------------------------------------
from core.session_center import SessionCenter  # noqa: E402

_SESSIONS = SessionCenter()


class _AttachBody(BaseModel):
    session_id: str
    mission_id: str


@cc_extra_router.get("/api/sessions")
def sessions_list():
    return {"sessions": _SESSIONS.list()}


@cc_extra_router.post("/api/sessions")
def sessions_attach(body: _AttachBody):
    return _SESSIONS.attach(body.session_id, body.mission_id)


@cc_extra_router.post("/api/sessions/{sid}/{action}")
def sessions_action(sid: str, action: str):
    fn = getattr(_SESSIONS, action, None)
    if action not in ("detach", "reconnect", "interrupt", "resume") or fn is None:
        return {"ok": False, "error": "unknown action"}
    return {"ok": bool(fn(sid))}


# ── Module registry → Command Center tab source of truth (directive 2026-09-19) ──

@cc_extra_router.get("/api/cc/modules")
def cc_modules():
    """Registry entries that own a Command Center tab.

    ``status`` is the lifecycle flag (``live|hidden|retired``); ``health`` is the
    backing endpoint the CC probes at render time before showing the tab.
    ``retired_tabs`` names tabs whose module no longer exists on the server, so
    the CC can drop them instead of rendering a dead panel.
    """
    from core.module_registry import get_cc_modules, get_retired_cc_tabs
    return {"modules": get_cc_modules(), "retired_tabs": get_retired_cc_tabs()}


# ── Legal Brain (Knowledge Engine) real sub-views ───────────────────────────
# ``/cc/legal/*`` proxies straight to the legal-brain service, which has no
# ``/knowledge`` or ``/firewall`` paths. These orchestrator-side views reuse
# core.legal_brain_client so the CC shows real data instead of a 404 dump.

def _legal_sources() -> list:
    from core import legal_brain_client as lb
    reg = lb.sources()
    if isinstance(reg, dict):
        return reg.get("sources", []) or []
    return reg or []


@cc_extra_router.get("/api/legal-brain/sources")
def legal_brain_sources():
    try:
        sources = _legal_sources()
    except Exception as e:  # noqa: BLE001
        return JSONResponse(
            {"ok": False, "error": f"{type(e).__name__}: {e}", "sources": []},
            status_code=502)
    return {"ok": True, "count": len(sources), "sources": sources}


@cc_extra_router.get("/api/legal-brain/firewall")
def legal_brain_firewall():
    """Source-allowlist / policy status for the Knowledge Engine firewall.

    The firewall is the legal brain's admitted-source registry: only enabled
    primary/secondary domains may be ingested. This surfaces the policy and the
    exact allowlist so the CC can show it as a real table.
    """
    try:
        sources = _legal_sources()
    except Exception as e:  # noqa: BLE001
        return JSONResponse(
            {"ok": False, "error": f"{type(e).__name__}: {e}",
             "policy": {}, "sources": []}, status_code=502)
    enabled = [s for s in sources if s.get("enabled", True)]
    disabled = [s for s in sources if not s.get("enabled", True)]
    domains = sorted({s.get("domain") for s in enabled if s.get("domain")})
    by_access: dict = {}
    for s in sources:
        by_access[s.get("access", "unknown")] = by_access.get(s.get("access", "unknown"), 0) + 1
    return {
        "ok": True,
        "policy": {
            "mode": "allowlist",
            "enforced": True,
            "allowed_sources": len(enabled),
            "blocked_sources": len(disabled),
            "allowed_domains": len(domains),
            "by_access": by_access,
        },
        "domains": domains,
        "sources": sources,
    }


# ── Legal Brain panel proxy (Legal Brain 2.0 Phase 8, Task 3) ───────────────
# The CC browser never talks to the brain (CT100) directly. Every route here
# proxies ``core.legal_brain_client`` (the token is injected server-side) and is
# operator-gated — even reads, because a corpus read is still privileged. The
# upstream base URL/token never reaches the browser.

def _lb_error(exc: Exception) -> JSONResponse:
    """Map a brain transport error to an honest, non-2xx JSON response.

    An upstream ``HTTPError`` keeps its status (e.g. 404 unknown everyday
    topic); anything else is a 502 (brain unreachable / bad payload).
    """
    import urllib.error
    if isinstance(exc, urllib.error.HTTPError):
        code = exc.code if 400 <= exc.code < 600 else 502
        return JSONResponse({"ok": False, "error": f"brain HTTP {exc.code}"},
                            status_code=code)
    return JSONResponse(
        {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
        status_code=502)


def _lb_ok(data):
    """Wrap a brain dict payload (or list) in the ``{ok: True, ...}`` shape."""
    if isinstance(data, dict):
        return {"ok": True, **data}
    return {"ok": True, "data": data}


@cc_extra_router.get("/api/legal/health")
def legal_health(stale_days: int | None = None, _: None = Depends(_req_op)):
    """Corpus knowledge-health snapshot (docs/with-content/temporal/integrity)."""
    from core import legal_brain_client as lb
    try:
        return _lb_ok(lb.legal_health(stale_days=stale_days))
    except Exception as exc:  # noqa: BLE001
        return _lb_error(exc)


@cc_extra_router.get("/api/legal/coverage")
def legal_coverage(threshold: int | None = None, _: None = Depends(_req_op)):
    """Legal-area coverage + harvest priorities."""
    from core import legal_brain_client as lb
    try:
        return _lb_ok(lb.coverage(threshold=threshold))
    except Exception as exc:  # noqa: BLE001
        return _lb_error(exc)


@cc_extra_router.get("/api/legal/gaps")
def legal_gaps(status: str = "", limit: int = 50,
               _: None = Depends(_req_op)):
    """Ask-to-Acquire gap queue (status/domain/ministry/question)."""
    from core import legal_brain_client as lb
    try:
        gaps = lb.list_gaps(status=status or None, limit=limit)
    except Exception as exc:  # noqa: BLE001
        return _lb_error(exc)
    return {"ok": True, "count": len(gaps or []), "gaps": gaps or []}


class _AcquireBody(BaseModel):
    per_source: int = 5
    delay: float = 0.5


@cc_extra_router.post("/api/legal/gaps/{gap_id}/acquire")
def legal_gap_acquire(gap_id: int, body: _AcquireBody | None = None,
                      _: None = Depends(_req_op)):
    """Run one bounded acquisition pass for a gap (operator-gated write)."""
    from core import legal_brain_client as lb
    per_source = int(body.per_source) if body is not None else 5
    delay = float(body.delay) if body is not None else 0.5
    return lb.acquire_gap(gap_id, per_source=per_source, delay=delay)


@cc_extra_router.get("/api/legal/everyday")
def legal_everyday(_: None = Depends(_req_op)):
    """Everyday-Law topic catalogue."""
    from core import legal_brain_client as lb
    try:
        return {"ok": True, "topics": lb.everyday_topics()}
    except Exception as exc:  # noqa: BLE001
        return _lb_error(exc)


@cc_extra_router.get("/api/legal/everyday/{topic}")
def legal_everyday_topic(topic: str, _: None = Depends(_req_op)):
    """One grounded plain-language explainer (404 for an unknown topic)."""
    from core import legal_brain_client as lb
    try:
        return _lb_ok(lb.everyday(topic))
    except Exception as exc:  # noqa: BLE001
        return _lb_error(exc)


@cc_extra_router.get("/api/legal/licences")
def legal_licences(_: None = Depends(_req_op)):
    """Source commercial-use licence register (read-only)."""
    from core import legal_brain_client as lb
    try:
        return _lb_ok(lb.licences())
    except Exception as exc:  # noqa: BLE001
        return _lb_error(exc)


@cc_extra_router.get("/api/legal/relations/{doc_id}")
def legal_relations(doc_id: int, _: None = Depends(_req_op)):
    """Typed knowledge-graph relations of one document."""
    from core import legal_brain_client as lb
    try:
        return _lb_ok(lb.relations(doc_id))
    except Exception as exc:  # noqa: BLE001
        return _lb_error(exc)


@cc_extra_router.get("/api/legal/status/{doc_id}")
def legal_status(doc_id: int, _: None = Depends(_req_op)):
    """Temporal / current-law status of one document."""
    from core import legal_brain_client as lb
    try:
        return _lb_ok(lb.status(doc_id))
    except Exception as exc:  # noqa: BLE001
        return _lb_error(exc)


class _AskBody(BaseModel):
    query: str = ""
    task_type: str = "juris_research"
    deep: bool = False
    fast: bool = False
    stream: bool = False
    context: str = ""


def _compact_docs(docs: list) -> list:
    """Bound retrieval docs to the fields the Ask tab renders (never bodies)."""
    keep = ("id", "title", "citation", "year", "store_mode", "authority_level")
    return [{k: d.get(k) for k in keep if d.get(k) is not None}
            for d in (docs or [])]


def _legal_generate(prompt: str, task_type: str) -> tuple[str, str]:
    """Blocking local generation with the same fallback as the Juris test query."""
    from core.juris_kai import streaming
    try:
        return streaming.generate(prompt, task_type=task_type), streaming.DEFAULT_MODEL
    except Exception:  # noqa: BLE001 - fall back to the generic router
        from core.ai.ai_router import delegate
        result = delegate(prompt, task_type=task_type, capability="text_task")
        return (result.get("response") or ""), (result.get("provider") or "")


def _legal_ask_quick(query: str, task_type: str, context: str) -> dict:
    from core.juris_kai import grounding
    plan = grounding.build_grounded_plan(query, task_type, context=context,
                                         asker="cc")
    if plan["refusal"]:
        return {
            "success": True, "mode": "quick", "text": plan["refusal"],
            "verdict": plan["verdict"], "grounded": False,
            "refusal": plan["refusal"], "sources": [], "sources_footer": "",
            "uncertainty": None, "model": "", "latency_ms": 0,
        }
    started = time.time()
    try:
        text, model = _legal_generate(plan["prompt"], task_type)
    except Exception as exc:  # noqa: BLE001 - report, never blank the endpoint
        return {"success": False, "mode": "quick", "error": str(exc)}
    if text and text.strip():
        text = plan["banner"] + text + plan["footer"]
    return {
        "success": True, "mode": "quick", "text": text,
        "verdict": plan["verdict"], "grounded": bool(plan.get("groundable")),
        "refusal": None, "sources": _compact_docs(plan["docs"]),
        "sources_footer": plan["footer"], "uncertainty": None,
        "model": model, "latency_ms": round((time.time() - started) * 1000, 1),
    }


def _deep_payload(query: str, result: dict, fast: bool) -> dict:
    """Shape a ``run_deep`` result for the Ask tab (fast or thorough)."""
    from core.juris_kai import grounding, reasoning
    docs = result.get("docs") or []
    judge = result.get("judge") or {}
    latency = result.get("latency") or {}
    text = (reasoning.render_deep(result)
            + grounding.build_sources_footer(docs))
    judge_ttft = latency.get("judge_ttft")
    ttft_ms = None
    if judge_ttft is not None:
        pre = latency.get("retrieve", 0.0) + latency.get(
            "parallel", latency.get("advocate", 0.0))
        ttft_ms = round((pre + judge_ttft) * 1000, 1)
    return {
        "success": True, "mode": "deep_fast" if fast else "deep",
        "fast": bool(fast), "text": text,
        "verdict": result.get("verdict") or ("GROUNDED" if docs else "UNGROUNDED"),
        "grounded": bool(docs), "refusal": None,
        "sources": _compact_docs(docs),
        "sources_footer": grounding.build_sources_footer(docs),
        "uncertainty": {
            "established": len(judge.get("established") or []),
            "disputed": len(judge.get("disputed") or []),
            "unresolved": len(judge.get("unresolved") or []),
            "confidence": judge.get("confidence"),
        },
        "degraded": bool(result.get("degraded")),
        "latency": latency,
        "ttft_ms": ttft_ms,
        "model": "deep",
    }


def _legal_ask_deep(query: str, fast: bool = False) -> dict:
    from core.juris_kai import reasoning
    result = reasoning.run_deep(query, fast=fast)
    return _deep_payload(query, result, fast)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _legal_ask_deep_stream(query: str, fast: bool = False):
    """Stream a Deep answer as SSE: status now, judge deltas, then final.

    The heavy passes run on a daemon worker; the judge's first token is relayed
    as soon as it arrives (low TTFT) and the full, citation-firewalled answer
    follows in the ``final`` event. The worker is bounded by the caller's
    per-pass read timeouts, so a stalled model closes the stream rather than
    hanging the endpoint.
    """
    import queue
    import threading

    from core.juris_kai import reasoning

    events: "queue.Queue" = queue.Queue()
    box: dict = {}

    def _work():
        try:
            def _on_chunk(piece):
                events.put(("delta", piece))

            box["result"] = reasoning.run_deep(
                query, fast=fast, stream_judge=True, on_judge_chunk=_on_chunk)
        except Exception as exc:  # noqa: BLE001 - surface, never hang
            box["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            events.put(("done", None))

    threading.Thread(target=_work, name="juris-deep-sse", daemon=True).start()

    def _gen():
        yield _sse("status", {"mode": "deep_fast" if fast else "deep",
                              "fast": bool(fast), "phase": "reasoning"})
        while True:
            kind, payload = events.get()
            if kind == "done":
                break
            yield _sse("delta", {"text": payload})
        result = box.get("result")
        if result is None:
            yield _sse("error", {"error": box.get("error") or "deep failed"})
            return
        yield _sse("final", _deep_payload(query, result, fast))

    return StreamingResponse(_gen(), media_type="text/event-stream")


@cc_extra_router.post("/api/legal/ask")
def legal_ask(body: _AskBody, _: None = Depends(_req_op)):
    """Grounded legal answer for the Ask tab; optional deep (IRAC) reasoning.

    ``deep=true&fast=true`` runs the fast 3-pass variant (retrieval-only
    opponent, leaner budgets); ``stream=true`` streams the judge pass as SSE.
    Strict grounding: an unsupported or non-Ghana question is refused without a
    model call. Operator-gated because it invokes local generation and may
    record an Ask-to-Acquire gap.
    """
    query = (body.query or "").strip()
    if not query:
        return {"success": False, "error": "query is required"}
    if body.deep:
        if body.stream:
            return _legal_ask_deep_stream(query, fast=body.fast)
        return _legal_ask_deep(query, fast=body.fast)
    return _legal_ask_quick(query, body.task_type or "juris_research",
                            body.context or "")

