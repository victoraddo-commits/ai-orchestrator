# Akush Money — Production Readiness Report (§79, Phase 9)

**Date:** 2026-10-01
**Verified by:** independent re-run of every suite, live state probes, and code inspection on this date. No prior claims trusted; every number below was regenerated in this session.
**Scope:** CT108 (`/opt/kai-money`, akush-core :8095) + CT111 (`/opt/ai-orchestrator`, bridge/notify/telegram/CC, PG16 `kai_money`), with vault reads from CT107.

---

## 1. Architecture

Single source of truth: **akush-core (CT108, HTTPS :8095)** owns the only money ledger. Verified by inspection:

- Telegram bot (`core/money_telegram`) never touches the money DB — `poller.py` calls akush-core over HTTPS as a `service:bot` principal (registry.py:103–127, code comment verified: "the bot itself never touches the money DB").
- SMS bridge (`core/money_sms/bridge.py`) POSTs to `https://192.168.1.118:8095/internal/sms/ingest` with a vault-issued service token; OTP-shaped bodies are refused at transport (`bridge.py:287–288`).
- Command Center panel (`cc_extra_routes.py /api/money/*`) reads through akush-core via `_akush_call()` — no parallel query path.
- CT111 has **zero** direct DB connections to `kai_money` from money modules (grep for `psycopg|asyncpg|connect(` in `core/money_sms`, `core/money_notify`, `core/money_telegram`: no hits). The only `kai_money` string outside scripts is a registry metadata field (`telegram/registry.py:108 owner_module="kai_money"`).

## 2. Components

| Component | Location | Port / Service | Command |
|---|---|---|---|
| akush-core API | CT108 `/opt/kai-money/services/akush-core/` | HTTPS :8095, binds 192.168.1.118; `akush-core.service` | `systemctl restart akush-core` |
| akush-core tests | `services/akush-core/test/` (31 test files + fixtures) | — | `node --test --test-concurrency=1` |
| Migrations 001–016 | CT108 `/opt/kai-money/services/money-center/migrations/` | — | `node src/migrate.js` (applied 16/16 in `kai_money._migrations`) |
| PWA | CT108 `services/akush-core/public/` | served by akush-core :8095 | browser `https://192.168.1.118:8095/` |
| kai-sms-worker (SMS receiver + bridge) | CT111 `core/sms/` + `core/money_sms/` | :8770 (HTTP), `kai-sms-worker.service` | `systemctl restart kai-sms-worker` |
| Bridge tests | CT111 `tests/test_money_sms_bridge.py` (20) | — | `.venv/bin/python -m pytest tests/test_money_sms_bridge.py` |
| Notify relay | CT111 `core/money_notify/` (gate, formatter, relay, prefs) | — | `tests/test_money_notify.py` (28) |
| Telegram bot | CT111 `core/money_telegram/` (poller, handlers, menu, client, token) | `akush-telegram.service` | `tests/test_money_telegram.py` (21) |
| CC panel | CT111 `core/cc_extra_routes.py` (`/api/money/*`) | ai-orchestrator-api :8000 (HTTPS) | `tests/test_money_cc_panel.py` (18) |
| PG backup | CT111 `scripts/akush_pg_backup.sh` + `akush-pg-backup.timer` (+`akush-pg-restore-test.sh`) | daily 03:00 UTC | `scripts/akush_pg_backup.sh` |
| Vault (CT107) | `kai-vault` | :8443 HTTPS, :8120 HTTP (operator-gated) | — |

**Discovered/reused:** PG16 instance on CT111, kai-vault (CT107), Kai event bus, CC authz (bridge token/session/trusted-proxy), vzdump daily job. **Upgraded:** CC overview (`cc_extra_routes.py` money block), auth.js (viewer tier fix), bridge observability state. **Newly created:** akush-core, SMS bridge, fingerprint dedup, money_notify gate, money_telegram, CC money panel, PWA, encrypted PG backup + restore drill.

## 3. Database schema (migrations 001–016)

