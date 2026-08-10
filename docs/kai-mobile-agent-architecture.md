# Kai Mobile Agent Architecture

**Part of**: Kai Mobile Command Node — Sub-project 6: Integration & Testing
**Last updated**: 2026-08-10
**Status**: Production

## Overview

The Kai Mobile Agent runs on Android (Samsung Galaxy S23 Ultra, Android 16 / One UI 8) 
and provides operator control over the Kai orchestrator. It communicates via WireGuard VPN 
to the Kai API server over a secure tunnel.

## Architecture Layers

```
┌────────────────────────────────────────────────┐
│          Android 16 / One UI 8                   │
│  ┌──────────────────────────────────────┐      │
│  │  Kai PWA (Chrome, standalone mode)     │      │
│  │  ├─ manifest.json                      │      │
│  │  ├─ sw.js (Service Worker)             │      │
│  │  ├─ command_center.html                │      │
│  │  └─ Web Speech API (voice input)       │      │
│  └──────────────┬───────────────────────┘      │
│                 │ HTTPS (WireGuard VPN)          │
│  ┌──────────────▼───────────────────────┐      │
│  │  WireGuard tunnel (10.8.0.8)          │      │
│  └──────────────┬───────────────────────┘      │
└─────────────────┼──────────────────────────────┘
                  │
┌─────────────────▼──────────────────────────────┐
│          DD-WRT Router (10.8.0.3)               │
│  WireGuard peer entry w/ keepalive               │
└─────────────────┬──────────────────────────────┘
                  │
┌─────────────────▼──────────────────────────────┐
│          Proxmox LXC (Kai Server)                │
│  ┌──────────────────────────────────────┐      │
│  │  FastAPI (port 8000)                   │      │
│  │  ├─ /kai/mobile/diagnose               │      │
│  │  ├─ /kai/devices/{id}/heartbeat        │      │
│  │  ├─ /kai/devices/{id}/notification-*   │      │
│  │  ├─ /kai/manifest.json                 │      │
│  │  ├─ /kai/sw.js                         │      │
│  │  └─ /command-center                    │      │
│  └──────────────────────────────────────┘      │
└────────────────────────────────────────────────┘
```

## Key Components

### 1. PWA (Progressive Web App)

**Manifest** (`core/kai/manifest.json`):
- `display: standalone` — full-screen, no browser chrome
- `theme_color: #16A34A` — Kai green
- `background_color: #020617` — dark slate
- `orientation: portrait-primary` — optimized for phone use
- App shortcuts: System Status, AI Workforce, Kai Chat, Devices
- Icons: 192×192 and 512×512, maskable

**Service Worker** (`core/kai/sw.js`):
- **Cache strategy**: Cache-first for app shell (instant cold starts), network-first for API data (stale-while-revalidate)
- **Push notifications**: Web push with action buttons, notification click → panel navigation via postMessage
- **Background sync**: `kai-heartbeat` tag for offline action queuing
- **Caches**: `kai-shell-v1` (static assets), `kai-api-v1` (API responses)

**Command Center** (`core/kai/command_center.html`):
- Single-page app with dynamic panel loading
- 5 main panels: Home, AI Workforce, Infrastructure, Kai Chat, More
- Bottom navigation bar (mobile-optimized)
- Module launcher grid (dynamic from `/api/modules`)
- Voice input via Web Speech API (`SpeechRecognition`)
- All user content HTML-escaped (`esc()` function)
- PWA meta tags for iOS Safari (`apple-mobile-web-app-*`)

### 2. VPN Connectivity

**WireGuard tunnel**:
- Phone IP: `10.8.0.8/32` (static assignment)
- DD-WRT as WireGuard server (port 51820)
- Keepalive: 25 seconds (NAT traversal)
- Health check: heartbeat includes VPN peer status via `_check_vpn_peer_health()`

