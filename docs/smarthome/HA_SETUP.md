# Home Assistant — full setup (2026-09-25)

**CT 115 `kai-homeassistant`** (Proxmox B, 192.168.1.115) — HA Container, `--net=host`.

## What was missing (root cause)
HA was installed and onboarded, but had **only the default integrations**
(sun, analytics, cast, met…). It had **no smart-home integration**, so it was
"running but connected to nothing". Tuya was never added.

## Setup performed
1. Verified HA onboarding complete (user, core_config, analytics, integration).
2. Started the **Tuya** config flow via the HA API and completed device-sharing
   login (user code `Ca80FKp`, QR scheme `smartlife`, scan string
   `tuyaSmart--qrLogin?token=…`).
3. Config entry created: domain `tuya`, title `victoraddo@gmail.com`,
   `entry_id 01M3D2K7PP5FRX7TEP44YKCSJ7`, **state `loaded`**.
4. HA now reports **55 entities**, including **14 Tuya switches**
   (office Switch 1, water pump Socket 1, Hall 3 gang, corridor 2 gang,
   outside disco, outside trees, Multi-mode Gateway…).
5. KAI registry re-synced from HA → **40 homeassistant devices** (68 total estate).

## Control-plane note (important)
There are now **two** paths to Tuya: **KAI (authoritative)** and **HA**. Per the
directive's "no duplicate control plane" rule, **KAI remains the authority**;
HA is an additional fabric/observatory. Either can control; keep writes routed
through KAI so audit/AgentGuard still apply.

## Operational notes
- HA long-lived token is stored encrypted in KAI's vault (`homeassistant`).
- Tuya in HA refreshes via its own login; if it lapses, re-run the HA Tuya flow
  (or KAI's `docs/smarthome/TUYA_LOGIN.md`).
- Start/stop: `pct exec 115 -- docker restart homeassistant`; unit is
  `onboot=1` so it returns after reboot.
