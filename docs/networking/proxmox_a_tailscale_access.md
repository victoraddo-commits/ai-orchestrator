# Reaching Proxmox A from the Kai stack (Tailscale via Proxmox B)

Proxmox A is on the Tailscale network and is **not** reachable directly from
CT111. The supported path is: **CT111 -> Proxmox B bare metal (192.168.1.110)
-> Tailscale -> Proxmox A API (100.83.4.27:8006)**.

## Forward
Proxmox B runs a systemd socat forward:
- unit: /etc/systemd/system/pve-a-api-forward.service
- 
- verified: https://192.168.1.110:8009/api2/json/version -> 401 (reachable, auth required)

## Orchestrator
- /etc/kai/proxmox.env (loaded by ai-orchestrator-api drop-in proxmox.conf):
  
- Result: world_model host:pve = online; kai.executive.prioritize critical = [].
