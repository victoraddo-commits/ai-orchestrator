"""Kai Control Center commands for Telegram.

Every command routes through the local FastAPI (https://127.0.0.1:8000) so
Telegram is another *interface* into the same backend, never a second
implementation. Business logic never lives here.

Recognized text (case-insensitive, leading slash optional):
  /help /commands     — list recognized commands
  /status             — orchestrator + provider health + counts
  /network            — network overview (sites, peers, tunnel, connectivity)
  /vpn                — VPN failover / tunnel health
  /missions           — mission engine snapshot (if wired)
  /approvals          — pending approvals
  /health             — quick system health

Returns None if the message is not a recognized control command (caller
should fall through to the next router).
"""
from __future__ import annotations

import json
import ssl
from typing import Any, Callable
from urllib import request as _urlreq
from urllib.error import HTTPError, URLError

# Local FastAPI is the SAME backend the web Command Center uses.
_API_BASE = "https://127.0.0.1:8000"
_TIMEOUT = 8.0
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def _api(path: str) -> tuple[int, Any]:
    req = _urlreq.Request(f"{_API_BASE}{path}", method="GET",
                          headers={"accept": "application/json"})
    try:
        with _urlreq.urlopen(req, timeout=_TIMEOUT, context=_SSL_CTX) as resp:
            body = resp.read().decode() or "{}"
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError:
                return resp.status, {"raw": body[:2000]}
    except HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {"error": str(e)}
    except URLError as e:
        return 0, {"error": f"unreachable: {e.reason}"}
    except Exception as e:
        return 0, {"error": str(e)}


def _fmt_status() -> str:
    """Aggregate status from multiple endpoints."""
    lines = ["*KAI STATUS*", ""]

    # Command center summary
    code, cc = _api("/api/command-center/summary")
    if code == 200 and isinstance(cc, dict):
        wf = cc.get("workforce") or {}
        pending = cc.get("pending_approvals") or cc.get("approvals") or []
        active = cc.get("active_builds") or cc.get("builds") or []
        lines.append(f"providers: {len(wf)}")
        lines.append(f"active builds: {len(active) if isinstance(active, list) else '?'}")
        lines.append(f"pending approvals: {len(pending) if isinstance(pending, list) else '?'}")
    else:
        lines.append(f"summary: unavailable (HTTP {code})")

    # Provider health
    code, providers = _api("/v1/providers")
    if code == 200 and isinstance(providers, dict):
        rows = providers.get("providers") or providers.get("data") or []
        ok = sum(1 for p in rows if (p.get("status") or "").lower() in ("ok", "healthy", "active"))
        lines.append(f"providers healthy: {ok}/{len(rows)}")

    # Network health
    code, net = _api("/api/network/overview")
    if code == 200 and isinstance(net, dict):
        tunnel = (net.get("tunnel") or {}).get("status", "?")
        health = net.get("health", "?")
        lines.append(f"network: {health}  tunnel: {tunnel}")

    lines.append("")
    lines.append("commands: /network /vpn /missions /approvals /health /help")
    return "\n".join(lines)


def _fmt_network() -> str:
    code, d = _api("/api/network/overview")
    if code != 200 or not isinstance(d, dict):
        return f"network overview unavailable (HTTP {code}): {d}"
    lines = ["*NETWORK*", ""]
    tunnel = d.get("tunnel") or {}
    lines.append(f"tunnel: {tunnel.get('status','?')}")
    a2b = tunnel.get("a_to_b_latency_ms")
    b2a = tunnel.get("b_to_a_latency_ms")
    lines.append(f"latency A→B: {a2b if a2b is not None else '—'} ms   B→A: {b2a if b2a is not None else '—'} ms")
    lines.append(f"packet loss: {tunnel.get('packet_loss_pct', 0)}%")
    lines.append("")
    conn = d.get("connectivity") or {}
    lines.append("connectivity:")
    for k, v in conn.items():
        mark = "✓" if v == "PASS" else "✗" if v == "FAIL" else "?"
        lines.append(f"  {mark} {k}: {v}")
    lines.append("")
    sites = d.get("sites") or []
    lines.append(f"sites ({len(sites)}):")
    for s in sites:
        pm = s.get("proxmox") or {}
        online = "up" if pm.get("online") else "down" if pm.get("online") is False else "?"
        lines.append(f"  {s.get('name'):>8}  {online}  {s.get('lan_subnet') or '—'}  ({pm.get('name') or '—'})")
    peers = d.get("peers") or []
    lines.append("")
    lines.append(f"tailscale peers: {len(peers)}")
    for p in peers[:5]:
        online = "up" if p.get("online") else "down" if p.get("online") is False else "?"
        lines.append(f"  {p.get('name'):>16}  {online}  {p.get('ip') or '—'}")
    warns = d.get("warnings") or []
    if warns:
        lines.append("")
        lines.append("warnings:")
        for w in warns:
            lines.append(f"  ! {w}")
    lines.append("")
    lines.append(f"overall: {d.get('health','?')}   discovery: {d.get('last_discovery') or '—'}")
    return "\n".join(lines)


def _fmt_vpn() -> str:
    code, d = _api("/api/vpn/status")
    if code != 200 or not isinstance(d, dict):
        return f"vpn status unavailable (HTTP {code}): {d}"
    lines = ["*VPN*", ""]
    lines.append(f"host: {d.get('host','?')}:{d.get('port','?')}")
    lines.append(f"reachable: {d.get('reachable')}")
    lines.append(f"checked: {d.get('checked_at','?')}")
    nodes = d.get("nodes") or []
    if nodes:
        lines.append("")
        lines.append(f"nodes ({len(nodes)}):")
        for n in nodes[:10]:
            lines.append(f"  {n}")
    return "\n".join(lines)


