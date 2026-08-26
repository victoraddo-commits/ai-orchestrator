"""KAI Ultimate mobile app API — pairing, device auth, aggregated endpoints.

Security model:
  - Pairing: 6-digit code generated server-side, delivered ONLY to the
    operator's Telegram (never in the API response). Code binds to a device
    fingerprint the client presents at request time. 10-min expiry, single use.
  - Device tokens: existing device_registry bearer tokens (bcrypt-hashed).
  - All /kai/app/* data endpoints require a valid device token.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/kai/app", tags=["kai-app"])

# pairing code store: code_hash -> {device_fp, expires, used}
_PAIRINGS: dict[str, dict] = {}
_LAST_CODES: list = []  # (raw_code, ts) — dev/test only, gated by KAI_PAIRING_DEBUG
PAIRING_TTL_S = 600


# --- internal ----------------------------------------------------------------

def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _fingerprint(device_fp: str) -> str:
    return hashlib.sha256(device_fp.encode()).hexdigest()[:32]


async def _require_device(
    authorization: Optional[str] = Header(default=None),
) -> dict:
    token = authorization[7:].strip() if (authorization or "").lower().startswith("bearer ") else None
    if not token:
        raise HTTPException(401, "missing device token")
    from core.device_registry import find_device_by_token
    device_id = find_device_by_token(token)
    if not device_id:
        raise HTTPException(401, "invalid or revoked device token")
    return {"device_id": device_id, "label": device_id}


# --- operator-side: create a pairing code (capability-gated) ------------------

class PairRequest(BaseModel):
    device_fingerprint: str      # app-generated stable id (hash of ANDROID_ID etc.)
    device_name: str = ""
    platform: str = "android"


def _telegram_send(text: str) -> bool:
    try:
        from core.telegram_bridge import send_message
        send_message(text)
        return True
    except Exception:
        return False


@router.post("/pair/request")
async def pair_request(body: PairRequest):
    """App asks for pairing. We do NOT return the code — it goes to Telegram."""
    code = f"{secrets.randbelow(1_000_000):06d}"
    fp = _fingerprint(body.device_fingerprint)
    # purge stale
    now = time.time()
    for k in [k for k, v in _PAIRINGS.items() if v["expires"] < now or v.get("used")]:
        _PAIRINGS.pop(k, None)
    _PAIRINGS[_hash_code(code)] = {
        "fp": fp, "expires": now + PAIRING_TTL_S,
        "meta": {"name": body.device_name or "unnamed", "platform": body.platform},
    }
    # dev/test ring buffer (KAI_PAIRING_DEBUG=1 only) for automated emulator tests
    try:
        if os.environ.get("KAI_PAIRING_DEBUG") == "1":
            _LAST_CODES.append((code, now))
            del _LAST_CODES[:-5]
    except NameError:
        pass
    sent = _telegram_send(
        f"🔐 KAI App pairing requested\n"
        f"Device: {body.device_name or 'unknown'} ({body.platform})\n"
        f"Code: {code}\nExpires in 10 minutes.\n\n"
        f"If you didn't request this, ignore it — no access is granted.")
    if not sent:
        raise HTTPException(503, "could not deliver code via Telegram")
    return {"ok": True, "expires_in": PAIRING_TTL_S,
            "hint": "code sent to your Telegram"}


class PairConfirm(BaseModel):
    code: str
    device_fingerprint: str
    device_name: str = "KAI App"
    platform: str = "android"
    platform_version: str = ""
    manufacturer: str = ""
    model: str = ""


@router.post("/pair/confirm")
async def pair_confirm(body: PairConfirm):
    h = _hash_code(body.code.strip())
    rec = _PAIRINGS.get(h)
    now = time.time()
    if not rec or rec["expires"] < now or rec.get("used"):
        raise HTTPException(403, "invalid or expired code")
    if rec["fp"] != _fingerprint(body.device_fingerprint):
        raise HTTPException(403, "code was issued to a different device")
    rec["used"] = True
    # register through existing registry → returns raw token once.
    # device_registry resolves "memory" CWD-relatively — pin the process cwd
    # so app-paired devices land in the SAME store the API service reads.
    import os
    os.chdir("/project/ai-orchestrator")
    from core.device_registry import register_device
    reg = register_device(
        device_id=f"kaiapp-{secrets.token_hex(4)}",
        device_name=body.device_name or rec["meta"]["name"],
        platform=body.platform, platform_version=body.platform_version,
        manufacturer=body.manufacturer, model=body.model,
        registered_by="app-pairing",
        capabilities=["monitor", "approve", "voice_chat", "wake_word"],
    )
    return {"ok": True, "device_id": reg["device_id"], "token": reg["token"],
            "note": "store token securely; biometric-gate all usage"}


# --- app data endpoints (device-token gated) ----------------------------------

@router.get("/home")
async def app_home(dev: dict = Depends(_require_device)):
    """Everything for the Home screen: executive summary + world + modules."""
    from core.kai_executive import prioritize
    from core.world_model import get_state
    p = prioritize()
    w = get_state()
    return {"executive": p, "world": w, "data_trust": _data_age_minutes()}


@router.get("/proxmox")
async def app_proxmox(dev: dict = Depends(_require_device)):
    from core.proxmox_monitor import PROXMOX_NODES
    from core.proxmox_registry import discover_node_inventory
    nodes = []
    for n in PROXMOX_NODES:
        try:
            inv = discover_node_inventory(n)
            nodes.append({"name": n["name"], "reachable": inv.get("reachable", False),
                          "containers": inv.get("containers", []),
                          "vms": inv.get("vms", []),
                          "storage": inv.get("storage", [])})
        except Exception as e:
            nodes.append({"name": n["name"], "reachable": False, "error": str(e)})
    return {"nodes": nodes}


@router.get("/missions")
async def app_missions(dev: dict = Depends(_require_device)):
    from core.kai_missions import list_missions
    return {"missions": list_missions()}


@router.get("/enhancements")
async def app_enhancements(dev: dict = Depends(_require_device)):
    from core.kai_enhancements import status
    return {"enhancements": status()}


@router.get("/wg/peers")
async def app_wg_peers(dev: dict = Depends(_require_device)):
    """Live WG peer list from DD-WRT (via the tool's telnet path)."""
    import os
    os.chdir("/project/ai-orchestrator")
    from core.kai_tools import builtin
    from core.kai_tools.registry import run_tool
    # show peers = SAFE read; reuse the telnet helper directly
    try:
        show = builtin._ddwrt_telnet("wg show wg0 peers")
        return {"ok": True, "raw": show[:2000]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class WgCreateBody(BaseModel):
    label: str


@router.post("/wg/create")
async def app_wg_create(body: WgCreateBody, dev: dict = Depends(_require_device)):
    """Create a peer: files the HIGH_RISK request → operator approves (Telegram/CC)
    → app polls the result. Returns the approval id for tracking."""
    from core.kai_tools.policy import request_approval
    rid = request_approval("kai.wireguard.create_peer",
                           {"server": "ddwrt", "label": body.label},
                           f"KAI App ({dev.get('device_id')}): create WG peer '{body.label}'")
    return {"ok": rid is not None, "approval_id": rid,
            "note": "operator must approve; app polls /wg/result"}


@router.post("/wg/execute/{approval_id}")
async def app_wg_execute(approval_id: str, dev: dict = Depends(_require_device)):
    """After operator approval, execute the pending peer creation and return
    the config. The approval must exist and be approved/executed."""
    import asyncio
    from core.kai_tools import policy
    # find the approved request to confirm authorization
    from core import approval as appr
    req = next((r for r in appr.load_requests()
                if r.get("id") == approval_id
                and r.get("status") in ("approved", "executed")
                and "wireguard" in str(r.get("action", ""))), None)
    if not req:
        raise HTTPException(403, "no approved wireguard request with that id")
    result = policy.execute("kai.wireguard.create_peer",
                            {"server": "ddwrt", "label": req.get("reason", "peer")[-30:]},
                            operator=f"app:{dev.get('device_id')}",
                            reason=f"approved request {approval_id}")
    if result.ok:
        return {"ok": True, "config_text": result.data.get("config_text", ""),
                "address": result.data.get("address")}
    return {"ok": False, "error": result.error}


@router.get("/pair/last-code")
async def pair_last_code():
    """DEV/TEST ONLY — returns the most recent unused pairing code.
    Gated by KAI_PAIRING_DEBUG=1 in the orchestrator env; never enabled in prod."""
    import os
    if os.environ.get("KAI_PAIRING_DEBUG") != "1":
        raise HTTPException(403, "disabled")
    now = time.time()
    # codes are hashed in _PAIRINGS; keep a parallel raw-code ring buffer at request time
    if not _LAST_CODES:
        raise HTTPException(404, "no codes issued")
    code, ts = _LAST_CODES[-1]
    if now - ts > PAIRING_TTL_S:
        raise HTTPException(404, "code expired")
    return {"code": code}


def _data_age_minutes() -> dict:
    """§28 data-trust: how old is each major data source?"""
    from datetime import datetime, timezone
    out = {}
    now = datetime.now(timezone.utc)
    try:
        import json
        with open("/project/ai-orchestrator/memory/world_model.json") as fh:
            wm = json.load(fh)
        ts = datetime.fromisoformat(wm.get("updated_at"))
        out["world_model"] = round((now - ts).total_seconds() / 60)
    except Exception:
        out["world_model"] = None
    try:
        import sqlite3
        c = sqlite3.connect("/project/ai-orchestrator/memory/health_observatory.db")
        row = c.execute("SELECT MAX(timestamp) FROM health_metrics").fetchone()[0]
        ts = datetime.fromisoformat(row)
        out["health_metrics"] = round((now - ts).total_seconds() / 60)
    except Exception:
        out["health_metrics"] = None
    return out


class VisionBody(BaseModel):
    image_b64: str
    question: str = "Describe this image and note anything unusual."


@router.post("/vision")
async def app_vision(body: VisionBody, dev: dict = Depends(_require_device)):
    """KAI EYES: analyze an image (camera capture or screenshot) through the
    vision model. Uses the same Gemini path as kai.vision.analyze_url."""
    import base64 as b64
    try:
        png = b64.b64decode(body.image_b64)
    except Exception:
        raise HTTPException(400, "image_b64 must be base64")
    if len(png) < 100:
        raise HTTPException(400, "image too small")
    if len(png) > 8_000_000:
        raise HTTPException(400, "image too large (max 8MB)")
    from core.kai_tools.builtin import _vision_ask
    result = _vision_ask(png, body.question)
    return {"ok": True, **result}


@router.get("/capabilities")
async def app_capabilities(dev: dict = Depends(_require_device)):
    """§50 universal capability registry — what can JARVIS do right now?
    Derived from the live tool bus (risk classes + descriptions)."""
    from core.kai_tools.registry import describe_all
    tools = describe_all()
    by_category = {}
    for t in tools:
        cat = t["id"].split(".")[1] if t["id"].count(".") >= 1 else "core"
        by_category.setdefault(cat, []).append({
            "id": t["id"], "name": t.get("name"), "risk": t.get("risk"),
            "description": t.get("description", "")[:120]})
    return {"total": len(tools), "categories": by_category,
            "note": "CONTROLLED/HIGH_RISK require approval per policy"}


@router.get("/briefing")
async def app_briefing(dev: dict = Depends(_require_device), send: bool = False):
    """§47: 'JARVIS, catch me up' — executive briefing (facts only)."""
    from core.kai_executive import run_briefing
    text = run_briefing(kind="on-demand", send=bool(send))
    return {"briefing": text}


@router.get("/spend")
async def app_spend(days: int = 30, dev: dict = Depends(_require_device)):
    """Real AI spend for the app's Jarvis tab — cost tracker summary."""
    from core.ai.cost_tracker import get_cost_summary
    return get_cost_summary(max(1, min(days, 90)))


@router.get("/terminal")
async def app_terminal(dev: dict = Depends(_require_device)):
    """Terminal join: returns the phone-reachable ttyd URL + basic-auth
    credential. Credential lives on disk (0600) and is only ever handed to
    a PAIRED device over the WireGuard-only relay chain."""
    import os as _os
    cred_path = "/etc/default/kai-terminal-cred"
    if not _os.path.exists(cred_path):
        raise HTTPException(503, "terminal service not configured")
    cred = open(cred_path).read().strip()
    if ":" not in cred:
        raise HTTPException(503, "terminal credential malformed")
    # phone-facing entry is the proxmox-b socat relay (WG/LAN reachable);
    # derive it from whatever host the app already talks to.
    from urllib.parse import urlparse
    # Config lives client-side; we can only return the path + creds and let
    # the app build the absolute URL from its own baseUrl host. The relay
    # listens on port 8001 of the SAME host that relays :8000 -> API.
    return {"ok": True, "port": 8001, "credential": cred,
            "path": "/", "note": "open http://<server-host>:8001 in-app; basic auth"}


class EmergencyBody(BaseModel):
    reason: str = ""


@router.post("/emergency/stop")
async def app_emergency_stop(body: EmergencyBody, dev: dict = Depends(_require_device)):
    """Kill switch from the phone — same path as the CC emergency panel:
    tool switch + scheduler pause + running-mission cancel. Audited with
    the device label so the log shows WHERE the stop came from."""
    from core.kai_emergency import check_rate, stopped_info, emergency_stop
    allowed, retry_after = check_rate(dev.get("label", "mobile"))
    if not allowed:
        raise HTTPException(429, f"rate limited, retry in {retry_after}s")
    result = emergency_stop(operator=f"mobile:{dev.get('label', '?')}",
                            reason=body.reason or "app emergency stop")
    return {"ok": True, **result}


@router.post("/emergency/resume")
async def app_emergency_resume(dev: dict = Depends(_require_device)):
    from core.kai_emergency import emergency_resume
    result = emergency_resume(operator=f"mobile:{dev.get('label', '?')}")
    return {"ok": True, **result}


@router.get("/emergency/status")
async def app_emergency_status(dev: dict = Depends(_require_device)):
    from core.kai_emergency import stopped_info
    info = stopped_info()
    paused = False
    try:
        from core.scheduler import SCHEDULER_PAUSE_FILE
        paused = SCHEDULER_PAUSE_FILE.exists()
    except Exception:
        pass
    return {"stopped": bool(info.get("stopped")), "scheduler_paused": paused,
            "by": info.get("by"), "reason": info.get("reason"), "at": info.get("at")}