All 16 applied (verified: `SELECT COUNT(*) FROM _migrations` = 16).

- **001 core** — append-only ledger tables; UPDATE/DELETE rejected by triggers (DB-level backstop).
- **002 intelligence** — exposure snapshots, portfolio reporting (§47).
- **003 kai_account** — KAI's own account + profit-transfer treasury.
- **004 kai_audit** — immutable KAI audit reports.
- **005 wallets_auth** — crypto wallet registry + local argon2id login beside Kai ID SSO.
- **006 profit_engine** — financial account registry, Tangem treasury flow (no parallel systems).
- **007 akush_core** — profiles, accounts, balances, transaction ledger, audit; append-only triggers.
- **008 sms_intelligence** — OTP-redacted SMS mirror, derived events, parser registry.
- **009 financial_inbox** — financial events + human inbox + reconciliation ("inbox is the ONLY place a suggestion becomes money data").
- **010 planning** — income, commitments, debts, paydays, budgets, goals, sinking funds, months, scenarios.
- **011 reports_docs** — subscriptions, net-worth components, documents, notifications.
- **012 networth_history** — point-in-time net-worth snapshot series.
- **013 sms_pipeline** — lifecycle columns, `processed_at` idempotency marker.
- **014 planning ext** — income occurrences (expected|actual), payday allocation planning, planning kinds.
- **015 intelligence** — recurring_patterns detection store, cadence/status CHECK extension (data-preserving).
- **016 kind_alignment** — codify widened CHECK enums for commitment/debt/income kinds.

**DB verification (live):** 48 FKs on app tables; 281 distinct indexes; append-only triggers active on `audit_events`, `audit_log`, `balance_observations`, `kai_audit_reports`, `ledger_entries`, `security_events`, `transaction_lines`, `transactions`; roles `akush_app` (owner of `kai_money`, super=false createdb=false createrole=false) and `akush_test` (owner of `kai_money_test`); no third-party grants on money tables (only system-catalog PUBLIC/pg_read_all_stats, normal).

**Table row counts (live, kai_money):** accounts=4, transactions=12, financial_events=35, financial_event_inbox=32, sms_messages=10.

## 4. API endpoints (akush-core, HTTPS :8095)

Full registered list (extracted from route table):

- Health/observability: `GET /health`, `GET /ready`, `GET /internal/health/detailed` (service principal only — verified 403 unauthenticated, 200 with bridge token).
- Accounts/lifecycle: `GET/POST /api/v1/accounts`, `GET/PATCH /api/v1/accounts/:id`, `POST /api/v1/accounts/:id/{confirm,activate,reject,deactivate}`, `GET /api/v1/accounts/:id/{balances,identifiers,reconciliations,summary}`, `POST /api/v1/accounts/:id/{balances,identifiers}`.
- Transactions: `GET/POST /api/v1/transactions`, `GET /api/v1/transactions/:id`, `POST /api/v1/transactions/:id/adjust` (confirmation-token gated, idempotency-keyed).
- Inbox/reconciliation: `GET /api/v1/financial-inbox(/:id)`, `POST /api/v1/financial-inbox/:id/act`, `POST /api/v1/reconciliations`, `POST /api/v1/reconciliations/:id/resolve`.
- Income: `GET/POST /api/v1/income-sources(/:id…)`, `POST .../{occurrences,generate-occurrences}`, `GET /api/v1/income/{expected,actual-vs-expected}`.
- Commitments: `GET/POST /api/v1/commitments(/:id…)`, `POST .../{occurrences,generate-occurrences,pay}`, `POST .../occurrences/:oid/mark-paid`, `POST /api/v1/occurrences/:id/mark-received`.
- Debts: `GET/POST /api/v1/debts(/:id…)`, `POST .../{payments,schedules/generate}`.
- Paydays: `GET/POST /api/v1/paydays(/:id…)`, `POST .../{apply-plan,generate-plan,plan}`, `PUT .../allocations`, `POST .../allocations/:aid/adjust`.
- Budgets: `GET/POST/PATCH/DELETE /api/v1/budgets(/:id…)`, `GET .../{current,status,lines,periods}`, `POST .../periods(/:pid/close)`, `PUT .../lines`.
- Goals/sinking funds: savings-goals + sinking-funds CRUD + `{contributions,progress}`.
- Net worth: `GET /api/v1/net-worth(/history)`, `POST /api/v1/net-worth/snapshot`.
- Assets/liabilities: CRUD.
- Search: `GET /api/v1/search`, `POST /api/v1/search/ask`.
- SMS read views: `GET /api/v1/{sms-messages,sms-messages/:id,sms-parsers,sms-ingestion-status}`, `GET /api/v1/{anomalies,security-events,audit-log}`.
- Profile/settings/session: `GET /api/v1/{profiles(/:id),settings,session}`, `PUT /api/v1/settings`, `POST /api/v1/auth/{login,logout}`, `POST /api/v1/confirmations/prepare`.
- Utilities: `GET /api/v1/utilities/:utility/summary`.
- Internal ingest: `POST /internal/sms/ingest` (service token only).

