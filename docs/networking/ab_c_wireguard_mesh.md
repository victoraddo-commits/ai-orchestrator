# Proxmox A ↔ B ↔ C full-mesh WireGuard (`wg-kai`)

Status: **live** (implemented 2026-09-24, owner-approved).
Interface: `wg-kai` · subnet `10.10.10.0/24` · listen **UDP 51900** · `MTU 1200`.

## Why this design

There is no pre-existing A/B/C WireGuard. Before this change:

- A ↔ B: reverse SSH tunnel (`socat`) + Tailscale
- A ↔ C and B ↔ C: Tailscale only
- A's WireGuard (CT102 `10.6.0.0/24`, CT103 headscale) serves **external client
  sites**, not the Proxmox nodes.

The mesh adds a dedicated point-to-point WireGuard fabric between the three
Proxmox hosts without touching CT102/CT103 or any existing SSH/Tailscale path.

### Endpoint choice — tailnet IPs

The three nodes are behind dynamic public IPs, so WireGuard endpoints are the
stable **tailnet** addresses. Reachability was probed first (tailnet ping + SSH
from B to each):

| Node | Hostname | Tailnet IP | LAN IP | Mesh IP |
|---|---|---|---|---|
| A | `proxmox-a` | `100.83.4.27` | `192.168.1.2` / `192.168.99.2` | `10.10.10.1` |
| B | `proxmox-b` | `100.122.38.118` | `192.168.1.110` | `10.10.10.2` |
| C | `proxmox-c` | `100.116.165.100` | `192.168.1.107` | `10.10.10.3` |

All three hosts use the same LAN range `192.168.1.0/24` at their respective
sites, so **LAN subnet routing is deliberately NOT added** (it would collide
with each host's own connected route). `AllowedIPs` are therefore peer `/32`s
only. `net.ipv4.ip_forward=1` is enabled on all three for future routing and
fixes B's pre-existing Tailscale "IP forwarding disabled" warning, but no
forwarded routes are advertised.

## Configuration (private keys masked)

Private keys live only at `/etc/wireguard/wg-kai.key` (mode `0600`); the configs
are mode `0600` and never printed. Public keys (masked):

| Node | `wg-kai` public key |
|---|---|
| A | `tgSCshcl…Kw4QCA=` |
| B | `xny6Me+/…b2XVw=` |
| C | `ipQ+6YaE…otxSs=` |

Each `wg-kai.conf` has the other two nodes as peers with
`PersistentKeepalive = 25` and `AllowedIPs = <peer mesh /32>`.

### Tailscale ↔ WireGuard endpoint loop (important)

Initial bring-up exposed a known Tailscale behaviour: `tailscaled` enumerates
every non-loopback interface as a candidate endpoint, so it advertised
`10.10.10.x:41641` to peers, which routed Tailscale's own WireGuard traffic over
`wg-kai` (double encapsulation → feedback storm; counters climbed to GB/min and
C became unreachable).

Tailscale v1.102.3 `isProblematicInterface` only excludes `zt*`/`wt0`; the
supported `TS_AVOID_INTERFACES`/`TS_AVOID_PREFIX` knobs (upstream PR #17762) are
not in this build. The documented workaround is applied **on the mesh interface
only**, via `wg-quick` `PostUp`/`PostDown`:

```
iptables -I OUTPUT 1 -o wg-kai -p udp --dport 41641 -j DROP
iptables -I INPUT  1 -i wg-kai -p udp --dport 41641 -j DROP
```

This does not affect the mesh (which uses UDP 51900) nor normal Tailscale
traffic (which does not traverse `wg-kai`). After the fix, counters are flat
and Tailscale direct paths are unchanged.

## Firewall

Tailscale's `ts-input` chain already accepts `-i tailscale0`; in addition each
node adds explicit, allow-only rules (never a flush):

```
iptables -I INPUT 1 -i wg-kai -j ACCEPT
iptables -I INPUT 1 -i tailscale0 -s <peer tailnet>/32 -p udp --dport 51900 -j ACCEPT
```

## Enablement / persistence

- `systemctl enable wg-quick@wg-kai` on A, B and C.
- `/etc/sysctl.d/99-wg-kai.conf` → `net.ipv4.ip_forward=1`.
- Pre-change backups:
  - `/root/wg-kai-backup/` on A, B, C (`iptables.<ts>.rules`, `sysctl.conf.<ts>`)

## Verification (2026-09-24)

`wg show wg-kai` on each node shows **2 peers with recent handshakes**; pings
across the mesh are 0% loss in all six directions:

```
B->10.10.10.1  0% loss ~205 ms      A->10.10.10.2  0% loss ~204 ms
B->10.10.10.3  0% loss ~252 ms      A->10.10.10.3  0% loss ~226 ms
C->10.10.10.1  0% loss ~218 ms      C->10.10.10.2  0% loss ~269 ms
```

60 s steady state: only ~tens of bytes of keepalive per peer (no storm).

Regression checks (all intact): Tailscale A↔B direct and A/B↔C via DERP; B
`socat` tunnels on `8008`/`8099`/`8443`/`51880`; CT102 `wg0` (10.6.0.0/24) and
CT103 `wg0` (headscale) peers on A; `pveproxy`; CT111 `kai-scheduler`.

## Command Center visibility

`GET /api/wg/mesh` (operator-gated) returns the live snapshot via the existing
SSH chain `CT111 → PVE-B → {PVE-A, PVE-C}`; the CC WireGuard tab renders a
"Full Mesh · wg-kai (A↔B↔C)" card. Private keys are never returned and public
keys are masked. Implementation: `core/wg_mesh.py` + `core/cc_extra_routes.py`.

## Rollback

```
wg-quick down wg-kai && systemctl disable wg-quick@wg-kai   # per node
```

`wg-quick down` also removes the `PostUp` firewall rules. Backups in
`/root/wg-kai-backup/` restore the pre-change iptables.
