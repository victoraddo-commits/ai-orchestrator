# Command Center — WireGuard Connection Manager + Host-Qualified Labels

**Date:** 2026-09-25
**Status:** Implemented
**Repo:** CT111 `/opt/ai-orchestrator` (branch `runner-kai-2.0-20260918`)

## Problem

The WireGuard tab already contained add/manage/export/QR code, but operators did
not see those features. Root cause: `loadWireguard()` awaited `/api/wg/mesh`,
which SSHes Proxmox A/B/C and takes ~33s (often >20s). The whole panel sat on
`loading…`, so the connection manager never appeared.

Separately, CT/VM VMIDs collide across Proxmox hosts (Proxmox A and B each have a
`102`), so bare IDs are ambiguous.

## Design

### 1. Non-blocking panel
- `loadWireguard()` fetches `/api/wg/peers` first via `apiT(..., 12000)` and
  renders the manager immediately.
- `wgMeshLoad()` loads `/api/wg/mesh` fire-and-forget into a placeholder card
  with its own `apiT(..., 75000)` and a Retry action.
- No panel read blocks another.

### 2. Connection manager
- **Add**: `POST /api/wg/peers` — name, optional static IP, DNS, **mode**
  (`client` full-tunnel | `site-to-site` + `peer_lans`), keepalive, MTU.
  Returns config + QR.
- **List**: status (up/down/paused), address, mode badge, endpoint, allowed IPs,
  handshake age, rx/tx; text filter; auto-refresh toggle (15s).
- **Manage**: pause/resume, rename/retune (`PATCH /api/wg/peers/{pubkey}`),
  delete (confirmed). External peers are read-only.
- **Export**: WireGuard `.conf`, **DD-WRT** script, **OpenWRT** UCI, with copy +
  download, plus bulk "Download all".
- **QR**: large scannable QR, Download PNG (`?raw=1`), copy config, and
  "WireGuard app → + → Scan from QR code" guidance.

### 3. Host-qualified labels
- `core/host_labels.py`: `host_site()` / `guest_label()` → `<KIND><vmid>-<SITE>`
  (`PA`=Proxmox A, `PB`=Proxmox B, `PC`=Proxmox C, `PX`=unknown).
- `core/infra_usage.py` adds `site`/`site_label` per host and `label` per
  CT/VM; the CC mirrors this with the `guestLabel()` JS helper.
- Applied in Containers/Doctor, Infrastructure usage tables, and any guest list.

### 4. Reliability
- `infra_usage` collection budget raised 25s→45s and the Proxmox C hop 16s→30s,
  because a slow node previously dropped the *entire* snapshot.

## Backend changes
- `core/wg_agent.py`: `rename_device()` + `rename` dispatch op.
- `core/wg_peer_service.py`: `rename_peer()`.
- `core/cc_extra_routes.py`: `WgUpdateDevice` + `PATCH /api/wg/peers/{pubkey}`.
- `core/host_labels.py`: new.
- `core/infra_usage.py`: labels + budget.
- `core/kai/command_center.html`: non-blocking loader, manager UI, labels.

## Tests
- `tests/test_host_labels.py` (sites, idempotency, infra_usage integration).
- `tests/test_wg_agent.py` (+rename/retune/dispatch).
- `tests/test_cc_contract.py` unchanged and green; `scripts/cc_contract_check.py`
  `--no-smoke` OK.

## Live verification
- API e2e: create → rename → export wg/ddwrt/openwrt → QR → list → delete all 200.
- Browser (Playwright): manager renders in ~5s, add form exposes both modes and
  reveals peer-LAN input, mesh card resolves async, guests render `CT100-PB` …
  `VM112-PB`.

## Owner actions (unchanged, out of scope)
- WAN `udp/51860` port-forward on Proxmox A's router for external CT102 clients.
- Enable `wg-quick@wg0` on CT102 for boot persistence.
