# KAI — CANONICAL NODE IDENTITY (do not confuse)

| Name | Hostname | LAN IP | Role | Notes |
|---|---|---|---|---|
| **Proxmox A** | `pve` | 192.168.1.2 / 192.168.99.2 / 100.83.4.27 | **remote site node** | Hosts CT100 claude-code, CT102 wireguard, CT103 headscale, VM101 OPNsense. Reached via PVE-B → Tailscale. |
| **Proxmox B** | `pve` | **192.168.1.110** | **THE SERVER (this host)** | Hosts VM104/VM112 + all LXC 100–115 incl. orchestrator, HA, and **the Sollatek UPS**. |
| **Proxmox C** | `pve` | 10.30.30.2 (direct link) | backup host | NFS /srv/kai-backups; no guests. Tailscale removed. |

**Rule:** "the server" = **Proxmox B** (`192.168.1.110`). The UPS is connected to **Proxmox B**.
Never attribute A-side devices to B or vice versa.
