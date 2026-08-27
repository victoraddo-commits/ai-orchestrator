"""Kai Mobile Launcher — PWA launcher dashboard for the Galaxy S23 Ultra.

Part of: Kai Mobile Command Node — Sub-project 6: Module Launcher & App Shortcuts.

Serves a mobile-first PWA launcher page that organizes all Kai services
and homelab modules into a grid of launchable app tiles. Designed for
the Samsung Galaxy S23 Ultra (3088×1440, 6.8" display).

Routes:
- GET /mobile          — the launcher HTML page
- GET /mobile/manifest — PWA web app manifest
- GET /mobile/sw.js    — service worker (offline cache)
- GET /mobile/tiles    — tile data (services/modules with status)
"""

import ipaddress
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

logger = logging.getLogger(__name__)

router = APIRouter(tags=["mobile-launcher"])

# --- LAN / WireGuard source-IP guard ----------------------------------------
# /mobile/api/* exposes operator-dashboard data (proxmox inventory, AI spend,
# WireGuard peer list, mission queue, etc). The API is bound to 0.0.0.0:8000
# and the dashboard is reached from the operator's LAN/phone, so we MUST
# enforce source-IP allowlisting at the application layer — not just rely on
# the deployment topology. Any request whose client IP isn't in one of these
# CIDRs is rejected with 403 before the handler runs.

_LAN_CIDRS = [
    ipaddress.ip_network("127.0.0.0/8"),       # loopback (curl from claude-code)
    ipaddress.ip_network("10.0.0.0/8"),         # private (10.x LAN + 10.8.0.x WireGuard)
    ipaddress.ip_network("172.16.0.0/12"),      # private (docker bridge 172.17/18)
    ipaddress.ip_network("192.168.0.0/16"),     # private (192.168.1.x / 192.168.99.x LANs)
]


def _client_ip(request: Request) -> str:
    """Best-effort client IP. Trusts X-Forwarded-For ONLY if the direct peer
    is in the LAN CIDRs (i.e. the request came through a known reverse proxy
    on the LAN). Otherwise returns the TCP peer address — never trust
    attacker-supplied XFF headers from the public internet."""
    peer = (request.client.host or "") if request.client else ""
    # Starlette TestClient uses "testclient" as the host; treat as loopback
    if peer in ("", "testclient", "localhost"):
        return "127.0.0.1"
    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        return peer
    in_lan = any(peer_ip in c for c in _LAN_CIDRS)
    if not in_lan:
        # Don't honor XFF from outside the LAN — return the direct peer
        return peer
    xff = request.headers.get("x-forwarded-for")
    if xff:
        # left-most = original client
        return xff.split(",")[0].strip()
    return peer


async def _require_lan_source(request: Request):
    """Reject requests originating from outside the LAN/WireGuard CIDRs.
    Used as a Depends() on every /mobile/api/* read endpoint."""
    ip = _client_ip(request)
    try:
        ip_obj = ipaddress.ip_address(ip)
    except ValueError:
        raise HTTPException(403, f"forbidden: unparseable source {ip!r}")
    if not any(ip_obj in c for c in _LAN_CIDRS):
        logger.warning("mobile/api: rejected request from non-LAN source %s", ip)
        raise HTTPException(403, "forbidden: source IP not in LAN/WireGuard")
    return ip


