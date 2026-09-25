# Tuya / Smart Life cloud login (device-sharing) — verified procedure

Local Tuya devices speak an encrypted protocol; a per-device **local key** is
required. Keys are obtained once from the Smart Life cloud via the same
device-sharing flow Home Assistant uses, then stored in KAI's encrypted vault
(`tuya:<device_id>`). No Tuya IoT developer account is needed.

## Exact protocol (this is what HA uses; earlier attempts were wrong)

| Field | Correct value |
|---|---|
| client_id | `HA_3y9q4ak7g4ephrvke` |
| schema | `haauthorize` |
| QR scan string | `tuyaSmart--qrLogin?token=<token>` |

Wrong values (client_id `HA_3y9q5u4ngmftpn7q`, schema `smartlife`, or a bare
token) make the Smart Life app fail to scan/login.

## Procedure
1. Smart Life app → Me → Settings → Account & Security → **User Code**.
2. Mint QR inside the HA container:
   `LoginControl().qr_code(CLIENT_ID, SCHEMA, USER_CODE)` →
   build `tuyaSmart--qrLogin?token={result.qrcode}` → render QR.
3. Deliver to Telegram (`KaiEnzo_bot`), scan within **~3 min**.
4. `LoginControl().login_result(token, CLIENT_ID, USER_CODE)`.
5. `Manager(...).update_device_cache()` → each device's `local_key`.
6. Store keys: vault `tuya:<id>`; register devices in the canonical registry.

## Gotchas (all hit and resolved 2026-09-25)
- Token expiry → Tuya `E0020003 "Login failed, please scan and try again"`.
  Mint and scan within the window.
- The LXC resolver intermittently fails on Tuya's CDN; retry.
- Telegram delivery: the working bot is `KaiEnzo_bot` (`KAI_TELEGRAM_BOT_TOKEN`
  in `/opt/ai-orchestrator/.env`); `deerude_bot_token` is 401/dead.
- The Smart Life account's devices may **not** be on this LAN (the account here
  returned WAN IPs for a different site). Matching a physical LAN device to an
  account device is a separate step.
