# Akush (Kai Money) — Security Notes

Updated: 2026-10-01 (TASK R1)

## Duo state (ACTUAL, verified)

Duo SSO **is configured and active** on the orchestrator plane:

- `/etc/kai/duo.env` (CT111) holds non-empty `DUO_IKEY`, `DUO_SKEY`,
  `DUO_API_HOST`, `DUO_USERNAME`, `KAI_DUO_ALLOWED_USERS` and is loaded into
  `ai-orchestrator-api.service` via `EnvironmentFile`. Values are never printed
  or logged.
- `core.auth.duo_sso.is_configured()` evaluates **True** in that process env.
- Behavior: Duo functions as a **step-up identity** — orchestrator login and
  high-risk approval (vault reveal) can require a Duo push
  (`core/auth/duo_sso.py`, `core/authz.py`). Push denial or Duo unreachability
  fails closed.
- Not yet exercised end-to-end: no live login has completed a real Duo push
  since activation. First operator login will confirm the push path.

## PWA authentication (akush-core, CT108 :8095)

The PWA's own login path does **not** depend on Duo and is unaffected:

- password (argon2id) login → HttpOnly HMAC-signed cookie (SameSite=Lax) +
  CSRF double-submit cookie;
- destructive actions require a one-time HMAC confirmation token
  (`confirmation_tokens` table) plus, where applicable, approval flow;
- session introspection via CT111 `POST /auth/status` with 30s cache,
  fail-closed when CT111 is unreachable.

Live checks (2026-10-01): login endpoint rejects an empty body with 400;
`GET /api/v1/auth/me` unauthenticated returns 401 `{"error":"unauthorized"}`;
introspection unaffected. The PWA security-card hint now reflects that Duo
step-up is active at the orchestrator (CT108 commit `b3a39b0`).

## Telegram bot token (TASK R1)

- The akush233bot token was uploaded by the operator as a pasted directive
  (`directives/20261001T085430Z-pasted-directive.md`, 95 bytes).
- It has been stored in kai-vault as `secrets/money/telegram_bot_token` via the
  machine-plane API (SET 200 + reveal roundtrip OK).
- The plaintext directive copy was **scrubbed** and replaced with a redaction
  note; sha256 before/after recorded in `directives/.audit.jsonl`
  (`action=secret_scrub`). The token exists ONLY in vault.
- `core/telegram/registry.py` `akush233-bot` `enabled=True` (CT111 commit
  `6f1e218`); the poller materializes the 0600 env file
  `/etc/kai/akush_bot.env` from vault at startup (never logged).
- Note: `akush-telegram.service` runs with `ProtectHome=true`, so the vault
  bearer token is staged at `/etc/kai/vault-mp.token` (0600) and provided via
  `VAULT_TOKEN_FILE` in the unit (backup: `akush-telegram.service.bak-r1`).

## Chat allowlist

- Gate: `AKUSH_TELEGRAM_ALLOWED_CHATS` (unit env) and/or
  `memory/money_notify_prefs.json` `allowed_chats` / `chat_id`
  (`core/money_notify/prefs.py`).
- Current state: **empty**. No unsolicited broadcasts were sent. The allowlist
  will be populated with the operator's chat id after the operator sends the
  first message (e.g. `/start`) to @akush233bot; no known operator chat id
  existed in bot registries or prefs at activation time
  (`BETTING_ADMIN_CHAT_ID` is unset, `device_registry.json` carries none).

## TASK R2 exposure closure (2026-10-01)