def _fmt_missions() -> str:
    for path in ("/kai/missions", "/api/missions", "/kai/missions/list"):
        code, d = _api(path)
        if code == 200:
            if isinstance(d, dict) and "missions" in d:
                items = d["missions"]
            elif isinstance(d, list):
                items = d
            else:
                items = []
            if not items:
                return "*MISSIONS*\n\nno missions found"
            lines = [f"*MISSIONS ({len(items)})*", ""]
            for m in items[:10]:
                lines.append(f"  {m.get('id','?')[:12]}  {m.get('status','?'):<12}  {m.get('title','?')[:50]}")
            return "\n".join(lines)
    return "missions endpoint not available (KX1 Mission Engine may not be wired to api.py)"


def _fmt_approvals() -> str:
    for path in ("/kai/approvals", "/api/approvals", "/kai/approvals/pending"):
        code, d = _api(path)
        if code == 200:
            if isinstance(d, dict) and "approvals" in d:
                items = d["approvals"]
            elif isinstance(d, list):
                items = d
            else:
                items = []
            pending = [i for i in items if (i.get("status") or "").lower() == "pending"]
            if not pending:
                return "*APPROVALS*\n\nno pending approvals"
            lines = [f"*PENDING APPROVALS ({len(pending)})*", ""]
            for a in pending[:10]:
                lines.append(f"  {a.get('id','?')[:8]}  {a.get('action','?'):<24}  {a.get('service','?')}")
            return "\n".join(lines)
    # Fall back to reading the local queue
    try:
        with open("memory/approval_queue.json") as f:
            q = json.load(f)
        items = q.get("records") if isinstance(q.get("records"), list) else list((q.get("records") or {}).values())
        pending = [i for i in items if (i.get("status") or "").lower() == "pending"]
        if not pending:
            return "*APPROVALS*\n\nno pending approvals"
        lines = [f"*PENDING APPROVALS ({len(pending)})*", ""]
        for a in pending[:10]:
            lines.append(f"  {a.get('id','?')[:8]}  {a.get('action','?'):<24}  {a.get('service','?')}")
        return "\n".join(lines)
    except Exception as e:
        return f"approvals unavailable: {e}"


def _fmt_health() -> str:
    lines = ["*HEALTH*", ""]
    # Try healthz
    for path in ("/api/healthz", "/healthz", "/api/health", "/health"):
        code, d = _api(path)
        if code == 200:
            lines.append(f"api: ok ({path})")
            if isinstance(d, dict):
                for k, v in list(d.items())[:6]:
                    lines.append(f"  {k}: {v}")
            break
    else:
        lines.append("api healthz: no endpoint found")

    # Provider health
    code, providers = _api("/v1/providers")
    if code == 200 and isinstance(providers, dict):
        rows = providers.get("providers") or providers.get("data") or []
        ok = sum(1 for p in rows if (p.get("status") or "").lower() in ("ok", "healthy", "active"))
        lines.append(f"providers: {ok}/{len(rows)} healthy")

    # Network health
    code, net = _api("/api/network/overview")
    if code == 200 and isinstance(net, dict):
        lines.append(f"network: {net.get('health','?')}   tunnel: {(net.get('tunnel') or {}).get('status','?')}")
    return "\n".join(lines)


def _fmt_help() -> str:
    return (
        "*KAI CONTROL CENTER*\n\n"
        "/status      orchestrator + provider + network summary\n"
        "/network     sites, peers, tunnel, connectivity\n"
        "/vpn         VPN failover status\n"
        "/missions    active missions (Mission Engine)\n"
        "/approvals   pending approvals\n"
        "/health      system health\n"
        "/help        this list\n\n"
        "money commands: /menu /pending /treasury /ops /wallets /payouts /risk\n"
        "build approvals: reply `approve` or `reject` to any pending build\n"
        "anything else routes to Kai (natural language)"
    )


# ── command registry ────────────────────────────────────────────────────────

_HANDLERS: dict[str, Callable[[], str]] = {
    "/status": _fmt_status,
    "/network": _fmt_network,
    "/vpn": _fmt_vpn,
    "/missions": _fmt_missions,
    "/approvals": _fmt_approvals,
    "/health": _fmt_health,
    "/help": _fmt_help,
    "/commands": _fmt_help,
}


def handle_control_command(text: str, *, via_bus: bool = False,
                            source: str = "telegram", user: str = "operator") -> str | None:
    """Return a reply string if the message is a recognized control command,
    else None so the caller can fall through to natural-language routing.

    When called via the Telegram bridge with `via_bus=True`, dispatch goes
    through the KAI Command Bus so AgentGuard authorization + audit fires.
    The bus's registered handlers call back into this function with
    `via_bus=False` to actually produce the reply string.
    """
    if not text:
        return None
    t = text.strip().lower()
    if "@" in t:
        parts = t.split()
        t = " ".join(p for p in parts if not p.startswith("@"))
    first = t.split()[0] if t.split() else ""
    if not first.startswith("/"):
        if first in ("status", "network", "vpn", "missions", "approvals", "health", "help", "commands"):
            first = "/" + first
        else:
            return None
    handler = _HANDLERS.get(first)
    if handler is None:
        return None

    if via_bus:
        # Route through the Command Bus so AgentGuard runs and every command
        # produces an audit event. The bus's own handler calls this same
        # function with via_bus=False, invoking `handler()` below.
        try:
            from core.command_bus import get_bus
            bus = get_bus()
            result = bus.dispatch(first, params={"text": text},
                                  source=source, user=user)
            if result.get("status") == "success":
                return result.get("data")
            return f"{first} refused: {result.get('message', 'denied')}"
        except Exception as e:
            return f"command bus error: {e}"

    try:
        return handler()
    except Exception as e:
        return f"command {first} failed: {e}"