async def _require_device_token(
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Hard auth on write actions (emergency stop, wg create). Reuses the
    device-token registry that the KAI Ultimate Android app already uses —
    so the dashboard can only file approvals when it's holding a paired
    device token. Truncates/length-checks user-supplied fields to prevent
    prompt-injection into operator-facing approval text."""
    from core.device_registry import find_device_by_token
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "missing device token (mobile/api write actions require it)")
    token = authorization[7:].strip()
    device_id = find_device_by_token(token)
    if not device_id:
        raise HTTPException(401, "invalid or revoked device token")
    return device_id


def _safe_field(value: str | None, max_len: int = 120) -> str:
    """Sanitize a user-supplied field before embedding in operator-facing text.
    Truncates, strips control chars, removes newlines that could break
    the single-line approval prompt."""
    if value is None:
        return ""
    s = str(value)
    s = "".join(c for c in s if c.isprintable() and c not in "\r\n\t")
    return s[:max_len].strip()

# ---------------------------------------------------------------------------
# Tile definitions — the services/modules shown on the launcher grid
# ---------------------------------------------------------------------------

TILES = [
    {
        "id": "kai-dashboard",
        "name": "Kai Dashboard",
        "description": "AI Orchestrator overview & approvals",
        "icon": "brain-circuit",
        "color": "#16A34A",
        "url": "/command-center",
        "type": "internal",
        "tags": ["ai", "core"],
    },
    {
        "id": "command-center",
        "name": "Command Center",
        "description": "Full ops control plane",
        "icon": "terminal",
        "color": "#3B82F6",
        "url": "/command-center",
        "type": "internal",
        "tags": ["ops", "core"],
    },
    {
        "id": "health",
        "name": "Health",
        "description": "System health & anomaly detection",
        "icon": "heart-pulse",
        "color": "#22C55E",
        "url": "/command-center",
        "type": "internal",
        "tags": ["monitoring", "core"],
    },
    {
        "id": "notifications",
        "name": "Notifications",
        "description": "Alerts, incidents & activity feed",
        "icon": "bell",
        "color": "#F59E0B",
        "url": "/kai/notifications",
        "type": "internal",
        "tags": ["monitoring"],
    },
    {
        "id": "wireguard",
        "name": "WireGuard",
        "description": "VPN tunnel status & management",
        "icon": "shield",
        "color": "#8B5CF6",
        "url": "/kai/wireguard/status",
        "type": "internal",
        "tags": ["network", "core"],
    },
    {
        "id": "it-manager",
        "name": "IT Manager",
        "description": "HR platform — workers, payroll, shifts",
        "icon": "users",
        "color": "#EC4899",
        "url": "http://192.168.99.11:8090",
        "type": "external",
        "tags": ["hr", "production"],
    },
    {
        "id": "susu",
        "name": "Susu",
        "description": "Microfinance — groups, contributions, payouts",
        "icon": "landmark",
        "color": "#14B8A6",
        "url": "http://192.168.1.111:8050",
        "type": "external",
        "tags": ["finance"],
    },
    {
        "id": "kai-betting",
        "name": "Kai Betting",
        "description": "Sports betting engine — picks, EV gate, sessions",
        "icon": "trophy",
        "color": "#DC2626",
        "url": "http://192.168.1.111:8096",
        "type": "external",
        "tags": ["betting", "production"],
    },
    {
        "id": "airdrop-hunter",
        "name": "Airdrop Hunter",
        "description": "Crypto airdrop monitor & alerts",
        "icon": "rocket",
        "color": "#F97316",
        "url": "http://192.168.99.11:8092",
        "type": "external",
        "tags": ["crypto"],
    },
    {
        "id": "proxdash",
        "name": "ProxDash",
        "description": "Homelab dashboard & resource monitor",
        "icon": "gauge",
        "color": "#06B6D4",
        "url": "http://192.168.1.114:8091",
        "type": "external",
        "tags": ["monitoring"],
    },
    {
        "id": "code-server",
        "name": "Code Server",
        "description": "VS Code in the browser",
        "icon": "code",
        "color": "#2563EB",
        "url": "http://192.168.99.11:8443",
        "type": "external",
        "tags": ["dev"],
    },
    {
        "id": "juris-kai",
        "name": "Juris Kai",
        "description": "Ghana legal corpus search & analysis",
        "icon": "scale",
        "color": "#D97706",
        "url": "/command-center#legal",
        "type": "internal",
        "tags": ["legal"],
    },
    {
        "id": "portfolio",
        "name": "Portfolio",
        "description": "Investment & asset tracker",
        "icon": "chart-line",
        "color": "#10B981",
        "url": "http://192.168.99.11:3000",
        "type": "external",
        "tags": ["finance"],
    },
    {
        "id": "money-center",
        "name": "Money Center",
        "description": "KAI Money Ecosystem — treasury, operations, KAI account",
        "icon": "banknote",
        "color": "#059669",
        "url": "http://192.168.1.118:8095",
        "type": "external",
        "tags": ["money", "production"],
    },
    {
        "id": "kai-vault",
        "name": "Kai Vault",
        "description": "Passkey login, secrets, identity, audit",
        "icon": "shield-check",
        "color": "#7C3AED",
        "url": "https://vault.sso.deerude.com",
        "type": "external",
        "tags": ["identity", "security", "core"],
    },
    {
        "id": "deerude",
        "name": "Deerude",
        "description": "Public site — ventures & careers portal",
        "icon": "globe",
        "color": "#0EA5E9",
        "url": "https://deerude.com",
        "type": "external",
        "tags": ["web", "public"],
    },
    # ── KAI Ultimate feature surfaces (v3.1.2 parity) ──────────────────────
    # type=feature tiles open a bottom sheet that fetches the endpoint.
    {
        "id": "feature-home",
        "name": "Home",
        "description": "Executive summary, priorities, world model",
        "icon": "home",
        "color": "#16A34A",
        "url": "/mobile/api/home",
        "endpoint": "/mobile/api/home",
        "type": "feature",
        "tags": ["core", "jarvis"],
    },
    {
        "id": "feature-proxmox",
        "name": "Proxmox",
        "description": "Nodes, containers, VMs, storage",
        "icon": "server",
        "color": "#3B82F6",
        "url": "/mobile/api/proxmox",
        "endpoint": "/mobile/api/proxmox",
        "type": "feature",
        "tags": ["infra", "monitoring"],
    },
    {
        "id": "feature-missions",
        "name": "Missions",
        "description": "Active and queued work items",
        "icon": "list-tree",
        "color": "#8B5CF6",
        "url": "/mobile/api/missions",
        "endpoint": "/mobile/api/missions",
        "type": "feature",
        "tags": ["ops"],
    },
    {
        "id": "feature-briefing",
        "name": "Catch me up",
        "description": "JARVIS executive briefing (facts only)",
        "icon": "sunrise",
        "color": "#F59E0B",
        "url": "/mobile/api/briefing",
        "endpoint": "/mobile/api/briefing",
        "type": "feature",
        "tags": ["jarvis"],
    },
    {
        "id": "feature-capabilities",
        "name": "What Kai can do",
        "description": "Live tool/capability registry",
        "icon": "list-checks",
        "color": "#22C55E",
        "url": "/mobile/api/capabilities",
        "endpoint": "/mobile/api/capabilities",
        "type": "feature",
        "tags": ["jarvis", "core"],
    },
    {
        "id": "feature-spend",
        "name": "AI Spend",
        "description": "Real AI cost tracker (last 30 days)",
        "icon": "dollar-sign",
        "color": "#10B981",
        "url": "/mobile/api/spend?days=30",
        "endpoint": "/mobile/api/spend?days=30",
        "type": "feature",
        "tags": ["finance", "jarvis"],
        "actions": [{"label": "7d", "endpoint": "/mobile/api/spend?days=7", "method": "GET"},
                    {"label": "30d", "endpoint": "/mobile/api/spend?days=30", "method": "GET"},
                    {"label": "90d", "endpoint": "/mobile/api/spend?days=90", "method": "GET"}],
    },
    {
        "id": "feature-emergency",
        "name": "Emergency",
        "description": "Kill switch + scheduler pause (approval-gated)",
        "icon": "alert-octagon",
        "color": "#EF4444",
        "url": "/mobile/api/emergency/status",
        "endpoint": "/mobile/api/emergency/status",
        "type": "feature",
        "tags": ["core", "control"],
        "actions": [{"label": "STOP", "endpoint": "/mobile/api/emergency/stop",
                     "method": "POST", "confirm": "File emergency-stop approval?",
                     "body": {"reason": "mobile dashboard tap"}},
                    {"label": "RESUME", "endpoint": "/mobile/api/emergency/resume",
                     "method": "POST", "confirm": "File emergency-resume approval?",
                     "body": {}}],
    },
    {
        "id": "feature-wg",
        "name": "WireGuard",
        "description": "Live peers + create-peer (approval-gated)",
        "icon": "network",
        "color": "#7C3AED",
        "url": "/mobile/api/wg/peers",
        "endpoint": "/mobile/api/wg/peers",
        "type": "feature",
        "tags": ["network", "core"],
        "actions": [{"label": "Create peer", "endpoint": "/mobile/api/wg/create",
                     "method": "POST", "prompt": "Peer label", "body_field": "label"}],
    },
    {
        "id": "feature-enhancements",
        "name": "Enhancements",
        "description": "Toggle Kai capability enhancements",
        "icon": "wrench",
        "color": "#06B6D4",
        "url": "/mobile/api/enhancements",
        "endpoint": "/mobile/api/enhancements",
        "type": "feature",
        "tags": ["jarvis", "core"],
    },
    {
        "id": "feature-claude-terminal",
        "name": "Claude Terminal",
        "description": "Live ttyd session with the active claude-code",
        "icon": "terminal",
        "color": "#0EA5E9",
        "url": "/mobile/api/terminal",
        "endpoint": "/mobile/api/terminal",
        "type": "feature",
        "tags": ["dev", "core"],
        "actions": [{"label": "Open terminal", "kind": "open-terminal"}],
    },
    {
        "id": "feature-alerts",
        "name": "Alerts",
        "description": "Critical / important notifications feed",
        "icon": "bell",
        "color": "#F59E0B",
        "url": "/mobile/api/alerts",
        "endpoint": "/mobile/api/alerts",
        "type": "feature",
        "tags": ["monitoring", "core"],
    },
]


# ---------------------------------------------------------------------------
# Route: Launcher HTML page
# ---------------------------------------------------------------------------

@router.get("/mobile", response_class=HTMLResponse)
async def mobile_launcher(request: Request):
    """Serve the PWA launcher dashboard. No-cache so a hard refresh always
    picks up the latest tile list and JS (the service worker handles its
    own caching for the static assets)."""
    return Response(
        content=_LAUNCHER_HTML,
        media_type="text/html",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@router.get("/mobile/", response_class=HTMLResponse)
async def mobile_launcher_slash(request: Request):
    """Redirect /mobile/ to /mobile."""
    return Response(
        content=_LAUNCHER_HTML,
        media_type="text/html",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


# ---------------------------------------------------------------------------
# Route: PWA manifest
# ---------------------------------------------------------------------------

_MANIFEST = {
    "name": "Kai Mobile Command",
    "short_name": "Kai",
    "description": "Kai AI Orchestrator — Mobile Command Node",
    "start_url": "/mobile",
    "display": "standalone",
    "background_color": "#020617",
    "theme_color": "#16A34A",
    "orientation": "portrait-primary",
    "icons": [
        {
            "src": "/mobile/icon-192",
            "sizes": "192x192",
            "type": "image/svg+xml",
            "purpose": "any maskable",
        },
        {
            "src": "/mobile/icon-512",
            "sizes": "512x512",
            "type": "image/svg+xml",
            "purpose": "any maskable",
        },
    ],
    "categories": ["developer tools", "utilities", "productivity"],
}


@router.get("/mobile/manifest")
async def mobile_manifest():
    """PWA web app manifest."""
    return JSONResponse(_MANIFEST)


# ---------------------------------------------------------------------------
# Route: PWA icons (inline SVG — no external assets needed)
# ---------------------------------------------------------------------------

_KAI_ICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">'
    '<rect width="512" height="512" rx="96" fill="#020617"/>'
    '<circle cx="200" cy="200" r="60" fill="none" stroke="#16A34A" stroke-width="18"/>'
    '<circle cx="312" cy="200" r="60" fill="none" stroke="#16A34A" stroke-width="18"/>'
    '<path d="M140 300 Q256 420 372 300" fill="none" stroke="#16A34A" stroke-width="18" stroke-linecap="round"/>'
    '<line x1="256" y1="188" x2="256" y2="112" stroke="#16A34A" stroke-width="14" stroke-linecap="round"/>'
    '<circle cx="256" cy="96" r="16" fill="#22C55E"/>'
    '</svg>'
)


@router.get("/mobile/icon-192")
async def icon_192():
    """192x192 PWA icon."""
    return Response(content=_KAI_ICON_SVG, media_type="image/svg+xml")


@router.get("/mobile/icon-512")
async def icon_512():
    """512x512 PWA icon."""
    return Response(content=_KAI_ICON_SVG, media_type="image/svg+xml")


# ---------------------------------------------------------------------------
# Route: Service Worker
# ---------------------------------------------------------------------------

_SW_JS = r"""
// kai-launcher-v2: bumped 2026-08-26 to drop the cache-first HTML strategy
// that was serving stale tiles. The HTML now always goes to the network
// (with cache fallback for offline). Icons/manifest remain cache-first.
const CACHE = 'kai-launcher-v2';
const ASSETS = [
  '/mobile/manifest',
  '/mobile/icon-192',
  '/mobile/icon-512',
];
// Never cache the page shell or any /mobile/api/* JSON — they must be live.
const NETWORK_ONLY = [/^\/mobile$/, /^\/mobile\/api\//];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(cache => cache.addAll(ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);
  // Always-fresh: page shell + API JSON
  if (NETWORK_ONLY.some(rx => rx.test(url.pathname))) {
    e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
    return;
  }
  // Cache-first for static assets (icons, manifest, sw.js itself)
  e.respondWith(
    caches.match(e.request).then(cached =>
      cached || fetch(e.request).then(resp => {
        if (resp.ok) {
          const clone = resp.clone();
          caches.open(CACHE).then(cache => cache.put(e.request, clone));
        }
        return resp;
      })
    )
  );
});
"""


@router.get("/mobile/sw.js")
async def service_worker():
    """Service worker for offline caching."""
    return Response(content=_SW_JS, media_type="application/javascript")


# ---------------------------------------------------------------------------
# Route: Tile data (API for live status badges)
# ---------------------------------------------------------------------------

@router.get("/mobile/tiles")
async def mobile_tiles():
    """Return tile definitions with live status where available."""
    # Try to enrich tiles with current health/status data
    try:
        from core.wireguard_manager import check_tunnel_to_proxmox_b
        wg_status = check_tunnel_to_proxmox_b()
    except Exception:
        wg_status = None

    tiles = []
    for tile in TILES:
        t = dict(tile)
        t["status"] = "unknown"

        # WireGuard status
        if tile["id"] == "wireguard" and wg_status:
            t["status"] = "ok" if wg_status.get("ok") else "critical"

        # Check if health worker is running
        if tile["id"] == "health":
            try:
                from core.health_worker import get_worker
                w = get_worker()
                t["status"] = "ok" if w and w.is_running else "warning"
                if w:
                    t["subtitle"] = f"{w.sample_count} samples"
            except Exception:
                pass

        tiles.append(t)

    return {
        "tiles": tiles,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# /mobile/api/* — public read-only + write-action endpoints for the mobile
# dashboard. These mirror the device-token-gated /kai/app/* routes in
# core.kai_app_api.py so a LAN browser (no device token) can show every
# KAI Ultimate feature surface. Write actions still go through the approval
# flow — the public dashboard never mutates state directly.
# ---------------------------------------------------------------------------

@router.get("/mobile/api/home")
async def mobile_api_home(_: str = Depends(_require_lan_source)):
    """Executive summary + world + data-trust — for the Home tile."""
    from core.kai_app_api import gather_home_payload
    return gather_home_payload()


@router.get("/mobile/api/proxmox")
async def mobile_api_proxmox(_: str = Depends(_require_lan_source)):
    """Proxmox nodes + containers + VMs + storage — for the Proxmox tile."""
    from core.kai_app_api import gather_proxmox_payload
    return gather_proxmox_payload()


@router.get("/mobile/api/missions")
async def mobile_api_missions(_: str = Depends(_require_lan_source)):
    """Active/queued missions — for the Missions tile."""
    from core.kai_app_api import gather_missions_payload
    return gather_missions_payload()


@router.get("/mobile/api/enhancements")
async def mobile_api_enhancements(_: str = Depends(_require_lan_source)):
    """Public enhancement status (read-only)."""
    from core.kai_app_api import gather_enhancements_payload
    return gather_enhancements_payload()


@router.get("/mobile/api/briefing")
async def mobile_api_briefing(send: bool = False, _: str = Depends(_require_lan_source)):
    """'JARVIS, catch me up' — executive briefing (facts only)."""
    from core.kai_app_api import gather_briefing_payload
    return gather_briefing_payload(send=send)


@router.get("/mobile/api/capabilities")
async def mobile_api_capabilities(_: str = Depends(_require_lan_source)):
    """§50 universal capability registry — what can JARVIS do right now."""
    from core.kai_app_api import gather_capabilities_payload
    return gather_capabilities_payload()


@router.get("/mobile/api/spend")
async def mobile_api_spend(days: int = 30, _: str = Depends(_require_lan_source)):
    """Real AI spend summary — for the Spend tile."""
    from core.kai_app_api import gather_spend_payload
    return gather_spend_payload(days)


@router.get("/mobile/api/emergency/status")
async def mobile_api_emergency_status(_: str = Depends(_require_lan_source)):
    """Current emergency-stop state (read-only)."""
    from core.kai_app_api import gather_emergency_status_payload
    return gather_emergency_status_payload()


@router.post("/mobile/api/emergency/stop")
async def mobile_api_emergency_stop(body: dict = Body(default={}),
                                    device_id: str = Depends(_require_device_token)):
    """File an emergency-stop approval. Requires a paired device token
    (the same one the KAI Ultimate Android app uses) — the LAN guard alone
    isn't enough for state-mutating actions. Reason is sanitized before
    being embedded in the operator-facing approval prompt."""
    from core.kai_tools.policy import request_approval
    raw_reason = (body or {}).get("reason", "mobile dashboard emergency stop")
    reason = _safe_field(raw_reason, max_len=200)
    rid = request_approval("kai.emergency.stop", {"source": f"mobile-dashboard:{device_id}"},
                           f"Mobile dashboard ({device_id}): emergency stop — {reason or 'no reason'}")
    if rid is None:
        raise HTTPException(503, "could not file emergency-stop approval")
    return {"ok": True, "approval_id": rid,
            "note": "operator must approve; the kill switch is gated"}


@router.post("/mobile/api/emergency/resume")
async def mobile_api_emergency_resume(body: dict = Body(default={}),
                                      device_id: str = Depends(_require_device_token)):
    """File an emergency-resume approval. Requires a paired device token."""
    from core.kai_tools.policy import request_approval
    rid = request_approval("kai.emergency.resume", {"source": f"mobile-dashboard:{device_id}"},
                           f"Mobile dashboard ({device_id}): emergency resume")
    if rid is None:
        raise HTTPException(503, "could not file emergency-resume approval")
    return {"ok": True, "approval_id": rid,
            "note": "operator must approve"}


@router.get("/mobile/api/wg/peers")
async def mobile_api_wg_peers(_: str = Depends(_require_lan_source)):
    """Live WireGuard peer list (read-only)."""
    from core.kai_app_api import gather_wg_peers_payload
    return gather_wg_peers_payload()


@router.get("/mobile/api/terminal")
async def mobile_api_terminal(_: str = Depends(_require_lan_source)):
    """Claude Code terminal join: ttyd port + basic-auth + live session
    status (tmux session name, uptime, claude PID). The dashboard sheet
    shows the active session is running and gives a button that opens
    the ttyd URL with credentials pre-baked."""
    from core.kai_app_api import gather_terminal_payload
    return gather_terminal_payload()


@router.get("/mobile/api/wallet")
async def mobile_api_wallet(_: str = Depends(_require_lan_source)):
    """KAI money-module wallet state — one-stop inspection surface.

    Used by the KAI Mobile "Money" tile and the autonomous agents to
    see the full state in one call:
      - on-chain balances (ETH + USDT + USDC + tx count, last check time)
      - monitor: is it running, baseline, delta, last alert
      - master treasury: balance + funded + earned
      - pending capital requests
      - auto-sweep: enabled + last sweep + total swept

    Aggregates data from money-center + the wallet monitor state file.
    """
    import os
    from pathlib import Path
    from datetime import datetime, timezone

    out = {
        "address": "0xa854EdEd5e1211Cb42bD28Ea53e4424Fa27ebaDd",
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }

    # Try to read the monitor state file (same path the monitor writes to).
    state_path = Path(os.environ.get("KAI_WALLET_STATE_DIR", "/var/lib/kai")) / "wallet_monitor_state.json"
    if state_path.exists():
        try:
            import json as _json
            monitor_state = _json.loads(state_path.read_text())
            out["monitor"] = {
                "running": True,
                "baseline_at": monitor_state.get("baseline_at"),
                "baseline_balance": monitor_state.get("last_balance"),
                "last_alert_at": monitor_state.get("last_alert_at"),
                "delta_from_baseline": None,  # computed below
                "watch_list_count": len(monitor_state.get("watch_list", [])),
                "alerts_history_count": len(monitor_state.get("alerts_history", [])),
                "state_path": str(state_path),
            }
            # Try to compute a fresh on-chain delta via blockscout (one-shot)
            try:
                import urllib.request as _ur, json as _j
                _addr = out["address"]
                _h = {"user-agent": "kai-mobile/1.0"}
                # ETH balance
                _r = _ur.Request("https://eth.blockscout.com/api?module=account&action=balance&address=" + _addr, headers=_h)
                with _ur.urlopen(_r, timeout=5) as _resp:
                    _d = _j.loads(_resp.read())
                _eth = int(_d.get("result", 0)) / 1e18
                # USDT balance (6 decimals)
                _r = _ur.Request(
                    "https://eth.blockscout.com/api?module=account&action=tokenbalance"
                    "&contractaddress=0xdAC17F958D2ee523a2206206994597C13D831ec7&address=" + _addr,
                    headers=_h)
                with _ur.urlopen(_r, timeout=5) as _resp:
                    _d = _j.loads(_resp.read())
                _usdt_raw = int(_d.get("result", 0))
                # USDT raw is 6-decimals → /1e6 = human-readable USD
                _usdt = _usdt_raw / 1e6
                cur = {"eth": _eth, "usdt": _usdt, "usdc": 0, "tx_count": 0}
                out["on_chain"] = cur
                if out["monitor"].get("baseline_balance"):
                    base = out["monitor"]["baseline_balance"]
                    # If baseline stored usdt as float, compare floats; if
                    # raw int, compare ints. We don't know which, so
                    # normalize: treat baseline as USD (float).
                    base_usd = base.get("usdt", 0)
                    # If baseline is huge (looks like raw), divide by 1e6
                    if base_usd > 1_000_000:  # 1M USDT = implausibly large
                        base_usd = base_usd / 1e6
                    base_eth = base.get("eth", 0)
                    out["monitor"]["delta_from_baseline"] = {
                        "eth": cur["eth"] - base_eth,
                        "usdt": round(cur["usdt"] - base_usd, 6),
                        "usdc": cur["usdc"] - base.get("usdc", 0),
                    }
            except Exception as e:
                out["on_chain_error"] = str(e)
        except Exception as e:
            out["monitor"] = {"running": False, "error": str(e)}
    else:
        out["monitor"] = {
            "running": False,
            "state_path": str(state_path),
            "note": "monitor not running or state file missing",
        }

    # Master treasury + pending capital requests via money-center's own
    # endpoints (we proxy to localhost; the kai_viewer token has read-only
    # access to /treasury/summary and /capital-requests).
    out["master_treasury"] = {"balance": 0, "funded": 0, "earned": 0}
    out["pending_capital_requests"] = []
    try:
        import httpx  # noqa: F401
        # Localhost proxy to money-center (same docker network on claude-code,
        # or via the KAI_MC_URL env if set)
        mc_url = os.environ.get("KAI_MC_URL", "http://192.168.1.118:8095")
        viewer_tok = None
        viewer_tok_path = "/root/.credentials/money-viewer-token"
        if os.path.exists(viewer_tok_path):
            viewer_tok = open(viewer_tok_path).read().strip()
        headers = {"authorization": f"Bearer {viewer_tok}"} if viewer_tok else {}
        import urllib.request, json as _json
        # /treasury/summary
        try:
            req = urllib.request.Request(f"{mc_url}/treasury/summary", headers=headers)
            with urllib.request.urlopen(req, timeout=5) as r:
                d = _json.loads(r.read())
                master = d.get("treasury", {}).get("master", {})
                out["master_treasury"] = {
                    "balance": float(master.get("balance", 0)),
                    "funded": float(master.get("funded", 0)),
                    "earned": float(master.get("earned", 0)),
                }
        except Exception as e:
            out["master_treasury_error"] = str(e)
        # /capital-requests?status=pending
        try:
            req = urllib.request.Request(f"{mc_url}/capital-requests?status=pending", headers=headers)
            with urllib.request.urlopen(req, timeout=5) as r:
                d = _json.loads(r.read())
                out["pending_capital_requests"] = [
                    {"id": cr.get("id"), "operation_slug": cr.get("operation_slug"),
                     "amount": float(cr.get("amount", 0)), "status": cr.get("status"),
                     "blocked_on": "master headroom" if out["master_treasury"]["balance"] == 0
                                  else "operator review"}
                    for cr in (d if isinstance(d, list) else [])
                ]
        except Exception as e:
            out["pending_capital_requests_error"] = str(e)
    except Exception as e:
        out["money_center_error"] = str(e)

    # Auto-sweep state
    sweeper_path = Path(os.environ.get("KAI_WALLET_STATE_DIR", "/var/lib/kai")) / "wallet_sweeper_state.json"
    out["auto_sweep"] = {
        "enabled": os.environ.get("KAI_AUTO_SWEEP", "0") == "1",
        "state_path": str(sweeper_path),
        "last_sweep_ts": None,
        "total_swept": 0.0,
    }
    if sweeper_path.exists():
        try:
            import json as _json
            ss = _json.loads(sweeper_path.read_text())
            out["auto_sweep"]["last_sweep_ts"] = ss.get("last_sweep_ts")
            out["auto_sweep"]["total_swept"] = float(ss.get("total_swept", 0))
            out["auto_sweep"]["last_sweep_tx"] = ss.get("last_sweep_tx")
        except Exception:
            pass

    return out


@router.get("/mobile/api/alerts")
async def mobile_api_alerts(limit: int = 10, _: str = Depends(_require_lan_source)):
    """Live alerts for the Alerts tile: counts by severity + recent N.
    Same data as the existing /kai/notifications router, but in a single
    compact shape that's easy to render in a bottom sheet."""
    from core.kai_app_api import gather_alerts_payload
    return gather_alerts_payload(limit=max(1, min(limit, 50)))


