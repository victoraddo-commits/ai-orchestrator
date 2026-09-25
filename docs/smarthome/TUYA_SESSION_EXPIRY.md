# Tuya cloud session — expiry & renewal (operational note)

**Status:** the device-sharing `tuya_cloud` session expires ~2h after the QR
login. Once expired, Tuya rejects refresh with `1010 token is expired` /
`sign invalid` — a fresh **QR scan** is required (no silent renewal).

## What this affects
- **Cloud control of the 20 off-LAN Tuya devices** — needs a live session.
- **NOT affected:** the on-LAN Tuya device (192.168.1.16) — it uses its
  **local key** from the vault and needs no cloud session.
- **NOT affected:** Home Assistant (separate integration), eWeLink, UPS.

## Renewal procedure (owner, ~2 minutes)
Run the QR flow in `docs/smarthome/TUYA_LOGIN.md`:
1. Mint QR in the HA container (client_id HA_3y9q4ak7g4ephrvke, schema haauthorize,
   scan string `tuyaSmart--qrLogin?token=...`).
2. Scan in Smart Life within ~3 min.
3. `login_result` -> store session as `tuya_cloud` in the vault.

## Follow-up (recommended)
Add a scheduled reminder / auto-detect: when `tuya_cloud` calls fail with
`token is expired`, surface a "Tuya cloud session expired — re-scan needed"
notice in the Command Center (do NOT fabricate device state).
