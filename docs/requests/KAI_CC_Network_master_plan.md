# KAI Command Center + Network — Master Plan (2026-09-24)

Investigation findings + phased delivery. Evidence-based; not guesses.

## Findings summary

| Area | State |
|---|---|
| **CC login** | 🔴 **Broken.** Duo login calls `authz.create_session_for()` which **does not exist** → no JWT minted → immediate logout. Plus: any 401 wipes the session; "remember 7 days" is false (defaults to sessionStorage); no token refresh. |
| **WireGuard** | 🟢 **Live.** CT102 wireguard (Proxmox A, 10.6.0.1/24, 3 peers); CT103 headscale (192.168.66.2/29, peers "R1 openwrt", "R2 spectrum"); **kaidash** dashboard :51880 bridged PVE-B→A→CT103. CC is **read-only** today. |
| **proxdash** | ⚪ **Not present/running.** Catalog entries point at unreachable hosts. (kaidash is the real WG dashboard.) |
| **Network dashboard** | 🟡 Exists but broken: `/network/*` returns **401** (not 403) → logs out viewers; topology graph **stale** (wrong SITE-B tailnet IP `100.89.97.76` vs actual `100.122.38.118`; sites offline). |
| **Data monitoring** | 🟢 Exists: `core/infra_usage.py` → `/api/infra/usage` (disk + `/proc/net/dev` for PVE-B/C) + "Data Usage" CC card. ⚠️ No bandwidth trending/vnstat. |
| **NICs (R730xd)** | 4× BCM5720 GbE — `nic0` UP 1 Gb/s (vmbr0); **`nic1–3` DOWN/unused**. Multi-WAN capable, unconfigured. Discovery reads name/ip/mac only. |
| **OPNsense** | ⚪ Stubs only (credentials slotted, no client). |

## Phases

### Phase A (P0) — Fix CC login/session  ← do first
1. **Implement `authz.create_session_for(username, role)`** (mint a real JWT) so Duo login returns a usable `cc_token`; make `duo_sso` use it. *(Primary cause.)*
2. **Differentiate 401 vs 403**: routes that need a capability but have a valid session must return **403**, not 401. SPA clears the session **only** on genuine auth-expiry (401 from auth endpoints), never on capability failure.
3. **Make "remember" honest**: default the checkbox on (localStorage) or relabel to the real TTL (24h); stop silent sessionStorage logout.
4. **Token refresh**: route `refresh_jwt()` (`/auth/refresh`) and have the SPA refresh before 24h.
5. **Harden boot**: a transient `/auth/status` error must not `clearToken()`.
6. Tests + live proof (Duo/password login persists; viewer opening Network does NOT log out).

### Phase B — WireGuard device management (from the CC)
Turn the read-only WG panel into management, against the **real** stack (kaidash/CT103 + CT102):
- List peers with status (pubkey, endpoint, handshake, RX/TX, allowed IPs, label).
- **Add** peer/device: generate keypair server-side, allocate IP, show **QR code**.
- **Manage**: rename/label, enable/**pause**/**resume** (remove/re-add peer or `wg set` toggle), delete.
- **Export configs**: generate client configs for **WireGuard-native**, **DD-WRT**, **OpenWRT** (+ generic) as downloadable files/QR.
- Guardrails: operator-gated writes, confirm destructive, never expose private keys except in the intended client config, audit log.
- Requires a **management API** on CT103 (or a controlled `wg` runner) — design + implement.

### Phase C — Network & Data dashboard (the R730xd multi-NIC design)
A first-class **Network** experience (not another firewall — OPNsense stays the router):
- **NICs**: name, physical port, link, speed, MAC, traffic, errors, VLAN; assign to a network/WAN; up/down.
- **WANs**: Telecel Fibre (Port 1, main), MTN Fibre, Starlink, VPN/WireGuard; status + traffic + failover view.
- **LANs/VLANs**, connected devices, IP/MAC map, bandwidth/traffic, link failures, routing/path status.
- **Network-aware app access** (e.g. `ITManager.local` on MTN, blocked on Telecel) — policy by network, not hard-coded IP.
- **Stable names** (`ProxmoxB.local`, `ITManager.local`) via mDNS/host mapping.
- **OPNsense integration** (API client using the slotted creds) for the firewall layer + read-only status.
- **Data monitoring**: continuous bandwidth + per-interface + disk + alerting (extend `infra_usage`; add vnstat-style trending).

### Phase D — Command Center development (all tabs)
- Make every panel rich: sub-tabs, usage graphs (`data-visualization`), metrics, empty states.
- Fill the thin panels (`loadLearning`, `loadApprovals`, `loadRoadmap`, `loadLogs`, …).
- Global search, better nav grouping, keyboard shortcuts, consistent timing/graphs.

### Phase E — Speed (from the earlier offer)
- ANN/vector index for retrieval (−0.5–0.7 s); small-model routing for simple queries (−40–60%).
- (GPU upgrade remains a hardware decision.)

## Order of execution
A (login) → B (WireGuard mgmt) → C (network/data dashboard) → D (CC polish) → E (speed).