## 5. SMS architecture

Single receiver preserved: **kai-sms-worker (CT111, :8770)** is the only webhook receiver (`core/sms/service.py` DEFAULT_PORT 8770; `core/sms/server` is the same service — no second receiver exists). Flow: webhook → OTP redaction + E.164 normalization + classification → fingerprint dedup (LRU in bridge state) → `core.money_sms.bridge` bounded FIFO worker pool → HTTPS POST to `akush-core /internal/sms/ingest` with service token (retry, exponential backoff) → akush-core classifies, appends `sms_messages` + `sms_events`, derives financial events into the inbox (human-gated). OTP: redacted before leaving CT111; bridge additionally refuses OTP-shaped bodies at transport (defense-in-depth, verified in code + tests). Dead-letter: overflow/failures land in `memory/money_sms_bridge_state.json` dead_letter and surface on CC `/api/money/overview` (`bridge.dead_letter_count`) — visibility verified live.

## 6. Account discovery

Lifecycle enforced: SMS-derived accounts start as `CANDIDATE` (`ACCOUNT_DISCOVERED` event, nothing official); only the human `POST /api/v1/accounts/:id/confirm` (`USER_CONFIRMED`) promotes a candidate and learns identifiers — no auto-trust. Fixtures 1–3 + confirm-endpoint tests cover this (`sms_pipeline.test.js`, `accounts.test.js`). Live state agrees: accounts by_status {CANDIDATE: 3, DETECTED: 1}.

## 7. Balance intelligence

`BALANCE CHANGE ≠ income`: a balance delta with no matching transaction yields `RECONCILIATION_REQUIRED` with the ledger untouched (fixture 4); a delta matching a transaction yields `MATCH_SUGGESTED` with evidence linked (fixture 5). Kind alignment codified in migration 016 + `kind_alignment.test.js` (21 tests). No balance observation ever writes an income/expense row directly.

## 8. Telegram integration

Interface, not truth: bot reads through akush-core API (`service:bot` principal); inbound handlers are allowlist-gated (`handlers.py:39 allowed_chats`, reject path verified `handlers.py:285–286`); notify fan-out gated by `core/money_notify/gate.py`. Fail-safe disabled state: registry `enabled=False` for `akush233-bot` (registry.py:124), poller idles loudly (`poller.py:84–89`), token env file `/etc/kai/akush_bot.env` absent — verified. Token lives only in vault `secrets/money/telegram_bot_token`.

**Activation checklist (operator, pending token):**
1. Provision bot token into vault `secrets/money/telegram_bot_token` (CT107).
2. Flip `enabled=True` for `akush233-bot` in `core/telegram/registry.py`.
3. Restart `akush-telegram.service`; reload orchestrator so notify re-gates.
4. Allowlist the operator chat in notify prefs; verify allowlist reject on unknown chat.
5. Confirm `/api/money/overview` bot block shows the bot idling→active.