- **Tailscale funnel → RESOLVED.** The public funnel
  (https://proxmox-b.tail82a9ca.ts.net → CT111 :8000) was removed with
  `tailscale funnel reset` (operator-authorized). No public listener remains.
  Tailnet-only CC access verified from pve-A over node socat :8443 →
  CT111 :8000 (HTTP 200). Pre-change serve/funnel JSON is preserved at
  pve-B:/root/r2-backups/tailscale-serve-before-20261001.json. The SMS phone
  path (:8770 via sms-worker-tailscale-proxy) was NOT touched by the funnel
  removal (independent socat unit; verified 401-without-token after change).
- **Kai Vault :8120 → RESOLVED (TLS :8443-only).** Consumer inventory:
  orchestrator (core/ai/kai_vault_client.py — already :8443 via VAULT_CA_BUNDLE;
  verified 200/reveal), akush-core CT108 (unit VAULT_URL switched to
  https://192.168.1.107:8443 + NODE_EXTRA_CA_CERTS=/opt/kai-money/state/tls/
  vault-mp-ca.pem pinned CA; fetch-secrets + /health verified), money-center
  docker-compose (config-only; VAULT_URL fixed from stale 192.168.1.117:8120).
  The plain :8120 listener is disabled in CT107 machine_plane.py via
  VAULT_MP_ENABLE_PLAIN=0 (drop-in r2-close-plain.conf; unit backups on CT107).
  Note: commit d791637 shows kai_doctor.py as a new file because it was not
  tracked before (pre-existing); only the :8120→:8443 lines are the R2 change.
- **SMS-worker TLS :8771 — PARTIAL (phone pending).** socat TLS sidecar
  `sms-worker-tls.service` on CT111 proxies :8771 → :8770 (self-signed cert
  SAN: 192.168.1.111, proxmox-b.tail82a9ca.ts.net, localhost). POST
  /webhook/sms over :8771 → 401 without token (listener healthy); :8770 phone
  path unchanged. Operator action: switch phone app URL to
  https://proxmox-b.tail82a9ca.ts.net:8771, then drop :8770.
- **Legacy grants → RESOLVED.** kai_money: no non-akush/postgres roles held
  grants (akush_app scoped per Phase 8; akush_test has zero grants).
  klaus_db: klaus_user scoped to its 6 tables, legal_read SELECT-only —
  untouched by R2.

## TASK R4 — unified gateway entry :8770 (2026-10-01)

- **One URL now serves both the SMS registration webhook and the Akush Money
  dashboard:** `http://proxmox-b.tail82a9ca.ts.net:8770` (and the same port on
  the LAN IP `http://192.168.1.110:8770`). The phone SMS-forwarder app keeps its
  EXISTING base URL — verified against live webhook traffic (POSTs arrive
  through the pve-B listener; the app was never reconfigured).
- **pve-B nginx gateway** (`/etc/nginx/sites-available/akush-gateway.conf` +
  symlink; PVE's own config untouched): `listen 8770` on ALL interfaces;
  `location = /webhook/sms` → `http://192.168.1.111:8770` (kai-sms-worker,
  method/body/headers preserved, 64k body cap, minimal no-body/no-query access
  log — SMS bodies and tokens are never logged); `location /` →
  `https://192.168.1.118:8095` (akush-core PWA; `proxy_ssl_verify off` is
  deliberate: upstream cert is self-signed for a PINNED LAN host, and every
  transport path (LAN or tailnet) is already mesh-encrypted, so hostname/CA
  verification adds nothing here).
- **Superseded:** `sms-worker-tailscale-proxy.service` (socat tailnet-IP →
  CT111 :8770) was stopped and disabled on pve-B — nginx now owns :8770 for
  both tailnet and LAN. Unit file kept for rollback (re-enable + disable nginx
  site). `sms-worker-tls.service` (:8771 CT111 TLS sidecar) is kept as-is but
  is now OPTIONAL/superseded for phone migration: the unified :8770 entry
  serves the phone already; do not migrate the phone to :8771.
  `cc-tailscale-proxy.service` (:8443) untouched — CC tailnet access re-verified
  200 after the change.
- **Transport tradeoff (honest):** the unified entry is plain HTTP inside the
  tailnet/LAN mesh (tailnet traffic is WireGuard-encrypted; LAN traffic is
  trusted-fabric). Consequence: akush-core issues session cookies with the
  `Secure` flag (upstream is HTTPS-terminated), so interactive BROWSER logins
  through the http:// gateway will not persist cookies in some browsers; the
  API-level cookie login is fully functional (verified end-to-end below). When
  browser login is needed, use the direct origin `https://192.168.1.118:8095`
  (still available and unchanged). No auth logic was weakened for the gateway.
- **Verified (R4):** phone-path webhook 200 through the gateway → bridge →
  akush-core row in kai_money; PWA via same URL (200 + CSP header); cookie
  login (pwa-e2e-viewer) via gateway → 200 + authenticated /api/v1/session
  roundtrip via="cookie"; dashboard from Proxmox B LAN via 192.168.1.110:8770 →
  200 (from CT111 AND VM104); CT111 pytest test_sms_webhook +
  test_money_sms_bridge 25/25; OTP-shaped webhook POST → stored as
  `[REDACTED]`, digits absent from CT111 inbox AND akush-core DB (0 rows).