**Heartbeat protocol** (30s interval from device):
```
POST /kai/devices/{id}/heartbeat
{
  "device_id": "s23-ultra",
  "device_token": "kt-...",
  "vpn_ip": "10.8.0.8",
  "device_info": {...},
  "ack_notification_ids": [...]
}
→ Response: { status, pending_commands, pending_notifications, health_summary }
```

### 3. Authentication

**Device identity**:
- Each device registered with unique `device_id` and `device_token` (prefixed `kt-`)
- Tokens stored in device registry (`core/device_registry.py`)
- Status: `pending` → `authorized` → `revoked`
- Bridge token for server-to-server API access

**JWT sessions** (for dashboard/web access):
- Stateless JWT with embedded role claims
- Auto-generated secret file (`memory/.jwt_secret`, 0600)
- Blocklist for token revocation

### 4. Notifications

**Delivery mechanism**:
- Pending notifications returned in heartbeat response
- Device acknowledges via `ack_notification_ids` in next heartbeat
- Push notifications via Web Push API (service worker)

**Preference system** (per-device):
- Three filter levels: `per_severity`, `per_module`, `per_source`
- Deep-merge config updates (partial updates don't wipe existing prefs)
- `_should_deliver_notification()` gates all delivery

**Module routing**:
- Sources auto-map to modules (health_analyzer→health, vpn_failover→vpn, etc.)
- Each module defines action buttons with `{label, action, target}`

### 5. Self-Diagnostics

**Endpoint**: `GET /kai/mobile/diagnose`
**Command**: `Kai, mobile diagnose`

Checks 8 subsystems:
| Check | How | Status |
|-------|-----|--------|
| Device Registry | `list_devices()` → authorized/online count | PASS/WARN/FAIL |
| WireGuard | `get_wg_status()` → peer handshake age | PASS/WARN/FAIL |
| Kai API | TestClient `GET /health` | PASS/FAIL |
| Authentication | Bridge token, device tokens, JWT secret | PASS/WARN/FAIL |
| Notifications | `NotificationManager.get_stats()` + test enqueue | PASS/FAIL |
| AI Providers | `list_providers()` → active count | PASS/WARN/FAIL |
| Health Worker | `_default_worker.is_running` + assignments | PASS/WARN/FAIL |
| PWA Assets | manifest.json, sw.js, PWA meta in HTML | PASS/FAIL |

Each check returns `{name, status, detail, artifact, elapsed_ms}`.

## Data Flow: Operator Command

```
Operator says "Kai, analyze system health"
  → Web Speech API or text input
  → command_center.html sends to server
  → core/kai/commands.py::dispatch() pattern-matches
  → _handle_health() → analyze_health() + provider_health snapshots
  → Response rendered in Chat panel
```

## Data Flow: Incident Alert

```
Health analyzer detects anomaly
  → NotificationManager.enqueue(severity="critical", source="health_analyzer")
  → Module auto-routed to "health"
  → _should_deliver_notification(device_id, notif) checks preferences
  → queue_notification(device_id, notif)
  → Next heartbeat returns { pending_notifications: [...] }
  → Service worker push event (if Web Push enabled)
  → Operator taps notification → opens Command Center → navigates to Health panel
```

## Security Considerations

- **Token storage**: Device tokens use `kt-` prefix, bridge token in API_TOKEN_PATH, JWT secret in memory/.jwt_secret (0600)
- **API keys**: Provider secrets encrypted in `provider_secrets.json` (0600)
- **Revocation**: Device status can be set to `revoked`, JWT tokens can be blocklisted
- **Audit trail**: Secret access logged to `secret_access_audit.json`
- **Rate limiting**: API gateway has per-consumer-key rate limiting
- **VPN-only**: Mobile endpoints intended for use over WireGuard tunnel only
- **HTML safety**: All user content HTML-escaped in command center

## Performance

- **PWA install**: < 1s cold install (cached app shell)
- **Heartbeat**: < 100ms server-side (synchronous device registry query)
- **Diagnostics**: < 2s full 8-check suite (parallelizable, currently sequential)
- **Service worker**: < 50ms cache hit, network-fallback on API cache miss
