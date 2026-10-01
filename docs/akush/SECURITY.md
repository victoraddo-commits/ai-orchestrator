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