## 9. PWA

Served by akush-core itself (`public/`, same origin, no third-party origins). Verified: CSP whitelist-only (`default-src 'self'`, `script-src 'self'`, `style-src 'self'`, `connect-src 'self'`, no `unsafe-*` outside self), applied to every static response (`server.js:118–124,148`); **API responses are `cache-control: no-store`** (`server.js:53–58`); static assets 300s except html/sw/manifest no-store; cookies HttpOnly + SameSite=Lax, HMAC-signed, JS-unreadable (`user_session.js:4,119–120`); CSRF double-submit cookie for the PWA session. `pwa_auth.test.js`: 20/20.

## 10. Security architecture

§71 hardening verified by re-run: HTTPS on :8095 (self-signed, LAN-pinned), auth matrix enforced (user tier: operator→`user`, viewer→read-only `viewer`, unknown roles fail closed; service principals via vault-issued per-service tokens; timing-safe compare), confirmation-token HMAC for destructive actions (`confirmation_tokens` table, `confirm.js`), idempotency keys with replay-payload-mismatch detection (`transactions.js:45–56`), rate limiting, body limit 1 MiB, redacted pipeline errors, security events + audit log (append-only triggers), §72 SMS-as-data (instruction-override SMS treated as data, never executed). Secrets: vault-only (CT107), never printed; env file `akush_bot.env` 0600 pattern.

## 11. Authentication

Three principals: (1) human PWA — local argon2id login or CT111-issued session, introspected via CT111 `/auth/status` with 30s cache, fail-closed on CT111 unreachable; (2) viewer — read-only, denied by every write gate; (3) service — vault `secrets/money/service_tokens` JSON map (`bridge`, `bot`, …), each request matched timing-safe. Destructive endpoints additionally require a confirmation token (`POST /api/v1/confirmations/prepare` → one-time HMAC token). Tests: `auth.test.js` 9/9, `pwa_auth.test.js` 20/20, `security_phase8.test.js` 15/15.

## 12. Duo

**BLOCKED — unconfigured.** No Duo env in `ai-orchestrator-api.service`/`.env`; only design references in `core/authz.py:184–187` (Duo push approval as step-up identity). Nothing to verify until the operator provisions Duo SSO.

## 13. Tailscale (funnel flag)

Funnel is **ON** on the Proxmox B host: `https://proxmox-b.tail82a9ca.ts.net` proxies to `https+insecure://192.168.1.111:8000` (CC API only). The PWA (:8095) is **not** funneled — money surface stays LAN-only. The funneled CC API is behind CC operator auth. Note: the proxy upstream uses `https+insecure` (self-signed upstream) — acceptable for the auth-gated CC, but the flag decision is operator-owned.

## 14. Command Center

CC money panel live and green: `GET /api/money/overview` returned 200 with real aggregates (health ok, accounts 4, sms 10/24h, dead_letters 0 core-side, obligations 7d, backup ok, bridge block present). Auth gate `_req_op` (bridge token / CC session / trusted-proxy headers) verified; `tests/test_money_cc_panel.py` 18/18 including `overview_requires_auth` and OpenAPI route presence.

## 15. Backup / recovery

- `akush-pg-backup.timer` **active**, daily 03:00 UTC, Persistent=true.
- App-level backup: `pg_dump kai_money → gzip → openssl AES-256-CBC PBKDF2 (600k iters)`, key fetched from vault at runtime (never printed/disked); new archive decrypt-tested + `gzip -t` on creation; all retained archives re-verified each run; retention 7. Live run this session: `akush-kai_money-20261001T084131Z.sql.gz.enc` (46,512 B, `retention_failures:0`).
- **Restore drill (§60/§74):** first run this session exposed a real defect — the drill compared the restored snapshot against **live** counts (moving target) and false-failed (dst_sms 8 vs src 12 after SMS kept arriving). Diagnosed and fixed minimally: backup now writes a `.meta.json` with dump-time counts; drill prefers meta counts. Re-run: **PASS** (82/82 tables, 12/12 SMS).
- vzdump daily 03:30 job includes **CT108** in vmid list (plus CT111); zstd, stop mode, kai-c storage.

