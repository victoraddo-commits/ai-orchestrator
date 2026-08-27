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

# --- shared data-gathering helpers -------------------------------------------
# These are used by both the device-token-gated /kai/app/* routes below and
# the public /mobile/api/* routes in core.mobile_launcher_routes. Keep them
# pure (no auth/Header deps) so they can be called from anywhere.

def gather_home_payload() -> dict:
    """Executive summary + world + data-trust for the Home surface."""
    from core.kai_executive import prioritize
    from core.world_model import get_state
    return {"executive": prioritize(), "world": get_state(),
            "data_trust": _data_age_minutes()}


def gather_proxmox_payload() -> dict:
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


def gather_missions_payload() -> dict:
    from core.kai_missions import list_missions
    return {"missions": list_missions()}


def gather_enhancements_payload() -> dict:
    from core.kai_enhancements import status
    return {"enhancements": status()}


def gather_wg_peers_payload() -> dict:
    """Live WG peer list from DD-WRT (via the tool's telnet path)."""
    import os
    os.chdir("/project/ai-orchestrator")
    from core.kai_tools import builtin
    try:
        show = builtin._ddwrt_telnet("wg show wg0 peers")
        return {"ok": True, "raw": show[:2000]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# --- /kai/app/* device-token-gated routes -----------------------------------

@router.get("/home")
async def app_home(dev: dict = Depends(_require_device)):
    """Everything for the Home screen: executive summary + world + modules."""
    return gather_home_payload()


@router.get("/proxmox")
async def app_proxmox(dev: dict = Depends(_require_device)):
    return gather_proxmox_payload()


@router.get("/missions")
async def app_missions(dev: dict = Depends(_require_device)):
    return gather_missions_payload()


@router.get("/enhancements")
async def app_enhancements(dev: dict = Depends(_require_device)):
    return gather_enhancements_payload()


@router.get("/wg/peers")
async def app_wg_peers(dev: dict = Depends(_require_device)):
    return gather_wg_peers_payload()


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


def gather_vision_payload(image_b64: str, question: str) -> dict:
    """KAI EYES: analyze an image (camera capture or screenshot) through the
    vision model. Uses the same Gemini path as kai.vision.analyze_url."""
    import base64 as b64
    try:
        png = b64.b64decode(image_b64)
    except Exception:
        raise HTTPException(400, "image_b64 must be base64")
    if len(png) < 100:
        raise HTTPException(400, "image too small")
    if len(png) > 8_000_000:
        raise HTTPException(400, "image too large (max 8MB)")
    from core.kai_tools.builtin import _vision_ask
    result = _vision_ask(png, question)
    return {"ok": True, **result}


@router.post("/vision")
async def app_vision(body: VisionBody, dev: dict = Depends(_require_device)):
    return gather_vision_payload(body.image_b64, body.question)


def gather_capabilities_payload() -> dict:
    """§50 universal capability registry — what can JARVIS do right now?"""
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


def gather_briefing_payload(send: bool = False) -> dict:
    """§47: 'JARVIS, catch me up' — executive briefing (facts only)."""
    from core.kai_executive import run_briefing
    return {"briefing": run_briefing(kind="on-demand", send=bool(send))}


def gather_spend_payload(days: int = 30) -> dict:
    """Real AI spend for the app's Jarvis tab — cost tracker summary."""
    from core.ai.cost_tracker import get_cost_summary
    return get_cost_summary(max(1, min(days, 90)))


def gather_alerts_payload(limit: int = 10) -> dict:
    """Live alerts: counts by severity + recent N for the dashboard tile.

    Uses the same NotificationManager APIs as /kai/notifications (unread
    counts + list_notifications) so the mobile dashboard shows the same
    data the KAI Ultimate Android app and the /kai/notifications page
    surface. Returns errors as a dict so the sheet can show 'unavailable'
    instead of crashing the dashboard."""
    try:
        from core.notifications import NotificationManager
        # Unread counts (static method, returns dict with critical/important/
        # informational keys)
        try:
            counts = NotificationManager.unread_count() or {}
        except Exception:
            counts = {}
        # Normalize the shape
        counts = {
            "critical": int(counts.get("critical", 0) or 0),
            "important": int(counts.get("important", 0) or 0),
            "informational": int(counts.get("informational", 0) or 0),
        }
        # Recent N
        try:
            recent_raw = NotificationManager.list_notifications(limit=limit)
            # list_notifications returns a dict {total, notifications: [...]}
            if isinstance(recent_raw, dict):
                recent = recent_raw.get("notifications", [])
            else:
                recent = recent_raw or []
        except Exception:
            recent = []
        # Normalize to the {id, severity, title, body} shape the dashboard expects
        norm = []
        for n in recent[:limit]:
            if not isinstance(n, dict):
                continue
            norm.append({
                "id": n.get("id"),
                "severity": n.get("severity", "informational"),
                "title": n.get("title") or n.get("summary") or n.get("id", "(no title)"),
                "body": n.get("body") or n.get("message") or "",
                "source": n.get("source"),
                "module": n.get("module"),
                "acknowledged": bool(n.get("acknowledged") or n.get("acked")),
                "created_at": n.get("created_at") or n.get("timestamp"),
            })
        return {"counts": counts, "recent": norm, "limit": limit}
    except Exception as e:
        return {"counts": {"critical": 0, "important": 0, "informational": 0},
                "recent": [], "limit": limit, "error": str(e)[:120]}


def gather_terminal_payload() -> dict:
    """Terminal join: ttyd port + basic-auth credential for the phone.

    Also returns live status of the active claude-code session running in
    the shared tmux ('claude-cc') so the dashboard can show "session is
    running, X minutes uptime, Y CPU%" before the user opens the terminal.
    """
    import os as _os
    import subprocess as _sp
    cred_path = "/etc/default/kai-terminal-cred"
    if not _os.path.exists(cred_path):
        raise HTTPException(503, "terminal service not configured")
    cred = open(cred_path).read().strip()
    if ":" not in cred:
        raise HTTPException(503, "terminal credential malformed")
    # Detect the actual ttyd port from its process args (port may change)
    port = 7681
    try:
        out = _sp.run(["pgrep", "-f", "/usr/bin/ttyd"], capture_output=True, text=True, timeout=2).stdout.strip().splitlines()
        if out:
            cmdline = open(f"/proc/{out[0]}/cmdline", "rb").read().decode("utf-8", "ignore").split("\x00")
            for i, tok in enumerate(cmdline):
                if tok == "-p" and i + 1 < len(cmdline):
                    port = int(cmdline[i + 1])
                    break
    except Exception:
        pass
    # Active session: which tmux session + which claude binary is running
    session = {"running": False, "tmux_session": None, "claude_pid": None,
               "uptime_s": None, "cpu_pct": None, "started_at": None}
    try:
        out = _sp.run(["tmux", "list-sessions", "-F", "#{session_name}:#{session_created}:#{session_windows}"],
                      capture_output=True, text=True, timeout=2).stdout.strip()
        for line in out.splitlines():
            if line.startswith("claude-cc:"):
                parts = line.split(":", 2)
                if len(parts) >= 3:
                    import time as _t
                    session["tmux_session"] = parts[0]
                    session["started_at"] = int(parts[1])
                    session["uptime_s"] = int(_t.time()) - int(parts[1])
                    session["windows"] = int(parts[2])
                    session["running"] = True
                break
    except Exception:
        pass
    # Find the claude-code PID if any
    try:
        out = _sp.run(["pgrep", "-f", "claude"], capture_output=True, text=True, timeout=2).stdout.strip().splitlines()
        for pid_str in out:
            try:
                pid = int(pid_str)
                # /proc/PID/comm gives the process name
                comm = open(f"/proc/{pid}/comm", "rb").read().decode("utf-8", "ignore").strip()
                if "claude" in comm.lower():
                    stat = open(f"/proc/{pid}/stat", "rb").read().decode("utf-8", "ignore").split()
                    # fields: (1)pid (2)comm (3)state (4)ppid ... (22)starttime (in clock ticks)
                    session["claude_pid"] = pid
                    session["claude_comm"] = comm
                    clk_tck = _os.sysconf("SC_CLK_TCK")
                    boot_time = int(open("/proc/stat").read().split("btime ")[1].split()[0])
                    starttime_sec = boot_time + int(stat[21]) / clk_tck
                    session["claude_uptime_s"] = int(_t.time()) - int(starttime_sec)
                    break
            except (ValueError, OSError, IndexError):
                continue
    except Exception:
        pass
    return {"ok": True, "port": port, "credential": cred,
            "path": "/", "session": session,
            "note": "basic-auth in URL: http://user:pass@<host>:<port>/"}


def gather_emergency_status_payload() -> dict:
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


@router.get("/capabilities")
async def app_capabilities(dev: dict = Depends(_require_device)):
    return gather_capabilities_payload()


@router.get("/briefing")
async def app_briefing(dev: dict = Depends(_require_device), send: bool = False):
    return gather_briefing_payload(send=send)


@router.get("/spend")
async def app_spend(days: int = 30, dev: dict = Depends(_require_device)):
    return gather_spend_payload(days)


@router.get("/terminal")
async def app_terminal(dev: dict = Depends(_require_device)):
    return gather_terminal_payload()


class EmergencyBody(BaseModel):
    reason: str = ""


@router.post("/emergency/stop")
async def app_emergency_stop(body: EmergencyBody, dev: dict = Depends(_require_device)):
    """Kill switch from the phone — same path as the CC emergency panel:
    tool switch + scheduler pause + running-mission cancel. Audited with
    the device label so the log shows WHERE the stop came from."""
    from core.kai_emergency import check_rate, emergency_stop
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
    return gather_emergency_status_payload()



class AdbPortReport(BaseModel):
    adb_port: int


@router.post("/device/report-port")
async def app_report_port(body: AdbPortReport, dev: dict = Depends(_require_device)):
    """Port monitor: the phone reports its current wireless-debugging port
    whenever it changes. We relay it to the operator's Telegram so claude-code
    can `adb connect` any time without asking."""
    port = max(1024, min(65535, int(body.adb_port)))
    label = dev.get("label", "?")
    from core.telegram_bridge import send_message as tg
    try:
        tg(f"📱 adb port update — {label}: 10.8.0.8:{port}\n(connect: adb connect 10.8.0.8:{port})")
        relayed = True
    except Exception:
        relayed = False
    # also persist for polling
    import json as _json
    path = "/project/ai-orchestrator/memory/adb_ports.json"
    try:
        data = _json.load(open(path)) if _os_path_exists(path) else {}
    except Exception:
        data = {}
    data[label] = {"port": port, "ip": "10.8.0.8", "updated": int(time.time())}
    with open(path, "w") as fh:
        fh.write(_json.dumps(data))
    return {"ok": True, "relayed": relayed}


def _os_path_exists(p: str) -> bool:
    import os
    return os.path.exists(p)