@router.post("/mobile/api/wg/create")
async def mobile_api_wg_create(body: dict = Body(...),
                               device_id: str = Depends(_require_device_token)):
    """File a WireGuard-peer-creation approval. Label is required and
    sanitized. Requires a paired device token (rate limit at the approval
    layer prevents flooding from a single compromised device)."""
    label = _safe_field((body or {}).get("label", ""), max_len=60)
    if not label:
        raise HTTPException(422, "label is required (max 60 chars)")
    from core.kai_tools.policy import request_approval
    rid = request_approval("kai.wireguard.create_peer",
                           {"server": "ddwrt", "label": label,
                            "source": f"mobile-dashboard:{device_id}"},
                           f"Mobile dashboard ({device_id}): create WG peer '{label}'")
    if rid is None:
        raise HTTPException(503, "could not file WG-create approval")
    return {"ok": True, "approval_id": rid,
            "note": "operator must approve; poll /approvals for status"}


# ---------------------------------------------------------------------------
# Inline HTML — the PWA launcher page
# ---------------------------------------------------------------------------

_LAUNCHER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, user-scalable=no">
<meta name="theme-color" content="#020617">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Kai">
<link rel="manifest" href="/mobile/manifest">
<title>Kai Mobile Command</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}

:root {
  --bg:#020617; --surface:#0F172A; --elevated:#1E293B; --border:#1E293B;
  --fg:#F8FAFC; --muted:#94A3B8; --subtle:#64748B;
  --accent:#16A34A; --accent-glow:rgba(22,163,74,0.15);
  --radius:20px; --radius-sm:14px; --gap:14px;
  --font:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  --mono:"SF Mono","Fira Code",monospace;
  --safe-bottom:env(safe-area-inset-bottom, 16px);
}