## 16. Testing

**CT108 — akush-core, Node `node --test --test-concurrency=1`: total 248 tests, 248 pass, 0 fail, 0 skipped** (full-suite run, 86.9s). Per file: accounts 8, auth 9, balances 3, bot_actor 4, budgets 5, commitments 9, debts 9, goals 7, inbox 7, income 12, intelligence_anomalies 10, intelligence_forecast 3, intelligence_forgot 3, intelligence_nlquery 8, intelligence_recurring 16, intelligence_safetospend 3, intelligence_scan 2, intelligence_scheduler 4, kind_alignment 21, networth 2, paydays 9, pwa_auth 20, recur 9, search 2, security_phase8 15, sms_classifier 12, sms_pipeline 12, sms_registry 4, transactions 11, utilities 7 (+1 test in each of `sms_fixtures.js`, `helpers.js` — both files are also part of the suite glob; 246+2=248).

**CT111 — money/CC/pytest, `.venv/bin/python -m pytest`: total 150 tests, 150 pass, 0 fail** (29.7s). Per file: money_sms_bridge 20, money_notify 28, money_telegram 21, money_cc_panel 18, phase8_security 6, sms_manager 12, sms_webhook 5, sms_security 7, sms_e164 11, account_registration_api 22.

**Grand total: 398 tests, 398 pass, 0 fail.**

## 17. Security testing

§71 suites re-run in this session: CT108 `security_phase8.test.js` 15/15 (HTTPS, auth matrix, viewer gate, confirmation tokens, rate limit, error redaction, service-token auth); CT111 `test_phase8_security.py` 6/6; supporting: `sms_security` 7/7 (OTP never forwarded, malicious SMS inert), `sms_webhook` 5/5 (single receiver, auth), `pwa_auth` 20/20, `auth.test.js` 9/9, OTP fixtures (§72 case 6) and malicious-SMS fixture (§72 case 7) green. Secret greps over both codebases: no hardcoded secrets/tokens/passwords/OTP (vault-only patterns confirmed in bridge/backup/telegram token code).

## 18. Known limitations

1. **kai-sms-worker :8770 is plain HTTP on 0.0.0.0** — TLS on the webhook receiver is operator-gated (needs webhook cert/key + sender trust store).
2. **Vault :8120 HTTP listener on 0.0.0.0 (CT107)** alongside :8443 HTTPS — restricting :8120 is operator-gated.
3. **Bridge dead-letter holds 12 historical entries** (development-phase transport `post_failed`), no replay mechanism — operator-gated purge/replay.
4. **Duo unconfigured** (step-up identity exists in design only) — blocked.
5. **Telegram bot token unprovisioned** — bot fail-safe disabled (by design) — blocked.
6. **akush_app owns the whole `kai_money` database** (broad within the DB; no superuser flags; infra tables owned by `postgres` and write-restricted) — acceptable, but ownership scoping could be tightened.
7. **Dead code:** 64 `.bak`/`.backup` files in CT111 `core/`, 10 in CT108 `services/akush-core/src/` (incl. `auth.js.bak-phase7`, `server.js.orig`, `transactions.js.bak-phase6`…). Untracked; candidates for cleanup.
8. **Tailscale funnel proxies CC :8000 with `https+insecure` upstream** — self-signed upstream trust is implicit; flag rotation is operator-owned.
9. CT111 carries a dirty worktree (uncommitted non-money changes predating this session) — not touched.

## 19. Status table

