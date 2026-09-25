# Home Assistant — Smart Home Fabric Deployment (LXC 115)

**Host:** Proxmox B (`pve`, 192.168.1.110) · **CT:** 115 `kai-homeassistant`
**IP:** 192.168.1.115 · **UI:** http://192.168.1.115:8123
**Role:** device/integration **fabric** only. **KAI remains the intelligence and
control authority** (KAI drives HA; HA never holds authority).

## Configuration
- Debian 12 LXC, unprivileged, 2 vCPU / 3 GB / 1 GB swap, 10 GB rootfs on
  `local-lvm`, `nesting=1,keyctl=1`, `onboot=1`, `startup order=6,up=10`.
- **DNS pinned** to `1.1.1.1,8.8.8.8` via `pct set 115 --nameserver`
  (the PVE host's Tailscale DNS `100.100.100.100` is not usable inside the CT —
  this was the cause of the first `apt update` hang).
- Docker 29.8.1 (`systemctl enable docker`), HA Container
  `ghcr.io/home-assistant/home-assistant:stable` (~3.4 GB), `--restart=unless-stopped`,
  `--net=host`, `--privileged` (mDNS/discovery), config at
  `/opt/homeassistant/config`, `/run/dbus` mounted.

## Verified
- `pct status 115` → running; `docker ps` → `homeassistant Up`.
- `http://192.168.1.115:8123` serves the HA UI.
- KAI (CT111) can reach `192.168.1.115:8123`.
- Adapter smoke: `HomeAssistantAdapter("http://192.168.1.115:8123", token="")`
  returns the **typed** error `Home Assistant token is not configured` (no crash).

## Owner action required (one time)
1. Open `http://192.168.1.115:8123` and complete HA onboarding (create the admin).
2. Create a **long-lived access token** (Profile → Security → Long-lived tokens).
3. Store it in KAI Vault (never a file, never the frontend):
   ```
   python -c "from core.vault import set_secret; set_secret('homeassistant','token','<TOKEN>')"
   ```
   Until then, the HA adapter reports its typed "not configured" error and no
   smart-home state is shown as live (no fabrication).

## Rollback
- `pct stop 115 && pct destroy 115` (fabric only; KAI's registry is unaffected).
- Docker data is confined to `/opt/homeassistant/config` inside the CT.