body {
  background:var(--bg); color:var(--fg); font-family:var(--font);
  min-height:100vh; min-height:100dvh;
  -webkit-font-smoothing:antialiased;
  -webkit-tap-highlight-color:transparent;
  user-select:none; -webkit-user-select:none;
  display:flex; flex-direction:column;
}

/* ── Status Bar ── */
.status-bar {
  display:flex; align-items:center; justify-content:space-between;
  padding:12px 20px; padding-top:max(12px, env(safe-area-inset-top, 0));
  background:rgba(2,6,23,0.85); backdrop-filter:blur(20px);
  -webkit-backdrop-filter:blur(20px);
  position:sticky; top:0; z-index:100;
  border-bottom:1px solid rgba(30,41,59,0.5);
}
.status-left{display:flex;align-items:center;gap:10px;}
.status-dot{width:8px;height:8px;border-radius:50%;display:inline-block}
.status-dot.ok{background:var(--accent);box-shadow:0 0 8px var(--accent-glow)}
.status-dot.warning{background:#F59E0B;box-shadow:0 0 8px rgba(245,158,11,0.15)}
.status-dot.critical{background:#EF4444;box-shadow:0 0 8px rgba(239,68,68,0.15)}
.status-label{font-size:0.75rem;font-weight:600;letter-spacing:.04em;text-transform:uppercase}
.status-time{font-size:0.7rem;color:var(--subtle);font-family:var(--mono)}
.status-right{display:flex;align-items:center;gap:12px}
.badge{background:var(--elevated);border:1px solid var(--border);border-radius:20px;
  padding:4px 10px;font-size:0.7rem;font-weight:600;color:var(--muted)}
.badge.has-notif{color:#F59E0B;border-color:rgba(245,158,11,0.3)}

/* ── Main Content ── */
.main{padding:16px 20px;flex:1;display:flex;flex-direction:column;gap:var(--gap)}

/* ── Header ── */
.launcher-header{text-align:center;padding:8px 0 4px}
.launcher-header h1{font-size:1.6rem;font-weight:700;letter-spacing:-0.02em}
.launcher-header .k{color:var(--accent)}
.launcher-header p{font-size:0.78rem;color:var(--subtle);margin-top:2px}

/* ── Search ── */
.search-wrap{position:relative}
.search-input{
  width:100%;background:var(--surface);border:1px solid var(--border);
  border-radius:var(--radius);padding:12px 16px 12px 42px;
  font-size:0.9rem;color:var(--fg);font-family:var(--font);
  outline:none;transition:border-color 150ms;
}
.search-input::placeholder{color:var(--subtle)}
.search-input:focus{border-color:var(--accent)}
.search-icon{
  position:absolute;left:14px;top:50%;transform:translateY(-50%);
  color:var(--subtle);pointer-events:none;
}

/* ── Section Headers ── */
.section-header{display:flex;align-items:center;gap:8px;margin-top:4px}
.section-header .dot{width:6px;height:6px;border-radius:50%}
.section-header .dot.core{background:var(--accent)}
.section-header .dot.external{background:#3B82F6}
.section-header .dot.monitoring{background:var(--accent)}
.section-header h3{
  font-size:0.7rem;font-weight:700;letter-spacing:.06em;
  text-transform:uppercase;color:var(--subtle);
}

/* ── Tile Grid ── */
.tile-grid{
  display:grid;grid-template-columns:repeat(3, 1fr);gap:var(--gap);
}
.tile{
  background:var(--surface);border:1px solid var(--border);
  border-radius:var(--radius);padding:16px 10px 14px;
  display:flex;flex-direction:column;align-items:center;
  gap:8px;text-decoration:none;color:inherit;
  transition:transform 120ms,border-color 150ms,box-shadow 150ms;
  -webkit-tap-highlight-color:transparent;
  position:relative;
}
.tile:active{transform:scale(0.96)}
.tile:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.tile-icon{
  width:44px;height:44px;border-radius:var(--radius-sm);
  display:flex;align-items:center;justify-content:center;
  font-size:1.3rem;flex-shrink:0;
}
.tile-name{font-size:0.75rem;font-weight:600;text-align:center;line-height:1.2}
.tile-desc{font-size:0.65rem;color:var(--subtle);text-align:center;line-height:1.3;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.tile-status{
  position:absolute;top:10px;right:10px;
  width:8px;height:8px;border-radius:50%;
}
.tile-status.ok{background:var(--accent)}
.tile-status.warning{background:#F59E0B}
.tile-status.critical{background:#EF4444}
.tile-status.unknown{background:var(--subtle)}
.tile-badge{
  position:absolute;top:-4px;right:-4px;
  background:#EF4444;color:white;font-size:0.6rem;font-weight:700;
  min-width:18px;height:18px;border-radius:9px;
  display:flex;align-items:center;justify-content:center;
  padding:0 5px;
}

/* ── Quick Actions ── */
.quick-actions{display:flex;gap:10px}
.quick-action{
  flex:1;background:var(--surface);border:1px solid var(--border);
  border-radius:var(--radius);padding:14px;
  display:flex;align-items:center;gap:10px;
  text-decoration:none;color:inherit;font-size:0.8rem;font-weight:600;
  transition:border-color 150ms;
}
.quick-action:active{transform:scale(0.98)}
.quick-action .qa-icon{font-size:1.2rem}

/* ── Footer ── */
.launcher-footer{
  text-align:center;padding:12px;padding-bottom:var(--safe-bottom);
  font-size:0.65rem;color:var(--subtle);
}
.launcher-footer span{color:var(--accent)}

/* ── Hidden class ── */
.hidden{display:none!important}

/* ── Bottom sheet (KAI Ultimate feature tiles) ── */
.sheet-backdrop{
  position:fixed; inset:0; background:rgba(0,0,0,0.6);
  opacity:0; pointer-events:none; transition:opacity 200ms ease;
  z-index:200;
}
.sheet-backdrop.open{opacity:1; pointer-events:auto}
.sheet{
  position:fixed; left:0; right:0; bottom:0;
  background:var(--bg);
  border-top-left-radius:24px; border-top-right-radius:24px;
  border:1px solid var(--border);
  max-height:85vh; min-height:30vh;
  display:flex; flex-direction:column;
  transform:translateY(100%);
  transition:transform 240ms cubic-bezier(0.32, 0.72, 0, 1);
  z-index:201; padding-bottom:var(--safe-bottom);
  box-shadow:0 -10px 30px rgba(0,0,0,0.4);
}
.sheet.open{transform:translateY(0)}
.sheet-handle{
  width:36px; height:4px; border-radius:2px;
  background:var(--border); margin:8px auto 4px;
}
.sheet-header{
  padding:8px 20px 12px;
  border-bottom:1px solid var(--border);
  display:flex; align-items:center; justify-content:space-between;
  gap:10px;
}
.sheet-title{font-size:1.05rem; font-weight:700; color:var(--fg)}
.sheet-subtitle{font-size:0.7rem; color:var(--subtle); margin-top:2px}
.sheet-close{
  background:var(--elevated); border:1px solid var(--border);
  border-radius:50%; width:32px; height:32px;
  display:flex; align-items:center; justify-content:center;
  cursor:pointer; color:var(--muted); font-size:1.2rem; line-height:1;
}
.sheet-body{
  flex:1; overflow-y:auto; overflow-x:hidden;
  padding:14px 20px 14px;
  -webkit-overflow-scrolling:touch;
}
.sheet-loading{padding:30px 0; text-align:center; color:var(--muted); font-size:0.85rem}
.sheet-error{
  padding:14px; border-radius:14px;
  background:rgba(239,68,68,0.12); border:1px solid rgba(239,68,68,0.3);
  color:#FCA5A5; font-size:0.8rem; line-height:1.4;
}
.sheet-actions{
  display:flex; gap:8px; padding:10px 20px 14px;
  border-top:1px solid var(--border);
  background:rgba(2,6,23,0.85); backdrop-filter:blur(20px);
}
.sheet-action{
  flex:1; padding:12px; border-radius:14px;
  background:var(--elevated); border:1px solid var(--border);
  color:var(--fg); font-size:0.85rem; font-weight:600;
  cursor:pointer; transition:transform 120ms, border-color 150ms;
}
.sheet-action:active{transform:scale(0.97)}
.sheet-action.danger{background:rgba(239,68,68,0.15); border-color:rgba(239,68,68,0.4); color:#FCA5A5}
.sheet-action.primary{background:var(--accent); color:#fff; border-color:transparent}
.sheet-action:disabled{opacity:0.5; cursor:not-allowed}

/* Generic key/value renderer for sheet data */
.kv-card{
  background:var(--surface); border:1px solid var(--border);
  border-radius:var(--radius-sm); padding:12px 14px;
  margin-bottom:10px;
}
.kv-card h4{font-size:0.75rem; color:var(--muted); font-weight:600;
  text-transform:uppercase; letter-spacing:.04em; margin-bottom:6px}
.kv-row{display:flex; justify-content:space-between; gap:10px; padding:4px 0;
  border-top:1px solid var(--border)}
.kv-row:first-of-type{border-top:0}
.kv-key{color:var(--muted); font-size:0.8rem; flex:0 0 auto; max-width:40%;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis}
.kv-val{color:var(--fg); font-size:0.8rem; font-weight:500;
  text-align:right; word-break:break-word; min-width:0; flex:1 1 auto}
.kv-val code{font-family:var(--mono); font-size:0.75rem;
  background:var(--elevated); padding:1px 5px; border-radius:4px}
.status-pill{
  display:inline-block; padding:2px 8px; border-radius:10px;
  font-size:0.7rem; font-weight:600;
}
.status-pill.ok{background:rgba(22,163,74,0.18); color:#86EFAC}
.status-pill.warning{background:rgba(245,158,11,0.18); color:#FCD34D}
.status-pill.critical{background:rgba(239,68,68,0.18); color:#FCA5A5}
.status-pill.unknown{background:var(--elevated); color:var(--subtle)}
.briefing-text{
  font-size:0.95rem; line-height:1.5; color:var(--fg);
  white-space:pre-wrap; word-wrap:break-word;
}

/* ── Tablet / landscape overrides ── */
@media (min-width:600px) {
  .tile-grid{grid-template-columns:repeat(4, 1fr)}
  .main{max-width:600px;margin:0 auto}
}
@media (min-width:900px) {
  .tile-grid{grid-template-columns:repeat(5, 1fr)}
}
</style>
</head>
<body>

<!-- Status Bar -->
<div class="status-bar">
  <div class="status-left">
    <span class="status-dot ok" id="sys-dot"></span>
    <span class="status-label">Kai</span>
  </div>
  <div class="status-right">
    <span class="badge" id="notif-badge">—</span>
    <span class="status-time" id="clock">--:--</span>
  </div>
</div>

<!-- Main -->
<div class="main">
  <div class="launcher-header">
    <h1><span class="k">K</span>ai Mobile</h1>
    <p>Command Node</p>
  </div>

  <!-- Search -->
  <div class="search-wrap">
    <svg class="search-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
    <input class="search-input" type="text" id="search" placeholder="Search modules..." autocomplete="off">
  </div>

  <!-- Core Services -->
  <div class="section-header"><span class="dot core"></span><h3>Core</h3></div>
  <div class="tile-grid" id="grid-core"></div>

  <!-- External Services -->
  <div class="section-header"><span class="dot external"></span><h3>Services</h3></div>
  <div class="tile-grid" id="grid-external"></div>

  <!-- Quick Actions -->
  <div class="quick-actions">
    <a href="#" onclick="startVoice(event)" class="quick-action" id="voice-btn">
      <span class="qa-icon">🎙️</span>Talk to Kai
    </a>
    <a href="/command-center" class="quick-action">
      <span class="qa-icon">🫀</span>Health Check
    </a>
    <a href="#" onclick="openAlertsQuick(event)" class="quick-action">
      <span class="qa-icon">🔔</span>Alerts
    </a>
  </div>
</div>

<footer class="launcher-footer">
  Kai AI Orchestrator <span>●</span> Mobile Command Node
</footer>

<!-- Inline JS -->
<script>
// ── Icons (inline SVG, no external deps) ──
const ICONS = {
  'brain-circuit': '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4.5a2.5 2.5 0 0 0-4.96-.46 2.5 2.5 0 0 0-1.98 3 2.5 2.5 0 0 0-1.32 4.24 3 3 0 0 0 .34 5.58 2.5 2.5 0 0 0 2.96 3.08A2.5 2.5 0 0 0 12 19.5a2.5 2.5 0 0 0 4.96.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-1.98-3A2.5 2.5 0 0 0 12 4.5Z"/><path d="M17.97 14.83a2.5 2.5 0 0 0 .02-4.66"/></svg>',
  'terminal': '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>',
  'heart-pulse': '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/></svg>',
  'bell': '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/></svg>',
  'shield': '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10"/></svg>',
  'users': '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
  'landmark': '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="3" y1="22" x2="21" y2="22"/><line x1="6" y1="18" x2="6" y2="11"/><line x1="10" y1="18" x2="10" y2="11"/><line x1="14" y1="18" x2="14" y2="11"/><line x1="18" y1="18" x2="18" y2="11"/><polygon points="12 2 20 7 4 7"/></svg>',
  'rocket': '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z"/><path d="M12 15l-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z"/><path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0"/><path d="M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5"/></svg>',
  'gauge': '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m12 14 4-4"/><path d="M3.34 19a10 10 0 1 1 17.32 0"/></svg>',
  'code': '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>',
  'scale': '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m16 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z"/><path d="m2 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z"/><path d="M7 21h10"/><path d="M12 3v18"/><path d="M3 7h2c2 0 5-1 7-2 2 1 5 2 7 2h2"/></svg>',
  'chart-line': '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="m19 9-5 5-4-4-3 3"/></svg>',
  'trophy': '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6"/><path d="M18 9h1.5a2.5 2.5 0 0 0 0-5H18"/><path d="M4 22h16"/><path d="M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 18.75 7 20.24 7 22"/><path d="M14 14.66V17c0 .55.47.98.97 1.21C16.15 18.75 17 20.24 17 22"/><path d="M18 2H6v7a6 6 0 0 0 12 0V2Z"/></svg>',
  'globe': '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/><path d="M2 12h20"/></svg>',
  // KAI Ultimate feature surfaces (lucide-style 22x22 outline)
  'home': '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 9.5 12 2l9 7.5V20a2 2 0 0 1-2 2h-4v-7H9v7H5a2 2 0 0 1-2-2V9.5Z"/></svg>',
  'server': '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="8" rx="2"/><rect x="2" y="13" width="20" height="8" rx="2"/><line x1="6" y1="7" x2="6.01" y2="7"/><line x1="6" y1="17" x2="6.01" y2="17"/></svg>',
  'list-tree': '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h4l3 12h7"/><circle cx="6" cy="6" r="2"/><circle cx="14" cy="18" r="2"/></svg>',
  'sunrise': '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v4"/><path d="m4.93 10.93 2.83 2.83"/><path d="M2 18h2"/><path d="M20 18h2"/><path d="m19.07 10.93-2.83 2.83"/><path d="M22 18H2"/><path d="M8 6a4 4 0 0 0 8 0"/><path d="M12 18a6 6 0 0 0-6 6h12a6 6 0 0 0-6-6Z"/></svg>',
  'list-checks': '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m3 17 2 2 4-4"/><path d="m3 7 2 2 4-4"/><path d="M13 6h8"/><path d="M13 12h8"/><path d="M13 18h8"/></svg>',
  'dollar-sign': '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="2" x2="12" y2="22"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>',
  'alert-octagon': '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="7.86 2 16.14 2 22 7.86 22 16.14 16.14 22 7.86 22 2 16.14 2 7.86 7.86 2"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>',
  'network': '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="2" width="6" height="6"/><rect x="16" y="16" width="6" height="6"/><rect x="2" y="16" width="6" height="6"/><path d="M5 16v-2a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v2"/><path d="M12 8v4"/></svg>',
  'wrench': '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a4 4 0 1 1-5.4 5.4L4 17l3 3 5.3-5.3a4 4 0 0 1 5.4-5.4l-3 3-2-2 3-3Z"/></svg>',
};

// ── Clock ──
function tick(){document.getElementById('clock').textContent=new Date().toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})};
tick();setInterval(tick,30000);

// ── Fetch and render tiles ──
async function loadTiles(){
  try{
    const resp=await fetch('/mobile/tiles');
    const data=await resp.json();
    renderTiles(data.tiles);
  }catch(e){
    console.warn('Failed to load tiles, using static fallback');
    loadStaticTiles();
  }
}

function loadStaticTiles(){
  // Fallback tile data (same as TILES in Python)
  renderTiles([
    {id:'kai-dashboard',name:'Kai Dashboard',description:'AI Orchestrator overview & approvals',icon:'brain-circuit',color:'#16A34A',url:'/command-center',type:'internal',status:'unknown',tags:['ai','core']},
    {id:'command-center',name:'Command Center',description:'Full ops control plane',icon:'terminal',color:'#3B82F6',url:'/command-center',type:'internal',status:'unknown',tags:['ops','core']},
    {id:'health',name:'Health',description:'System health & anomaly detection',icon:'heart-pulse',color:'#22C55E',url:'/command-center',type:'internal',status:'unknown',tags:['monitoring','core']},
    {id:'notifications',name:'Notifications',description:'Alerts, incidents & activity feed',icon:'bell',color:'#F59E0B',url:'/kai/notifications',type:'internal',status:'unknown',tags:['monitoring']},
    {id:'wireguard',name:'WireGuard',description:'VPN tunnel status & management',icon:'shield',color:'#8B5CF6',url:'/kai/wireguard/status',type:'internal',status:'unknown',tags:['network','core']},
    {id:'it-manager',name:'IT Manager',description:'HR platform — workers, payroll, shifts',icon:'users',color:'#EC4899',url:'http://192.168.99.11:8090',type:'external',status:'unknown',tags:['hr','production']},
    {id:'susu',name:'Susu',description:'Microfinance — groups, contributions, payouts',icon:'landmark',color:'#14B8A6',url:'http://192.168.1.111:8050',type:'external',status:'unknown',tags:['finance']},
    {id:'kai-betting',name:'Kai Betting',description:'Sports betting engine — picks, EV gate, sessions',icon:'trophy',color:'#DC2626',url:'http://192.168.1.111:8096',type:'external',status:'unknown',tags:['betting','production']},
    {id:'airdrop-hunter',name:'Airdrop Hunter',description:'Crypto airdrop monitor & alerts',icon:'rocket',color:'#F97316',url:'http://192.168.99.11:8092',type:'external',status:'unknown',tags:['crypto']},
    {id:'proxdash',name:'ProxDash',description:'Homelab dashboard & resource monitor',icon:'gauge',color:'#06B6D4',url:'http://192.168.1.114:8091',type:'external',status:'unknown',tags:['monitoring']},
    {id:'code-server',name:'Code Server',description:'VS Code in the browser',icon:'code',color:'#2563EB',url:'http://192.168.99.11:8443',type:'external',status:'unknown',tags:['dev']},
    {id:'juris-kai',name:'Juris Kai',description:'Ghana legal corpus search & analysis',icon:'scale',color:'#D97706',url:'/command-center#legal',type:'internal',status:'unknown',tags:['legal']},
    {id:'portfolio',name:'Portfolio',description:'Investment & asset tracker',icon:'chart-line',color:'#10B981',url:'http://192.168.99.11:3000',type:'external',status:'unknown',tags:['finance']},
    {id:'money-center',name:'Money Center',description:'KAI Money Ecosystem — treasury, operations, KAI account',icon:'banknote',color:'#059669',url:'http://192.168.1.118:8095',type:'external',status:'unknown',tags:['money','production']},
    {id:'kai-vault',name:'Kai Vault',description:'Passkey login, secrets, identity, audit',icon:'shield-check',color:'#7C3AED',url:'https://vault.sso.deerude.com',type:'external',status:'unknown',tags:['identity','security','core']},
    {id:'deerude',name:'Deerude',description:'Public site — ventures & careers portal',icon:'globe',color:'#0EA5E9',url:'https://deerude.com',type:'external',status:'unknown',tags:['web','public']},
    // KAI Ultimate feature surfaces (mirror of TILES in Python)
    {id:'feature-home',name:'Home',description:'Executive summary, priorities, world model',icon:'home',color:'#16A34A',url:'/mobile/api/home',endpoint:'/mobile/api/home',type:'feature',status:'unknown',tags:['core','jarvis']},
    {id:'feature-proxmox',name:'Proxmox',description:'Nodes, containers, VMs, storage',icon:'server',color:'#3B82F6',url:'/mobile/api/proxmox',endpoint:'/mobile/api/proxmox',type:'feature',status:'unknown',tags:['infra','monitoring']},
    {id:'feature-missions',name:'Missions',description:'Active and queued work items',icon:'list-tree',color:'#8B5CF6',url:'/mobile/api/missions',endpoint:'/mobile/api/missions',type:'feature',status:'unknown',tags:['ops']},
    {id:'feature-briefing',name:'Catch me up',description:'JARVIS executive briefing (facts only)',icon:'sunrise',color:'#F59E0B',url:'/mobile/api/briefing',endpoint:'/mobile/api/briefing',type:'feature',status:'unknown',tags:['jarvis']},
    {id:'feature-capabilities',name:'What Kai can do',description:'Live tool/capability registry',icon:'list-checks',color:'#22C55E',url:'/mobile/api/capabilities',endpoint:'/mobile/api/capabilities',type:'feature',status:'unknown',tags:['jarvis','core']},
    {id:'feature-spend',name:'AI Spend',description:'Real AI cost tracker (last 30 days)',icon:'dollar-sign',color:'#10B981',url:'/mobile/api/spend?days=30',endpoint:'/mobile/api/spend?days=30',type:'feature',status:'unknown',tags:['finance','jarvis']},
    {id:'feature-emergency',name:'Emergency',description:'Kill switch + scheduler pause (approval-gated)',icon:'alert-octagon',color:'#EF4444',url:'/mobile/api/emergency/status',endpoint:'/mobile/api/emergency/status',type:'feature',status:'unknown',tags:['core','control']},
    {id:'feature-wg',name:'WireGuard',description:'Live peers + create-peer (approval-gated)',icon:'network',color:'#7C3AED',url:'/mobile/api/wg/peers',endpoint:'/mobile/api/wg/peers',type:'feature',status:'unknown',tags:['network','core']},
    {id:'feature-enhancements',name:'Enhancements',description:'Toggle Kai capability enhancements',icon:'wrench',color:'#06B6D4',url:'/kai/app/enhancements',endpoint:'/kai/app/enhancements',type:'feature',status:'unknown',tags:['jarvis','core']},
  ]);
}

function renderTiles(tiles){
  const core=document.getElementById('grid-core');
  const ext=document.getElementById('grid-external');
  core.innerHTML=''; ext.innerHTML='';

  tiles.forEach(t=>{
    const el=document.createElement('a');
    el.className='tile'; el.href=t.url;
    if(t.type==='external') { el.target='_blank'; el.rel='noopener'; }
    el.dataset.name=t.name.toLowerCase();
    el.dataset.tags=t.tags.join(' ');
    el.dataset.id=t.id;
    if(t.type==='feature' && t.endpoint) el.dataset.endpoint=t.endpoint;
    if(t.type==='feature' && t.actions) el.dataset.actions=JSON.stringify(t.actions);

    el.innerHTML=
      (t.notif_count ? `<span class="tile-badge">${t.notif_count}</span>` : '') +
      `<div class="tile-icon" style="background:${t.color}22">${ICONS[t.icon]||''}</div>` +
      `<span class="tile-name">${t.name}</span>` +
      `<span class="tile-desc">${t.description||''}</span>` +
      (t.status!=='unknown' ? `<span class="tile-status ${t.status}"></span>` : '');

    if(t.type==='internal' || t.type==='feature') core.appendChild(el);
    else ext.appendChild(el);
  });
  // Wire feature tile taps → openSheet
  document.querySelectorAll('.tile').forEach(t=>{
    if(t.dataset.endpoint){
      t.addEventListener('click', function(ev){
        ev.preventDefault();
        openSheet(t);
      });
    }
  });
}

// ── Bottom sheet ─────────────────────────────────────────────────────────
const _sheet=document.createElement('div');
_sheet.className='sheet';
_sheet.innerHTML=`
  <div class="sheet-handle"></div>
  <div class="sheet-header">
    <div>
      <div class="sheet-title" id="sheet-title">Loading…</div>
      <div class="sheet-subtitle" id="sheet-subtitle"></div>
    </div>
    <button class="sheet-close" id="sheet-close" aria-label="Close">×</button>
  </div>
  <div class="sheet-body" id="sheet-body"></div>
  <div class="sheet-actions" id="sheet-actions" style="display:none"></div>
`;
document.body.appendChild(_sheet);
const _backdrop=document.createElement('div');
_backdrop.className='sheet-backdrop';
document.body.appendChild(_backdrop);

document.getElementById('sheet-close').addEventListener('click', closeSheet);
_backdrop.addEventListener('click', closeSheet);
document.addEventListener('keydown', e=>{ if(e.key==='Escape') closeSheet(); });

let _currentTile=null;
function openSheet(tileEl){
  _currentTile=tileEl;
  const name=tileEl.querySelector('.tile-name')?.textContent || tileEl.dataset.id;
  const desc=tileEl.querySelector('.tile-desc')?.textContent || '';
  document.getElementById('sheet-title').textContent=name;
  document.getElementById('sheet-subtitle').textContent=desc;
  document.getElementById('sheet-body').innerHTML='<div class="sheet-loading">Loading…</div>';
  document.getElementById('sheet-actions').style.display='none';
  _backdrop.classList.add('open');
  _sheet.classList.add('open');
  fetchSheet(tileEl);
}

function closeSheet(){
  _backdrop.classList.remove('open');
  _sheet.classList.remove('open');
  _currentTile=null;
}

async function fetchSheet(tileEl){
  const url=tileEl.dataset.endpoint;
  const body=document.getElementById('sheet-body');
  try{
    const r=await fetch(url);
    if(!r.ok) throw new Error('HTTP '+r.status);
    const data=await r.json();
    renderSheet(data, body);
    renderActions(tileEl);
  }catch(err){
    body.innerHTML=`<div class="sheet-error">Failed to load: ${escapeHtml(err.message)}</div>`;
  }
}

function renderActions(tileEl){
  const bar=document.getElementById('sheet-actions');
  const raw=tileEl.dataset.actions;
  if(!raw){ bar.style.display='none'; return; }
  let actions;
  try { actions=JSON.parse(raw); } catch(e){ bar.style.display='none'; return; }
  if(!actions || !actions.length){ bar.style.display='none'; return; }
  bar.style.display='flex';
  bar.innerHTML='';
  actions.forEach(a=>{
    const b=document.createElement('button');
    b.className='sheet-action';
    if(a.label==='STOP' || a.label==='RESUME') b.classList.add('danger');
    b.textContent=a.label;
    b.addEventListener('click', ()=>runAction(a));
    bar.appendChild(b);
  });
}

async function runAction(a){
  // Special action kinds (not HTTP requests) — handled in the sheet
  if(a.kind === 'open-terminal') return openTerminal();
  if(a.confirm && !confirm(a.confirm)) return;
  let body=a.body || {};
  if(a.prompt){
    const v=prompt(a.prompt, '');
    if(!v) return;
    body={...body, [a.body_field || 'value']: v};
  }
  const bar=document.getElementById('sheet-actions');
  [...bar.children].forEach(c=>c.disabled=true);
  try{
    const r=await fetch(a.endpoint, {
      method: a.method || 'POST',
      headers: a.method==='POST' ? {'content-type':'application/json'} : {},
      body: a.method==='POST' ? JSON.stringify(body) : undefined,
    });
    const data=await r.json();
    if(!r.ok) throw new Error(data.detail || 'HTTP '+r.status);
    const body2=document.getElementById('sheet-body');
    const ok=document.createElement('div');
    ok.className='sheet-error';
    ok.style.background='rgba(22,163,74,0.15)';
    ok.style.borderColor='rgba(22,163,74,0.4)';
    ok.style.color='#86EFAC';
    ok.textContent='✅ Approval filed: '+(data.approval_id||'ok');
    body2.prepend(ok);
    if(_currentTile && _currentTile.dataset.endpoint.includes('emergency')){
      setTimeout(()=>{ if(_currentTile) fetchSheet(_currentTile); }, 800);
    }
  }catch(err){
    alert('Action failed: '+err.message);
  }finally{
    [...bar.children].forEach(c=>c.disabled=false);
  }
}

async function openTerminal(){
  // Use the most recent /mobile/api/terminal payload (cached on the tile)
  // to build a clickable URL with basic-auth baked in.
  const bar=document.getElementById('sheet-actions');
  [...bar.children].forEach(c=>c.disabled=true);
  try{
    const r=await fetch('/mobile/api/terminal');
    if(!r.ok) throw new Error('HTTP '+r.status);
    const d=await r.json();
    if(!d.ok) throw new Error('terminal not configured');
    const url = `http://${d.credential}@${location.hostname}:${d.port}${d.path || '/'}`;
    window.open(url, '_blank', 'noopener');
  }catch(err){
    alert('Could not open terminal: '+err.message);
  }finally{
    [...bar.children].forEach(c=>c.disabled=false);
  }
}

function renderSheet(data, body){
  // Try a few smart shapes; otherwise dump as key/values
  if(typeof data==='string'){
    const div=document.createElement('div');
    div.className='briefing-text';
    div.textContent=data;
    body.replaceChildren(div);
    return;
  }
  if(data && data.briefing){
    const div=document.createElement('div');
    div.className='briefing-text';
    div.textContent=data.briefing;
    body.replaceChildren(div);
    return;
  }
  if(data && data.total_cost !== undefined){
    body.innerHTML=`
      <div class="kv-card">
        <h4>AI Spend (last ${data.days || 30}d)</h4>
        <div class="kv-row"><span class="kv-key">Total cost</span><span class="kv-val"><code>$${escapeHtml(String(data.total_cost))}</code></span></div>
        <div class="kv-row"><span class="kv-key">Calls estimated</span><span class="kv-val">${escapeHtml(String(data.calls_estimated))}</span></div>
        ${data.by_provider ? renderProviderTable(data.by_provider) : ''}
      </div>`;
    return;
  }
  if(data && data.stopped !== undefined){
    const stopped=data.stopped;
    const cls=stopped?'critical':'ok';
    const lbl=stopped?'STOPPED':'RUNNING';
    body.innerHTML=`
      <div class="kv-card">
        <h4>System state</h4>
        <div class="kv-row"><span class="kv-key">Status</span><span class="kv-val"><span class="status-pill ${cls}">${lbl}</span></span></div>
        <div class="kv-row"><span class="kv-key">Scheduler paused</span><span class="kv-val">${data.scheduler_paused?'yes':'no'}</span></div>
        ${data.by ? `<div class="kv-row"><span class="kv-key">Last stop by</span><span class="kv-val"><code>${escapeHtml(data.by)}</code></span></div>` : ''}
        ${data.reason ? `<div class="kv-row"><span class="kv-key">Reason</span><span class="kv-val">${escapeHtml(data.reason)}</span></div>` : ''}
      </div>`;
    return;
  }
  if(data && data.raw !== undefined){
    body.innerHTML=`
      <div class="kv-card">
        <h4>${data.ok ? 'Live peer list' : 'wg show failed'}</h4>
        ${data.ok ? `<pre style="font-family:var(--mono);font-size:0.75rem;white-space:pre-wrap;color:var(--fg)">${escapeHtml(data.raw)}</pre>` : `<div class="sheet-error">${escapeHtml(data.error||'unknown')}</div>`}
      </div>`;
    return;
  }
  if(data && data.session !== undefined){
    // Claude terminal: session + ttyd URL
    const s = data.session || {};
    const running = s.running;
    const cls = running ? 'ok' : 'critical';
    const lbl = running ? 'ACTIVE' : 'OFFLINE';
    const u = s.uptime_s || 0;
    const days = Math.floor(u / 86400);
    const hours = Math.floor((u % 86400) / 3600);
    const mins = Math.floor((u % 3600) / 60);
    let uptime = '';
    if (days) uptime += days + 'd ';
    if (hours || days) uptime += hours + 'h ';
    uptime += mins + 'm';
    body.innerHTML = '';
    const card = document.createElement('div');
    card.className = 'kv-card';
    const h = document.createElement('h4');
    h.textContent = 'Claude Code session';
    card.appendChild(h);
    card.appendChild(kvRow('Status', statusPill(cls, lbl)));
    if (s.tmux_session) card.appendChild(kvRow('tmux session', s.tmux_session));
    if (s.claude_pid) card.appendChild(kvRow('claude PID', s.claude_pid));
    if (s.claude_uptime_s != null) card.appendChild(kvRow('claude uptime', formatUptime(s.claude_uptime_s)));
    else if (uptime) card.appendChild(kvRow('session uptime', uptime));
    if (s.windows) card.appendChild(kvRow('windows', s.windows));
    body.appendChild(card);

    const tcard = document.createElement('div');
    tcard.className = 'kv-card';
    const th = document.createElement('h4');
    th.textContent = 'ttyd (web terminal)';
    tcard.appendChild(th);
    tcard.appendChild(kvRow('Port', data.port));
    tcard.appendChild(kvRow('Path', data.path || '/'));
    const credRow = document.createElement('div');
    credRow.className = 'kv-row';
    const credK = document.createElement('span');
    credK.className = 'kv-key'; credK.textContent = 'basic-auth';
    const credV = document.createElement('span');
    credV.className = 'kv-val';
    const code = document.createElement('code');
    code.textContent = data.credential;
    credV.appendChild(code);
    credRow.appendChild(credK); credRow.appendChild(credV);
    tcard.appendChild(credRow);
    const urlRow = document.createElement('div');
    urlRow.className = 'kv-row';
    const uK = document.createElement('span');
    uK.className = 'kv-key'; uK.textContent = 'open in new tab';
    const uV = document.createElement('span');
    uV.className = 'kv-val';
    const ttydUrl = `http://${data.credential}@${location.hostname}:${data.port}${data.path || '/'}`;
    const a = document.createElement('a');
    a.href = ttydUrl;
    a.target = '_blank';
    a.rel = 'noopener';
    a.style.color = 'var(--accent)';
    a.textContent = 'ttyd →';
    uV.appendChild(a);
    urlRow.appendChild(uK); urlRow.appendChild(uV);
    tcard.appendChild(urlRow);
    body.appendChild(tcard);
    return;
  }
  if(data && data.categories){
    let html=`<div class="kv-card"><h4>${data.total||0} capabilities</h4>`;
    for(const [cat, items] of Object.entries(data.categories)){
      html+=`<div class="kv-row"><span class="kv-key">${escapeHtml(cat)}</span><span class="kv-val">${items.length} tools</span></div>`;
    }
    html+=`</div>`;
    body.innerHTML=html;
    return;
  }
  if(data && data.counts && data.recent !== undefined){
    // Alerts tile: severity counts + recent items
    const counts = data.counts || {};
    const recent = data.recent || [];
    body.innerHTML = '';
    const c = document.createElement('div');
    c.className = 'kv-card';
    const ch = document.createElement('h4'); ch.textContent = 'Open alerts';
    c.appendChild(ch);
    c.appendChild(kvRow('Critical', statusPill('critical', String(counts.critical || 0))));
    c.appendChild(kvRow('Important', statusPill('warning', String(counts.important || 0))));
    c.appendChild(kvRow('Informational', statusPill('ok', String(counts.informational || 0))));
    body.appendChild(c);
    if (recent.length) {
      const r = document.createElement('div');
      r.className = 'kv-card';
      const rh = document.createElement('h4');
      rh.textContent = 'Recent (' + recent.length + ')';
      r.appendChild(rh);
      recent.forEach(n => {
        const sev = n.severity || 'informational';
        const cls = sev === 'critical' ? 'critical' : (sev === 'important' ? 'warning' : 'unknown');
        const row = document.createElement('div');
        row.className = 'kv-row';
        const k = document.createElement('span');
        k.className = 'kv-key';
        k.textContent = n.title || n.id || '(no title)';
        const v = document.createElement('span');
        v.className = 'kv-val';
        v.appendChild(statusPill(cls, sev));
        row.appendChild(k); row.appendChild(v);
        r.appendChild(row);
        // body excerpt below
        if (n.body) {
          const br = document.createElement('div');
          br.style.fontSize = '0.7rem';
          br.style.color = 'var(--subtle)';
          br.style.padding = '2px 0 6px';
          br.textContent = String(n.body).slice(0, 200);
          r.appendChild(br);
        }
      });
      body.appendChild(r);
    } else {
      const empty = document.createElement('div');
      empty.className = 'kv-card';
      empty.style.color = 'var(--muted)';
      empty.textContent = 'No recent alerts ✅';
      body.appendChild(empty);
    }
    return;
  }
  if(data && data.nodes && Array.isArray(data.nodes)){
    let html='';
    data.nodes.forEach(n=>{
      const cls=n.reachable?'ok':'critical';
      html+=`<div class="kv-card">
        <h4>${escapeHtml(n.name)} <span class="status-pill ${cls}">${n.reachable?'online':'offline'}</span></h4>
        ${n.error?`<div class="sheet-error">${escapeHtml(n.error)}</div>`:''}
        ${n.containers?`<div class="kv-row"><span class="kv-key">Containers</span><span class="kv-val">${n.containers.length}</span></div>`:''}
        ${n.vms?`<div class="kv-row"><span class="kv-key">VMs</span><span class="kv-val">${n.vms.length}</span></div>`:''}
        ${n.storage?`<div class="kv-row"><span class="kv-key">Storage</span><span class="kv-val">${n.storage.length}</span></div>`:''}
      </div>`;
    });
    body.innerHTML=html;
    return;
  }
  if(data && data.missions && Array.isArray(data.missions)){
    if(!data.missions.length){
      body.innerHTML=`<div class="kv-card"><h4>Missions</h4><div style="color:var(--muted);padding:10px 0">No active missions ✅</div></div>`;
      return;
    }
    let html='<div class="kv-card"><h4>Active missions</h4>';
    data.missions.forEach(m=>{
      html+=`<div class="kv-row"><span class="kv-key">${escapeHtml(m.id||m.name||'?')}</span><span class="kv-val">${escapeHtml(m.status||'')}</span></div>`;
    });
    html+='</div>';
    body.innerHTML=html;
    return;
  }
  if(data && data.executive){
    const ex=data.executive;
    let html='<div class="kv-card"><h4>Executive</h4>';
    if(ex.priorities && ex.priorities.length){
      html+=`<div style="padding:6px 0"><b>Priorities</b><ul style="margin:6px 0 0 18px">`;
      ex.priorities.slice(0,5).forEach(p=>{ html+=`<li>${escapeHtml(String(p))}</li>`; });
      html+='</ul></div>';
    }
    html+=`</div>`;
    if(data.world){
      html+=`<div class="kv-card"><h4>World model</h4>
        <div class="kv-row"><span class="kv-key">Keys</span><span class="kv-val">${Object.keys(data.world).length}</span></div>
        </div>`;
    }
    if(data.data_trust){
      html+=`<div class="kv-card"><h4>Data freshness</h4>`;
      for(const [k,v] of Object.entries(data.data_trust)){
        const cls=v===null?'unknown':(v>30?'critical':(v>10?'warning':'ok'));
        html+=`<div class="kv-row"><span class="kv-key">${escapeHtml(k)}</span><span class="kv-val"><span class="status-pill ${cls}">${v===null?'unknown':v+'m'}</span></span></div>`;
      }
      html+='</div>';
    }
    body.innerHTML=html;
    return;
  }
  // Fallback: generic key/value dump
  body.innerHTML='<div class="kv-card"><h4>Response</h4>'+dumpKV(data)+'</div>';
}

function dumpKV(obj, depth=0){
  if(depth>3) return '<div class="kv-row"><span class="kv-key">…</span><span class="kv-val">truncated</span></div>';
  let html='';
  if(obj===null||obj===undefined) return '';
  if(typeof obj!=='object') return `<div class="kv-row"><span class="kv-key">value</span><span class="kv-val"><code>${escapeHtml(String(obj))}</code></span></div>`;
  for(const [k,v] of Object.entries(obj)){
    if(v===null||v===undefined){ html+=`<div class="kv-row"><span class="kv-key">${escapeHtml(k)}</span><span class="kv-val">—</span></div>`; continue; }
    if(typeof v==='object'){ html+=`<div class="kv-row"><span class="kv-key">${escapeHtml(k)}</span><span class="kv-val">${Array.isArray(v)?`${v.length} items`:'object'}</span></div>`; continue; }
    html+=`<div class="kv-row"><span class="kv-key">${escapeHtml(k)}</span><span class="kv-val">${escapeHtml(String(v).slice(0,200))}</span></div>`;
  }
  return html;
}

function renderProviderTable(byProvider){
  let html='<div style="margin-top:10px"><b>By provider</b>';
  for(const [p, v] of Object.entries(byProvider)){
    html+=`<div class="kv-row"><span class="kv-key">${escapeHtml(p)}</span><span class="kv-val"><code>$${escapeHtml(String(v))}</code></span></div>`;
  }
  html+='</div>';
  return html;
}

function escapeHtml(s){
  return String(s).replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function kvRow(key, value){
  const row=document.createElement('div');
  row.className='kv-row';
  const k=document.createElement('span'); k.className='kv-key'; k.textContent=key;
  const v=document.createElement('span'); v.className='kv-val';
  if(value instanceof Node) v.appendChild(value);
  else { const c=document.createElement('code'); c.textContent=String(value); v.appendChild(c); }
  row.appendChild(k); row.appendChild(v);
  return row;
}

function statusPill(cls, label){
  const p=document.createElement('span');
  p.className='status-pill '+cls;
  p.textContent=label;
  return p;
}

function formatUptime(seconds){
  if(!seconds || seconds<0) return '—';
  const d=Math.floor(seconds/86400);
  const h=Math.floor((seconds%86400)/3600);
  const m=Math.floor((seconds%3600)/60);
  let s='';
  if(d) s+=d+'d ';
  if(h || d) s+=h+'h ';
  s+=m+'m';
  return s;
}

// ── Search ──
document.getElementById('search').addEventListener('input',function(e){
  const q=e.target.value.toLowerCase();
  document.querySelectorAll('.tile').forEach(t=>{
    const match=t.dataset.name.includes(q)||t.dataset.tags.includes(q)||t.dataset.id.includes(q);
    t.classList.toggle('hidden',!match);
  });
  // Hide section headers if their grid is all hidden
  document.querySelectorAll('.section-header').forEach(h=>{
    const grid=h.nextElementSibling;
    if(!grid)return;
    const allHidden=grid.querySelectorAll('.tile:not(.hidden)').length===0;
    h.classList.toggle('hidden',allHidden);
  });
});

// ── Talk to Kai (voice + text fallback) ──────────────────────────────────
// Two modes:
//   1. Voice: hold-to-talk via /kai/voice/chat (requires secure context for
//      getUserMedia — i.e. HTTPS or localhost/127.0.0.1).
//   2. Text:  always available. POSTs to /kai/chat and shows the reply inline.
// The button auto-detects the mode: if getUserMedia is available, it records;
// otherwise it opens a text input. This avoids the scary "microphone
// unavailable" error users hit when opening the page over a non-local HTTP
// IP (the dashboard's primary access pattern from the S23 over WireGuard).
let _mediaStream=null, _recorder=null, _chunks=[];
let _kaiChatOverlay = null;

function showKaiChatOverlay(){
  if(_kaiChatOverlay) return _kaiChatOverlay;
  const ov = document.createElement('div');
  ov.style.cssText = 'position:fixed;left:0;right:0;bottom:0;top:0;background:rgba(2,6,23,0.85);backdrop-filter:blur(8px);z-index:300;display:flex;flex-direction:column;padding:20px;padding-top:max(20px,env(safe-area-inset-top));padding-bottom:max(20px,env(safe-area-inset-bottom))';
  ov.innerHTML = `
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
      <div>
        <div style="font-size:1.1rem;font-weight:700;color:var(--fg)">🧠 Talk to Kai</div>
        <div style="font-size:0.7rem;color:var(--subtle);margin-top:2px">Text chat — same backend as the Android app</div>
      </div>
      <button id="kai-close" style="background:var(--elevated);border:1px solid var(--border);border-radius:50%;width:32px;height:32px;color:var(--muted);font-size:1.2rem;line-height:1;cursor:pointer">×</button>
    </div>
    <div id="kai-log" style="flex:1;overflow-y:auto;background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:12px;margin-bottom:12px;font-size:0.85rem;color:var(--fg)"></div>
    <form id="kai-form" style="display:flex;gap:8px">
      <input id="kai-input" type="text" autocomplete="off" placeholder="Ask Kai anything…"
             style="flex:1;background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:12px;color:var(--fg);font-size:0.9rem;outline:none">
      <button type="submit" style="background:var(--accent);color:#fff;border:0;border-radius:14px;padding:0 18px;font-weight:600;cursor:pointer">Send</button>
    </form>
  `;
  document.body.appendChild(ov);
  const close = () => { ov.remove(); _kaiChatOverlay = null; };
  ov.querySelector('#kai-close').addEventListener('click', close);
  const log = ov.querySelector('#kai-log');
  const append = (who, text) => {
    const d = document.createElement('div');
    d.style.cssText = 'margin-bottom:8px;padding:8px 10px;border-radius:10px;';
    d.style.background = who==='user' ? 'var(--elevated)' : 'rgba(22,163,74,0.12)';
    const who_lbl = document.createElement('div');
    who_lbl.style.cssText = 'font-size:0.65rem;font-weight:700;letter-spacing:.04em;text-transform:uppercase;color:var(--subtle);margin-bottom:3px';
    who_lbl.textContent = who==='user' ? 'You' : 'Kai';
    const body = document.createElement('div');
    body.textContent = text;
    d.appendChild(who_lbl); d.appendChild(body);
    log.appendChild(d);
    log.scrollTop = log.scrollHeight;
  };
  // Greet
  append('kai', 'Hi — what can I help with?');
  const form = ov.querySelector('#kai-form');
  const input = ov.querySelector('#kai-input');
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const text = input.value.trim();
    if(!text) return;
    input.value = '';
    append('user', text);
    const sending = document.createElement('div');
    sending.textContent = '…';
    sending.style.color = 'var(--subtle)';
    sending.style.fontSize = '0.75rem';
    sending.style.padding = '4px 10px';
    log.appendChild(sending);
    log.scrollTop = log.scrollHeight;
    try{
      const r = await fetch('/kai/chat', {
        method:'POST',
        headers:{'content-type':'application/json'},
        body: JSON.stringify({text: text, operator: 'mobile-dashboard'})
      });
      sending.remove();
      if(!r.ok){
        const err = await r.text();
        append('kai', '⚠️ Error: '+(err||'HTTP '+r.status));
        return;
      }
      const d = await r.json();
      append('kai', d.response || d.text || '(no response)');
    }catch(err){
      sending.remove();
      append('kai', '⚠️ Network error: '+err.message);
    }
  });
  setTimeout(() => input.focus(), 100);
  _kaiChatOverlay = ov;
  return ov;
}

async function startVoice(e){
  if(e)e.preventDefault();
  // Detect secure context — required for getUserMedia on non-localhost.
  const canVoice = !!(window.isSecureContext && navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
  if(!canVoice){
    // Fall back to text chat inline. The /mobile dashboard is typically
    // reached over the LAN as http://192.168.x.x (insecure) or via
    // http://10.x WireGuard — neither is a secure context, so this branch
    // is the common case. Don't pop an alert; open the chat UI directly.
    showKaiChatOverlay();
    return;
  }
  try{
    _mediaStream=await navigator.mediaDevices.getUserMedia({audio:true});
    _chunks=[];
    _recorder=new MediaRecorder(_mediaStream);
    _recorder.ondataavailable=x=>_chunks.push(x.data);
    _recorder.onstop=async()=>{
      _mediaStream.getTracks().forEach(t=>t.stop());
      const blob=new Blob(_chunks,{type:'audio/webm'});
      document.getElementById('voice-btn').innerHTML='<span class="qa-icon">🧠</span>Kai is thinking…';
      try{
        const arr=await blob.arrayBuffer();
        const ac=new (window.AudioContext||window.webkitAudioContext)();
        const buf=await ac.decodeAudioData(arr);
        const wav=encodeWav(buf);
        const b64=btoa(String.fromCharCode(...new Uint8Array(wav)));
        const r=await fetch('/kai/voice/chat',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({audio:b64})});
        const d=await r.json();
        if(d.response){
          document.getElementById('voice-btn').innerHTML='<span class="qa-icon">💬</span>'+d.response.slice(0,60);
          if(d.audio_base64){playWav(d.audio_base64);}
        }else{
          document.getElementById('voice-btn').innerHTML='<span class="qa-icon">🎙️</span>'+(d.transcript?'heard: '+d.transcript.slice(0,40):'no response');
        }
      }catch(err){
        document.getElementById('voice-btn').innerHTML='<span class="qa-icon">⚠️</span>Voice error';
      }
      setTimeout(()=>{document.getElementById('voice-btn').innerHTML='<span class="qa-icon">🎙️</span>Talk to Kai';},6000);
    };
    document.getElementById('voice-btn').innerHTML='<span class="qa-icon">🔴</span>Listening… tap to send';
    _recorder.start();
    document.getElementById('voice-btn').onclick=(ev)=>{ev.preventDefault();if(_recorder.state==='recording')_recorder.stop();};
    setTimeout(()=>{if(_recorder&&_recorder.state==='recording')_recorder.stop();},8000);
  }catch(err){
    // Permission denied or device error — open text chat as fallback
    showKaiChatOverlay();
  }
}
async function openAlertsQuick(e){
  if(e)e.preventDefault();
  // If the alerts feature tile exists, open it (synthesized since it's
  // not in the static grid render). Otherwise create an ad-hoc sheet.
  const tile = document.querySelector('[data-id="feature-alerts"]');
  if(tile){
    openSheet(tile);
    return;
  }
  // Fallback: fetch + render inline
  const body=document.getElementById('sheet-body');
  document.getElementById('sheet-title').textContent='Alerts';
  document.getElementById('sheet-subtitle').textContent='Live operational notifications';
  body.innerHTML='<div class="sheet-loading">Loading…</div>';
  document.getElementById('sheet-actions').style.display='none';
  document.querySelector('.sheet-backdrop').classList.add('open');
  document.querySelector('.sheet').classList.add('open');
  try{
    const r=await fetch('/mobile/api/alerts');
    const d=await r.json();
    // Render using the renderer's path: temporarily stash the data + call it
    renderSheet(d, body);
  }catch(err){
    body.innerHTML='<div class="sheet-error">Failed to load alerts: '+escapeHtml(err.message)+'</div>';
  }
}

function encodeWav(audioBuffer){
  const numCh=1,sr=16000;
  const src=audioBuffer.getChannelData(0);
  // downsample naive
  const ratio=Math.max(1,Math.floor(audioBuffer.sampleRate/sr));
  const len=Math.floor(src.length/ratio);
  const buffer=new ArrayBuffer(44+len*2);
  const view=new DataView(buffer);
  const w=(o,s)=>{for(let i=0;i<s.length;i++)view.setUint8(o+i,s.charCodeAt(i));};
  w(0,'RIFF');view.setUint32(4,36+len*2,true);w(8,'WAVE');w(12,'fmt ');
  view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,numCh,true);
  view.setUint32(24,audioBuffer.sampleRate/ratio,true);view.setUint32(28,(audioBuffer.sampleRate/ratio)*numCh*2,true);
  view.setUint16(32,numCh*2,true);view.setUint16(34,16,true);w(36,'data');view.setUint32(40,len*2,true);
  let o=44;
  for(let i=0;i<len;i++){const v=Math.max(-1,Math.min(1,src[i*ratio]));view.setInt16(o,v<0?v*0x8000:v*0x7FFF,true);o+=2;}
  return buffer;
}
function playWav(b64){
  const bin=atob(b64);const bytes=new Uint8Array(bin.length);
  for(let i=0;i<bin.length;i++)bytes[i]=bin.charCodeAt(i);
  const blob=new Blob([bytes],{type:'audio/wav'});
  new Audio(URL.createObjectURL(blob)).play();
}

// ── Notification badge ──
async function updateNotifs(){
  try{
    const r=await fetch('/kai/notifications/unread-count');
    const d=await r.json();
    const total=d.critical+d.important+d.info;
    const badge=document.getElementById('notif-badge');
    badge.textContent=total>0 ? total+' alert'+ (total!==1?'s':'') : 'clear';
    badge.className='badge'+(total>0?' has-notif':'');
  }catch(e){}
}

// ── System status dot ──
async function updateSysStatus(){
  try{
    const r=await fetch('/kai/health/status');
    if(!r.ok){document.getElementById('sys-dot').className='status-dot warning';return}
    const d=await r.json();
    const score=d.health_score||100;
    const dot=document.getElementById('sys-dot');
    if(score>=90)dot.className='status-dot ok';
    else if(score>=60)dot.className='status-dot warning';
    else dot.className='status-dot critical';
  }catch(e){
    document.getElementById('sys-dot').className='status-dot warning';
  }
}

// ── Register service worker ──
if('serviceWorker' in navigator){
  navigator.serviceWorker.register('/mobile/sw.js').catch(()=>{});
}

// ── Init ──
loadTiles();
updateNotifs();
updateSysStatus();
setInterval(updateNotifs,60000);
setInterval(updateSysStatus,120000);
</script>
</body>
</html>"""
