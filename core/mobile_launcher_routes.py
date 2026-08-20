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

import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

logger = logging.getLogger(__name__)

router = APIRouter(tags=["mobile-launcher"])

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
        "url": "/kai/dashboard",
        "type": "internal",
        "tags": ["ai", "core"],
    },
    {
        "id": "command-center",
        "name": "Command Center",
        "description": "Full ops control plane",
        "icon": "terminal",
        "color": "#3B82F6",
        "url": "/kai/command-center",
        "type": "internal",
        "tags": ["ops", "core"],
    },
    {
        "id": "health",
        "name": "Health",
        "description": "System health & anomaly detection",
        "icon": "heart-pulse",
        "color": "#22C55E",
        "url": "/kai/health",
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
        "url": "/kai/susu",
        "type": "internal",
        "tags": ["finance"],
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
        "url": "/kai/juris",
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
]


# ---------------------------------------------------------------------------
# Route: Launcher HTML page
# ---------------------------------------------------------------------------

@router.get("/mobile", response_class=HTMLResponse)
async def mobile_launcher(request: Request):
    """Serve the PWA launcher dashboard."""
    return _LAUNCHER_HTML


@router.get("/mobile/", response_class=HTMLResponse)
async def mobile_launcher_slash(request: Request):
    """Redirect /mobile/ to /mobile."""
    return _LAUNCHER_HTML


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

_SW_JS = """
const CACHE = 'kai-launcher-v1';
const ASSETS = [
  '/mobile',
  '/mobile/manifest',
  '/mobile/icon-192',
  '/mobile/icon-512',
];

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
    <a href="/kai/health" class="quick-action">
      <span class="qa-icon">🫀</span>Health Check
    </a>
    <a href="/kai/notifications" class="quick-action">
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
    {id:'kai-dashboard',name:'Kai Dashboard',description:'AI Orchestrator overview & approvals',icon:'brain-circuit',color:'#16A34A',url:'/kai/dashboard',type:'internal',status:'unknown',tags:['ai','core']},
    {id:'command-center',name:'Command Center',description:'Full ops control plane',icon:'terminal',color:'#3B82F6',url:'/kai/command-center',type:'internal',status:'unknown',tags:['ops','core']},
    {id:'health',name:'Health',description:'System health & anomaly detection',icon:'heart-pulse',color:'#22C55E',url:'/kai/health',type:'internal',status:'unknown',tags:['monitoring','core']},
    {id:'notifications',name:'Notifications',description:'Alerts, incidents & activity feed',icon:'bell',color:'#F59E0B',url:'/kai/notifications',type:'internal',status:'unknown',tags:['monitoring']},
    {id:'wireguard',name:'WireGuard',description:'VPN tunnel status & management',icon:'shield',color:'#8B5CF6',url:'/kai/wireguard/status',type:'internal',status:'unknown',tags:['network','core']},
    {id:'it-manager',name:'IT Manager',description:'HR platform — workers, payroll, shifts',icon:'users',color:'#EC4899',url:'http://192.168.99.11:8090',type:'external',status:'unknown',tags:['hr','production']},
    {id:'susu',name:'Susu',description:'Microfinance — groups, contributions, payouts',icon:'landmark',color:'#14B8A6',url:'/kai/susu',type:'internal',status:'unknown',tags:['finance']},
    {id:'airdrop-hunter',name:'Airdrop Hunter',description:'Crypto airdrop monitor & alerts',icon:'rocket',color:'#F97316',url:'http://192.168.99.11:8092',type:'external',status:'unknown',tags:['crypto']},
    {id:'proxdash',name:'ProxDash',description:'Homelab dashboard & resource monitor',icon:'gauge',color:'#06B6D4',url:'http://192.168.1.114:8091',type:'external',status:'unknown',tags:['monitoring']},
    {id:'code-server',name:'Code Server',description:'VS Code in the browser',icon:'code',color:'#2563EB',url:'http://192.168.99.11:8443',type:'external',status:'unknown',tags:['dev']},
    {id:'juris-kai',name:'Juris Kai',description:'Ghana legal corpus search & analysis',icon:'scale',color:'#D97706',url:'/kai/juris',type:'internal',status:'unknown',tags:['legal']},
    {id:'portfolio',name:'Portfolio',description:'Investment & asset tracker',icon:'chart-line',color:'#10B981',url:'http://192.168.99.11:3000',type:'external',status:'unknown',tags:['finance']},
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

    el.innerHTML=
      (t.notif_count ? `<span class="tile-badge">${t.notif_count}</span>` : '') +
      `<div class="tile-icon" style="background:${t.color}22">${ICONS[t.icon]||''}</div>` +
      `<span class="tile-name">${t.name}</span>` +
      `<span class="tile-desc">${t.description||''}</span>` +
      (t.status!=='unknown' ? `<span class="tile-status ${t.status}"></span>` : '');

    if(t.type==='internal') core.appendChild(el);
    else ext.appendChild(el);
  });
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
