# Kai Command Center — Full Control Panel Redesign

**Date**: 2026-08-07
**Status**: In Progress
**Phase**: 13O + 19B

## Scope

Redesign the Kai Command Center from a view-only dashboard into a unified control plane
for every module and application in the ecosystem. All panels must have real management
controls — no stub tabs or disabled buttons.

## Design System

Generated via ui-ux-pro-max v2.0. Data-Dense Dashboard + Terminal-inspired dark mode.

| Token | Value | Usage |
|-------|-------|-------|
| Background | #020617 | Deepest base |
| Surface | #0F172A | Cards, sidebar |
| Elevated | #1E293B | Modals, dropdowns |
| Foreground | #F8FAFC | Primary text |
| Accent | #16A34A | Healthy/running green |
| Warning | #D29922 | Degraded/queued amber |
| Destructive | #DC2626 | Failed/critical red |
| Border | #334155 | Dividers |
| Font | Fira Code (monospace) | Data, status, code |
| | Fira Sans | Labels, descriptions |
| Radius | 0px | Terminal aesthetic |
| Density | 9/10 | Compact dashboard |
| Motion | 3/10 | Subtle micro-interactions |

## Architecture

```
Kai Command Center (SPA, :8000/command-center)
├── Kai Modules Panel → /kai/*, /approvals, /builds, /roadmap (existing)
├── Docker Panel → /api/docker/* (NEW — Docker socket)
├── Airdrop Panel → /admin/* on airdrop-hunter:4201 (NEW — Express API)
└── Security/Settings/Logs → existing endpoints, enhanced
```

## New API Endpoints

### Docker (Kai API)
- GET /api/docker/containers — list all containers + status
- POST /api/docker/containers/{name}/start
- POST /api/docker/containers/{name}/stop
- POST /api/docker/containers/{name}/restart
- GET /api/docker/containers/{name}/logs?tail=100

### Airdrop Hunter Admin API
- GET /admin/subscribers — list subscribers
- GET /admin/stats — bot stats
- POST /admin/broadcast — send to all
- GET /admin/config — get config
- PUT /admin/config — update config

### Module Config
- GET /api/modules/{name}/config — get settings schema
- PUT /api/modules/{name}/config — update config

## Module Descriptor Enhancement

Each module JSON gets optional `settings_schema` and `ops_actions`:

```json
{
  "name": "kai-chat",
  "settings_schema": {
    "max_history": {"type": "number", "default": 40, "label": "Max chat history"},
    "stream_enabled": {"type": "boolean", "default": true, "label": "Stream responses"}
  },
  "ops_actions": ["restart", "clear_history"]
}
```

## Panels (9 total)

1. Home — KPI row + 4 quadrant cards
2. Modules — dynamic settings/ops/logs from descriptors
3. AI Workforce — provider health + enable/disable toggle
4. Docker — container grid, start/stop/restart/logs
5. Airdrop — subscribers, broadcast, config
6. Infrastructure — Proxmox, GPU, VPN
7. Security — accounts management, API keys, login history
8. Settings — autonomy, scheduler, provider toggles
9. Logs — audit trail with module/severity/date filters

## Implementation Order

1. Docker API endpoints in Kai's api.py
2. Airdrop Hunter admin API (Express routes)
3. Enhance module descriptors with settings_schema
4. Rewrite command_center.html with full redesign
5. Wire up all panels with API calls