| Component | Status | Evidence (this session) |
|---|---|---|
| akush-core API (CT108 :8095, HTTPS) | VERIFIED | 248/248 tests; `/health` 200 `{db ok, latency 16ms}`; live service active |
| kai_money schema, migrations 001–016 | VERIFIED | 16/16 applied; 48 FKs, 281 indexes; 8 append-only triggers live |
| akush_app least-privilege | VERIFIED | super/createdb/createrole all false; scoped grants; infra tables read/insert-restricted |
| Single source of truth (no second ledger) | VERIFIED | CT111 money modules: zero direct DB connections; all writes via `:8095` API |
| Auth matrix + fail-closed | VERIFIED | auth.test.js 9, pwa_auth 20, security_phase8 15; CT111 unreachable → fail closed |
| Confirmation tokens (destructive ops) | VERIFIED | `confirm.js` + confirmation_tokens table; §71 tests |
| Idempotency keys | VERIFIED | transactions.js:45–56 replay-mismatch tests |
| SMS pipeline (single :8770 receiver, bridge, dedup) | VERIFIED | sms tests green; live counters forwarded=11 duplicates=5; only one receiver in codebase |
| OTP containment (never leaves CT111) | VERIFIED | redaction + transport refusal (bridge.py:287); sms_security 7/7 |
| §72 acceptance fixtures (8 mandated cases) | VERIFIED | sms_pipeline.test.js 12/12 incl. all 8 §72 cases + 2 extras |
| Account discovery lifecycle (no auto-trust) | VERIFIED | fixtures 1–3; live accounts {CANDIDATE:3, DETECTED:1} |
| Balance intelligence (CHANGE ≠ income) | VERIFIED | fixtures 4–5; kind_alignment 21/21 |
| Telegram bot | BLOCKED | token absent (`/etc/kai/akush_bot.env` missing), `enabled=False` fail-safe idle; built + 21/21 tests |
| Telegram allowlist + fail-safe | VERIFIED | handlers.py allowlist reject; poller idle-logging disabled state |
| Command Center panel | VERIFIED | money_cc_panel 18/18; live `/api/money/overview` 200 with aggregates |
| PWA (CSP, no-store, cookie security) | VERIFIED | pwa_auth 20/20; CSP/no-store/HttpOnly verified in source |
| Observability (health, dead-letter visibility) | VERIFIED | /health + /internal/health/detailed live (service-gated); CC bridge block visible |
| Bridge dead-letter hygiene | PARTIALLY VERIFIED | visibility live; 12 historical entries unreplayed |
| Encrypted PG backup | VERIFIED | live run 2026-10-01T084131Z, AES-256/PBKDF2, integrity re-verified, retention ok |
| Restore drill | VERIFIED | PASS after meta-count fix (82/82 tables, 12/12 SMS); defect found + fixed this session |
| vzdump CT108 inclusion | VERIFIED | daily jobs.cfg vmid list contains 108 |
| Secrets handling (vault-only) | VERIFIED | greps clean both codebases; vault-fetch patterns in code |
| Exposed ports audit | PARTIALLY VERIFIED | binds documented (see limitations #1/#2): 8770 HTTP 0.0.0.0; 8000 0.0.0.0 TLS; 8099 0.0.0.0; 8095 LAN-bound; vault 8120 HTTP 0.0.0.0 |
| Duplicate-implementation sweep | VERIFIED | no second SMS receiver/ledger/event bus found (`core/sms/service.py` IS the receiver) |
| Dead code | PARTIALLY VERIFIED | inventoried (64 + 10 files); not removed (operator-gated) |
| Duo | BLOCKED | unconfigured (no env, no Duo config) |
| Tailscale funnel | PARTIALLY VERIFIED | funnel ON → CC :8000 only; end-to-end external reachability not exercised from WAN |
| SMS-worker TLS | NOT VERIFIED | plain HTTP receiver — needs operator cert provisioning |
| Overall | **READY with operator-gated items** | 398/398 tests; live services green; 4 BLOCKED/PENDING items, all operator-gated |

**Honesty note:** not 100% — Telegram and Duo are blocked on operator provisioning; SMS-worker TLS, vault :8120 exposure, funnel flag, and legacy grant/role cleanup are operator-gated; bridge dead-letter backlog has no replay path.
