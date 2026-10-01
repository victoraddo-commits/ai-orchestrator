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
| akush-core tests | `services/akush-core/test/` (35 test files + fixtures) | — | `node --test --test-concurrency=1` |
| Migrations 001–016 | CT108 `/opt/kai-money/services/money-center/migrations/` | — | `node src/migrate.js` (applied 16/16 in `kai_money._migrations`) |
| PWA | CT108 `services/akush-core/public/` | served by akush-core :8095 | browser `https://192.168.1.118:8095/` |
| kai-sms-worker (SMS receiver + bridge) | CT111 `core/sms/` + `core/money_sms/` | :8770 (HTTP), `kai-sms-worker.service` | `systemctl restart kai-sms-worker` |
| Bridge tests | CT111 `tests/test_money_sms_bridge.py` (20) | — | `.venv/bin/python -m pytest tests/test_money_sms_bridge.py` |
| Notify relay | CT111 `core/money_notify/` (gate, formatter, relay, prefs) | — | `tests/test_money_notify.py` (28) |
| Telegram bot | CT111 `core/money_telegram/` (poller, handlers, menu, client, token) | `akush-telegram.service` | `tests/test_money_telegram.py` (21) |
| CC panel | CT111 `core/cc_extra_routes.py` (`/api/money/*`) | ai-orchestrator-api :8000 (HTTPS) | `tests/test_money_cc_panel.py` (18) |
| PG backup | CT111 `scripts/akush_pg_backup.sh` + `akush-pg-backup.timer` (+`akush-pg-restore-test.sh`) | daily 03:00 UTC | `scripts/akush_pg_backup.sh` |
| Vault (CT107) | `kai-vault` | :8443 HTTPS only (R2: plain :8120 closed) | akush-core unit: VAULT_URL=https://192.168.1.107:8443 + NODE_EXTRA_CA_CERTS pinned CA (unit outside repo, backup /root/r2-bkp-akush.service) |

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
- Statement imports (§38 G1): `POST /api/v1/imports/upload` (multipart, magic-byte sniffed), `GET /api/v1/imports(/:id)` (batch + per-row status/confidence/match), `POST /api/v1/imports/:id/row/:rid/override`, `POST /api/v1/imports/:id/commit` (confirm-gated).
- Documents/receipts/OCR (§37 G2): `POST /api/v1/documents` (multipart ≤10 MB, sha256 dedup), `GET /api/v1/documents(/:id)`, `DELETE /api/v1/documents/:id` (confirm-gated), `POST /api/v1/receipts/:id/confirm` (confirm-gated; uncertain OCR never auto-commits), `POST /api/v1/receipts/:id/attach` (confirmed receipts only; link-only), `GET /api/v1/transactions/:id/receipts`.
- Month close (§40 G3): `GET /api/v1/months`, `GET /api/v1/months/:ym/close-preview` (real-aggregate checklist, ok/warn/bad), `POST /api/v1/months/:ym/close` (confirm-gated; refuses blocking items unless force+reason), `POST /api/v1/months/:ym/reopen` (operator-only, reason mandatory, audited).
- Simulators (§26/§31 G4 — read-only, kind:simulation): `POST /api/v1/simulations/debt-payoff`, `POST /api/v1/simulations/cashflow`, `POST /api/v1/simulations/save`, `GET /api/v1/simulations`, `DELETE /api/v1/simulations/:id` (confirm-gated).
- Internal ingest: `POST /internal/sms/ingest` (service token only).

## 5. SMS architecture

Single receiver preserved: **kai-sms-worker (CT111, :8770)** is the only webhook receiver (`core/sms/service.py` DEFAULT_PORT 8770; `core/sms/server` is the same service — no second receiver exists). Flow: webhook → OTP redaction + E.164 normalization + classification → fingerprint dedup (LRU in bridge state) → `core.money_sms.bridge` bounded FIFO worker pool → HTTPS POST to `akush-core /internal/sms/ingest` with service token (retry, exponential backoff) → akush-core classifies, appends `sms_messages` + `sms_events`, derives financial events into the inbox (human-gated). OTP: redacted before leaving CT111; bridge additionally refuses OTP-shaped bodies at transport (defense-in-depth, verified in code + tests). Dead-letter: overflow/failures land in `memory/money_sms_bridge_state.json` dead_letter and surface on CC `/api/money/overview` (`bridge.dead_letter_count`) — visibility verified live.

## 6. Account discovery

Lifecycle enforced: SMS-derived accounts start as `CANDIDATE` (`ACCOUNT_DISCOVERED` event, nothing official); only the human `POST /api/v1/accounts/:id/confirm` (`USER_CONFIRMED`) promotes a candidate and learns identifiers — no auto-trust. Fixtures 1–3 + confirm-endpoint tests cover this (`sms_pipeline.test.js`, `accounts.test.js`). Live state agrees: accounts by_status {CANDIDATE: 3, DETECTED: 1}.

## 7. Balance intelligence

`BALANCE CHANGE ≠ income`: a balance delta with no matching transaction yields `RECONCILIATION_REQUIRED` with the ledger untouched (fixture 4); a delta matching a transaction yields `MATCH_SUGGESTED` with evidence linked (fixture 5). Kind alignment codified in migration 016 + `kind_alignment.test.js` (21 tests). No balance observation ever writes an income/expense row directly.

## 8. Telegram integration

Interface, not truth: bot reads through akush-core API (`service:bot` principal); inbound handlers are allowlist-gated (`handlers.py:39 allowed_chats`, reject path verified `handlers.py:285–286`); notify fan-out gated by `core/money_notify/gate.py`. **ACTIVE-pending-operator-chat (R1/R3):** token provisioned in vault `secrets/money/telegram_bot_token` and materialized to `/etc/kai/akush_bot.env` (0600); registry `enabled=True` (R1 commit 6f1e218); poller + notify live (`runtime_ready()` True — R3 verified). Awaiting the operator's first message to @akush233bot to seed the chat allowlist.

**Activation checklist (operator, pending token):**
1. Provision bot token into vault `secrets/money/telegram_bot_token` (CT107).
2. Flip `enabled=True` for `akush233-bot` in `core/telegram/registry.py`.
3. Restart `akush-telegram.service`; reload orchestrator so notify re-gates.
4. Allowlist the operator chat in notify prefs; verify allowlist reject on unknown chat.
5. Confirm `/api/money/overview` bot block shows the bot idling→active.

## 9. PWA

Served by akush-core itself (`public/`, same origin, no third-party origins). Verified: CSP whitelist-only (`default-src 'self'`, `script-src 'self'`, `style-src 'self'`, `connect-src 'self'`, no `unsafe-*` outside self), applied to every static response (`server.js:118–124,148`); **API responses are `cache-control: no-store`** (`server.js:53–58`); static assets 300s except html/sw/manifest no-store; cookies HttpOnly + SameSite=Lax, HMAC-signed, JS-unreadable (`user_session.js:4,119–120`); CSRF double-submit cookie for the PWA session. `pwa_auth.test.js`: 20/20.

**G5 (2026-10-01): all 18 panels complete, zero stubs.** Vanilla JS only, design-system tokens exactly (`--violet` reserved for the SIMULATION markers), status always color + label, mobile bottom-nav + generated More sheet unchanged (no new nav items needed — Documents was already in the sidebar):
- **Documents panel — real:** multipart upload (kind select + magic-byte-sniffed server side), list with OCR/receipt/linked-tx statuses, detail view with OCR fields (merchant/total/reference/date + confidence %), sha256 (overflow-wrapped), confirm flow (`prepareConfirm('receipt.confirm')` → `POST /receipts/:id/confirm` with `x-akush-confirm`), attach-to-transaction (confirmed receipts only; server warns on ±5% mismatch), confirm-gated delete. Duplicates replay by sha256.
- **Planning panel — Month-End Close card:** month picker, close-preview checklist rendered as ok/warn/bad badges with counts and blocking rows highlighted, Close (confirm flow, purpose `month.close` bound to profile id + `{ym, force, reason}` payload hash) → success shows LOCKED state; Force-close path requires an audited reason ≥3 chars; months history table; per-month Reopen with mandatory reason.
- **Debt panel — simulators:** per-debt "Simulate payoff" opens a `<dialog>` modal (extra_monthly / new_payment / frequency / lump_sum → payoff date, payments saved, interest saved, baseline vs simulated); "Cash-flow what-if" card (days 7/14/30/60/90 + salary delay / income change % / extra expense → baseline vs simulated series charts + lowest-balance delta). Both are prominently labelled `kind: simulation` (violet badge + banner, `SIMULATION ≠ EXECUTION`), save button disabled until a run exists, persistence only via explicit Save; saved scenarios listed with confirm-gated delete.
- **Reports panel — server-backed:** financial months from the month-close snapshots, statement-import history from `/api/v1/imports`, saved simulations from `/api/v1/simulations`; recent-ledger rollup kept as a clearly labelled supplementary view; CSV/JSON export buttons generate client-side from fetched data.
- New `test/pwa_ui.test.js` (12 tests) statically guards the wiring: endpoints, confirm-token flow, SIMULATION markers, disabled save buttons, label-backed inputs, no frameworks/secrets, SW never caches `/api/`.

## 10. Security architecture

§71 hardening verified by re-run: HTTPS on :8095 (self-signed, LAN-pinned), auth matrix enforced (user tier: operator→`user`, viewer→read-only `viewer`, unknown roles fail closed; service principals via vault-issued per-service tokens; timing-safe compare), confirmation-token HMAC for destructive actions (`confirmation_tokens` table, `confirm.js`), idempotency keys with replay-payload-mismatch detection (`transactions.js:45–56`), rate limiting, body limit 1 MiB, redacted pipeline errors, security events + audit log (append-only triggers), §72 SMS-as-data (instruction-override SMS treated as data, never executed). Secrets: vault-only (CT107), never printed; env file `akush_bot.env` 0600 pattern.

## 11. Authentication

Three principals: (1) human PWA — local argon2id login or CT111-issued session, introspected via CT111 `/auth/status` with 30s cache, fail-closed on CT111 unreachable; (2) viewer — read-only, denied by every write gate; (3) service — vault `secrets/money/service_tokens` JSON map (`bridge`, `bot`, …), each request matched timing-safe. Destructive endpoints additionally require a confirmation token (`POST /api/v1/confirmations/prepare` → one-time HMAC token). Tests: `auth.test.js` 9/9, `pwa_auth.test.js` 20/20, `security_phase8.test.js` 15/15.

## 12. Duo

**CONFIGURED (R1/R3):** `/etc/kai/duo.env` provisioned (DUO_IKEY/SKEY/API_HOST/USERNAME) and loaded by `ai-orchestrator-api` (drop-in EnvironmentFile); `is_configured()` True in the live service env (R3 re-verified). Pending: end-to-end Duo push not yet exercised from a real login.

## 13. Tailscale (funnel flag)

Funnel was **REMOVED (R2, 2026-10-01)** on the Proxmox B host (`tailscale funnel reset`). Public internet exposure of the CC API is gone; tailnet-only access remains via node socat `cc-tailscale-proxy.service` (:8443 → CT111 :8000). The PWA (:8095) was never funneled — money surface stays LAN-only.

## 14. Command Center

CC money panel live and green: `GET /api/money/overview` returned 200 with real aggregates (health ok, accounts 4, sms 10/24h, dead_letters 0 core-side, obligations 7d, backup ok, bridge block present). Auth gate `_req_op` (bridge token / CC session / trusted-proxy headers) verified; `tests/test_money_cc_panel.py` 18/18 including `overview_requires_auth` and OpenAPI route presence.

## 15. Backup / recovery

- `akush-pg-backup.timer` **active**, daily 03:00 UTC, Persistent=true.
- App-level backup: `pg_dump kai_money → gzip → openssl AES-256-CBC PBKDF2 (600k iters)`, key fetched from vault at runtime (never printed/disked); new archive decrypt-tested + `gzip -t` on creation; all retained archives re-verified each run; retention 7. Live run this session: `akush-kai_money-20261001T084131Z.sql.gz.enc` (46,512 B, `retention_failures:0`).
- **Restore drill (§60/§74):** first run this session exposed a real defect — the drill compared the restored snapshot against **live** counts (moving target) and false-failed (dst_sms 8 vs src 12 after SMS kept arriving). Diagnosed and fixed minimally: backup now writes a `.meta.json` with dump-time counts; drill prefers meta counts. Re-run: **PASS** (82/82 tables, 12/12 SMS).
- vzdump daily 03:30 job includes **CT108** in vmid list (plus CT111); zstd, stop mode, kai-c storage.

## 16. Testing

**CT108 — akush-core, Node `node --test --test-concurrency=1`: total 329 tests, 329 pass, 0 fail, 0 skipped** (full-suite run, 127.4s, this session). The pre-G5 suite (317) plus 12 new `pwa_ui.test.js` wiring tests. New-feature suites present since G1–G4: documents 19, imports 16, month_close 13, simulations 10.

**CT111 — money/pytest (mandated set), `.venv/bin/python -m pytest`: total 141 tests, 141 pass, 0 fail** (29.8s, this session). Per file: money_sms_bridge 20, money_notify 28, money_telegram 21, money_cc_panel 18, phase8_security 6, heartbeat_sections 13, sms_manager 12, sms_webhook 5, sms_security 7, sms_e164 11. (Stale test updated this session: `test_money_cc_panel.py::test_panel_wiring_deep_link_present` still asserted the pre-R4 direct origin `https://192.168.1.118:8095/`; updated to the R4 unified gateway deep link `http://proxmox-b.tail82a9ca.ts.net:8770/` — test-only change, implementation was already updated in R4.)

**Grand total: 470 tests, 470 pass, 0 fail.**

## 17. Security testing

§71 suites re-run in this session: CT108 `security_phase8.test.js` 15/15 (HTTPS, auth matrix, viewer gate, confirmation tokens, rate limit, error redaction, service-token auth); CT111 `test_phase8_security.py` 6/6; supporting: `sms_security` 7/7 (OTP never forwarded, malicious SMS inert), `sms_webhook` 5/5 (single receiver, auth), `pwa_auth` 20/20, `auth.test.js` 9/9, OTP fixtures (§72 case 6) and malicious-SMS fixture (§72 case 7) green. Secret greps over both codebases: no hardcoded secrets/tokens/passwords/OTP (vault-only patterns confirmed in bridge/backup/telegram token code).

## 18. Known limitations

1. **kai-sms-worker :8770 is plain HTTP on 0.0.0.0** — TLS on the webhook receiver is operator-gated (needs webhook cert/key + sender trust store).
2. **Vault :8120 HTTP listener — RESOLVED (R2):** plain listener disabled; TLS :8443-only with all consumers verified.
3. ~~**Bridge dead-letter holds 12 historical entries**~~ **Resolved 2026-10-01 (TASK R3):** purged (backed up); state reload required restart of both subscriber processes; live E2E re-verified.
4. **Duo unconfigured** (step-up identity exists in design only) — blocked.
5. **Telegram bot token unprovisioned** — bot fail-safe disabled (by design) — blocked.
6. **akush_app owns the whole `kai_money` database** (broad within the DB; no superuser flags; infra tables owned by `postgres` and write-restricted) — acceptable, but ownership scoping could be tightened.
7. ~~**Dead code:** 64 + 10 `.bak`/`.backup` files~~ **Reduced 2026-10-01 (TASK R3):** 66 (CT111 core/) + 9 (CT108 akush-core src/ + public/) moved to dedicated backup dirs, preserving paths; `.orig` files untouched; suites green post-move.
8. **Tailscale funnel proxies CC :8000 with `https+insecure` upstream** — self-signed upstream trust is implicit; flag rotation is operator-owned.
9. CT111 carries a dirty worktree (uncommitted non-money changes predating this session) — not touched.

## 19. Status table

| Component | Status | Evidence (this session) |
|---|---|---|
| akush-core API (CT108 :8095, HTTPS) | VERIFIED | 329/329 tests; `/health` 200 `{db ok, latency 15ms}`; `/internal/health/detailed` 200 (db ok, pipeline_failures 0); live service active |
| kai_money schema, migrations 001–016 | VERIFIED | 16/16 applied; 48 FKs, 281 indexes; 8 append-only triggers live |
| akush_app least-privilege | VERIFIED | super/createdb/createrole all false; scoped grants; infra tables read/insert-restricted |
| Single source of truth (no second ledger) | VERIFIED | CT111 money modules: zero direct DB connections; all writes via `:8095` API |
| Auth matrix + fail-closed | VERIFIED | auth.test.js 9, pwa_auth 20, security_phase8 15; CT111 unreachable → fail closed |
| Confirmation tokens (destructive ops) | VERIFIED | `confirm.js` + confirmation_tokens table; §71 tests |
| Idempotency keys | VERIFIED | transactions.js:45–56 replay-mismatch tests |
| SMS pipeline (single :8770 receiver, bridge, dedup) | VERIFIED | sms tests green; live counters forwarded=21 duplicates=1 failed=9 (R3); only one receiver in codebase |
| OTP containment (never leaves CT111) | VERIFIED | redaction + transport refusal (bridge.py:287); sms_security 7/7 |
| §72 acceptance fixtures (8 mandated cases) | VERIFIED | sms_pipeline.test.js 12/12 incl. all 8 §72 cases + 2 extras |
| Account discovery lifecycle (no auto-trust) | VERIFIED | fixtures 1–3; live accounts all REJECTED after R3 test-artifact purge (0 official) |
| Balance intelligence (CHANGE ≠ income) | VERIFIED | fixtures 4–5; kind_alignment 21/21 |
| Statement import pipeline (§38 G1) | VERIFIED | imports.test.js 16/16; PWA lists batches; confirm-gated commit |
| Documents/receipts/OCR (§37 G2) | VERIFIED | documents.test.js 19/19; PWA upload/confirm/attach/delete wired with confirm tokens |
| Month-end close (§40 G3) | VERIFIED | month_close.test.js 13/13; PWA close-preview checklist + confirm-gated close + reopen |
| Debt-payoff + cashflow simulators (§26/§31 G4) | VERIFIED | simulations.test.js 10/10; PWA dialogs labelled kind:simulation; nothing persisted without explicit Save |
| PWA panels complete (18/18, G5) | VERIFIED | pwa_auth 20/20 + pwa_ui 12/12; no NOT-VERIFIED stubs remain; live CSP + gateway 200 |
| Telegram bot | ACTIVE-pending-operator-chat (R1/R3) | bot live: runtime_ready() True, registry enabled=True, poller active; allowlist awaits operator /start |
| Telegram allowlist + fail-safe | VERIFIED | handlers.py allowlist reject; poller idle-logging disabled state |
| Command Center panel | VERIFIED | money_cc_panel 18/18; live `/api/money/overview` 200 with aggregates |
| PWA (CSP, no-store, cookie security) | VERIFIED | pwa_auth 20/20; CSP/no-store/HttpOnly verified in source; live :8770 gateway serves PWA 200 + CSP |
| Observability (health, dead-letter visibility) | VERIFIED | /health + /internal/health/detailed live (service-gated); CC bridge block visible |
| Bridge dead-letter hygiene | RESOLVED (R3) | 12 historical entries purged (backed up to /root/backups-r3); kai-scheduler+kai-sms-worker restarted to reload clean state; live E2E re-verify forwarded OK; dead_letter_count 0 stable |
| Encrypted PG backup | VERIFIED | live run 2026-10-01T084131Z, AES-256/PBKDF2, integrity re-verified, retention ok |
| Restore drill | VERIFIED | PASS after meta-count fix (82/82 tables, 12/12 SMS); defect found + fixed this session |
| vzdump CT108 inclusion | VERIFIED | daily jobs.cfg vmid list contains 108 |
| Secrets handling (vault-only) | VERIFIED | greps clean both codebases; vault-fetch patterns in code |
| Exposed ports audit | VERIFIED (R2) | funnel OFF; vault 8120 HTTP closed (8443 TLS-only); 8770 HTTP 0.0.0.0 + NEW 8771 TLS (socat); 8000 0.0.0.0 TLS; 8099 0.0.0.0; 8095 LAN-bound |
| Duplicate-implementation sweep | VERIFIED | no second SMS receiver/ledger/event bus found (`core/sms/service.py` IS the receiver) |
| Dead code | REDUCED (R3) | 66 CT111 core/ + 9 CT108 akush-core .bak/.backup files moved to dedicated backup dirs (nothing deleted); both suites green post-move |
| Duo | CONFIGURED (R1, R3 re-verified) | /etc/kai/duo.env loaded by api service; is_configured() True in live env; live push drill pending |
| Tailscale funnel | RESOLVED (R2) | funnel reset; tailnet-only :8443 socat verified 200 from pve-A; no public listener remains |
| SMS-worker TLS | PARTIAL (R2) | :8771 TLS sidecar live (socat); :8770 plain receiver still bound 0.0.0.0 — phone sender URL switch to :8771 pending operator |
| Overall | **READY with operator-gated items** | 470/470 tests (G5 re-run: CT108 329 + CT111 141); live services green; remaining items operator-gated (SMS TLS phone switch, Telegram allowlist, Duo live-push drill) |

**Honesty note:** not 100% — Telegram and Duo are blocked on operator provisioning; R2 (2026-10-01) resolved vault :8120 exposure, funnel flag, and legacy grants; SMS-worker TLS live on :8771 but phone URL switch still pending operator; bridge dead-letter backlog has no replay path; CC money-panel report views for observatory PVE auth and cc/* models remain pending external connections (see §22).

## 20. R3 addendum (2026-10-01 — cleanup + final verification)

**Bridge dead-letters — RESOLVED (R3):** the 12 historical development-phase dead-letters (post_failed transport errors, fingerprint_failed, queue_overflow, bridge_token_unavailable) purged from `money_sms_bridge_state.json` after backup to `/root/backups-r3/`; counters and last_processed preserved. Operational finding: BOTH bridge subscriber processes (kai-scheduler AND kai-sms-worker) hold the state in memory — a file-only purge gets re-overwritten on the next persist; both services were restarted after the purge to reload clean state. Live E2E re-verified post-restart: webhook SMS (:8770) → forwarded (counter 19→21) → akush-core row → account-candidate lifecycle exercised. `dead_letter_count: 0` stable (60 s observation + CC overview).

**DEMO/test data — RESOLVED (R3), via akush-core API only** (real CT111 operator JWT, introspected via /auth/status; no raw SQL where a flow exists):
- 4 DEMO income sources deactivated (`DELETE /api/v1/income-sources/:id` — soft-delete is_active=FALSE). Their 81 expected occurrences are append-only (no occurrence-delete flow) and are excluded from active calculation: forecast expected_in = 0.00.
- 2 DEMO commitments deactivated (`DELETE /api/v1/commitments/:id`); their 26 occurrences remain (due/partial) but the parent is inactive → obligations_7d = 0.
- 3 DEMO paydays deactivated (`DELETE /api/v1/paydays/:id`); 4 proposed payday_allocations remain attached but the engine reads active paydays only.
- 6 candidate/test accounts REJECTED via `POST /api/v1/accounts/:id/reject` (4 DEMO/manual candidates + 2 R3-verification SMS candidates) → 0 official accounts (honest empty state).
- All 43 pending `financial_event_inbox` items closed via `POST /api/v1/financial-inbox/:id/act` (9 actionable → rejected, 34 review/anomaly → ignored) → inbox pending = 0.
- Backup before cleanup: full-table CSV dumps + tarball in `/root/backups-r3/demo-db/` (12 tables).
- Remaining append-only rows (excluded from active calculations, documented): transactions id 4 (memo `commitment:2 DEMO bill - delete me`), ids 14/15 (`demo kiosk`) — transactions have no delete flow by design (append-only ledger); financial_events (46) and sms_messages (19) are the ingest/audit log; 2 recurring_patterns (mtn/shop) derived from user-entered transactions. Forecast honest: 0.00 in / 0.00 out / [] accounts; safe-to-spend -75.00 = 0 cash − 75.00 configured min_buffer.

**.bak/.backup hygiene — RESOLVED (R3):** CT111 `core/`: 66 files (64 .bak-* + 2 .backup) → `/opt/ai-orchestrator/backups/bak-files-r3/`; CT108 `services/akush-core/`: 9 files (8 in src/, 1 in public/; test/ had none) → `/opt/kai-money/state/bak-files-r3/` preserving relative paths. Nothing deleted; `.orig` files (not .bak/.backup) untouched. Both full suites re-run green after the move.

**Test harness fixes (R3):** (1) CT108 `test/helpers.js` — the vault reveal used `node:http` against the https `:8443` endpoint (introduced by R2 commit 8254711), so TEST_PGPASSWORD came back empty and the whole suite failed SASL; fixed to `node:https` (NODE_EXTRA_CA_CERTS pinned CA). (2) CT111 — 3 stale tests (`test_money_notify.py` bot-disabled, `test_money_telegram.py` runtime_ready/token-absent) updated for post-R1 reality: the operator has flipped the bot live (registry enabled=True, token env materialized), so the disabled state is now asserted explicitly via monkeypatch and the real transport token path is stubbed. No implementation changes.

**Final suite (R3):** CT108 `node --test --test-concurrency=1`: **248/248 pass, 0 fail**. CT111 pytest (10 money files): **150/150 pass, 0 fail**. Grand total **398/398**.

**Live verification (R3):** akush-core `/health` ok + `/internal/health/detailed` ok (db ok, pipeline_failures 0); services: akush-core active, ai-orchestrator-api active, kai-sms-worker active, akush-telegram active, kai-scheduler active, akush-pg-backup.timer active+enabled; CC `/api/money/overview`: bridge `dead_letter_count: 0`, inbox pending 0, obligations_7d 0, anomalies open 0, accounts `{REJECTED: 6}`, backup ok. PG post-cleanup: accounts 6 (all REJECTED), transactions 12, financial_events 46, financial_event_inbox 43 (0 pending), sms_messages 19.

**TASK R4 (2026-10-01) — unified gateway entry:** pve-B nginx on :8770 (all interfaces) now routes `= /webhook/sms` → CT111 kai-sms-worker and `/` → akush-core :8095 (PWA). Phone base URL unchanged (`http://proxmox-b.tail82a9ca.ts.net:8770/webhook/sms` — verified from live traffic); `sms-worker-tailscale-proxy.service` (socat) stopped+disabled for rollback-keep; SMS TLS :8771 sidecar now optional/superseded; CC :8443 untouched. Dashboard reachable from Proxmox B LAN via `http://192.168.1.110:8770/` (verified from CT111 + VM104). Deep-links updated to the unified URL: CC `command_center.html` AKUSH_PWA_URL + `mobile_launcher_routes.py` (3 refs). Tradeoff (resolved in R5): plain-http-inside-mesh; akush-core initially sent `Secure` cookies which browsers refuse to store over plain http — interactive browser login via the gateway was broken; fixed by the adaptive `Secure` rule (R5 below), gateway browser login now works. Verification: webhook test SMS 200 → bridge → kai_money row; PWA 200+CSP via gateway; cookie login + /api/v1/session roundtrip via gateway; LAN curl 200 ×2; pytest test_sms_webhook+test_money_sms_bridge 25/25; OTP body → `[REDACTED]`, 0 digit hits in CT111 inbox and akush DB. pve-B nginx config + unit backups: `/root/akush-gateway-backups-<ts>/`.

## 21. R5 addendum (2026-10-01 — browser login via gateway)

**TASK R5 (2026-10-01) — adaptive `Secure` cookie for gateway logins:** `src/user_session.js` gained `secureCookieFor()`: `Secure` = (akush-core connection is https) AND NOT (X-Forwarded-Proto == 'http' from the TRUSTED gateway). Trust boundary: X-Forwarded-Proto is honored only when the immediate TCP peer of akush-core is the pve-B nginx gateway (`192.168.1.110`, comma-list override `AKUSH_TRUSTED_GATEWAY_IPS`); any other peer keeps the old behavior, so a spoofed header cannot strip `Secure`. Gateway now sends `X-Forwarded-Proto $scheme`. Login AND logout clearing both use the rule. Consequence: **browser login via `http://…:8770` (tailnet + LAN) now stores cookies and the dashboard is fully usable at the gateway URL**; direct `https://192.168.1.118:8095` still sets `Secure`. Transport tradeoff unchanged: gateway path is plain http inside the mesh-encrypted tailnet/LAN, no public exposure. `public/app.js` checked: API base is same-origin relative (no hardcoded origin). Tests: 11 new in `test/cookie_secure.test.js` (7 unit + 4 integration over real TLS); full suite **259/259 pass** (was 248). Live: gateway login → Set-Cookie WITHOUT Secure → /api/v1/session 200 via cookie; direct https login → Set-Cookie WITH Secure.

## 22. G5 addendum (2026-10-01 — final gap closure G1–G5, PWA wiring + full verification)

**G5 — PWA wiring of the G1–G4 capabilities (all VERIFIED):** the Akush Money PWA (`services/akush-core/public/`, vanilla JS, Kai Design System tokens) gained: a real Documents panel (upload/list/detail/confirm/attach/delete with the §71 confirm-token flow, OCR fields + sha256 displayed); a Month-End Close card in Planning (close-preview checklist ok/warn/bad with counts and blocking highlighted, confirm-gated close → LOCKED state, months history, operator reopen with mandatory reason); payoff + cash-flow simulators in Debt (dialog modal, results labelled `kind: simulation` in violet with the `SIMULATION ≠ EXECUTION` banner, save disabled until a run exists, saved scenarios confirm-gated delete); a server-backed Reports panel (financial months from close snapshots, import history, saved simulations, CSV/JSON export). No new nav items were needed (Documents already in the sidebar); mobile bottom-nav and the generated More sheet are unchanged; no frameworks; the SW still never caches `/api/` responses; no secrets in client code.

**Full verification (fresh, this session):**
- CT108 `node --test --test-concurrency=1`: **329/329 pass, 0 fail** (was 317; +12 new `pwa_ui.test.js`), 127.4s.
- CT111 pytest (mandated money set, 10 files): **141/141 pass, 0 fail**, 29.8s. One stale test updated (CC deep-link → R4 gateway URL; test-only).
- **Grand total: 470/470.**
- Live: `/health` 200 (db ok, 15 ms); `/internal/health/detailed` 200 service-gated (db ok, scheduler 7 checks 0 errored, sms 30/24h, pipeline_failures 0); PWA 200 + full CSP direct (:8095) AND via the pve-B gateway (:8770); CC `/api/money/overview` 200 (health ok, bridge dead_letter_count 0); webhook route reachable via gateway (422 on empty body = receiver reached, validation intact); `omniroute-tunnel.service` active (claude-code/pve-A), `omniroute-lan-proxy.service` active (pve-B); `akush-pg-backup.timer` active+enabled, latest archive `akush-kai_money-20261001T084131Z.sql.gz.enc` present with `.meta.json`.
- Services `systemctl is-active`: akush-core ✓, ai-orchestrator-api ✓, kai-sms-worker ✓, akush-telegram ✓, kai-scheduler ✓, omniroute-lan-proxy ✓ (pve-B node).

**Status flips in this change:** statement import → VERIFIED (was pending PWA wiring); receipts/OCR + documents → VERIFIED (was NOT VERIFIED stub); month close → VERIFIED (Planning card shipped); simulators → VERIFIED (Debt panel shipped); PWA panels 18/18 complete.

**Remaining open items (unchanged from R5, all operator-gated or external):**
1. Observatory PVE auth — pending Proxmox PVE credential/connection for the observatory view.
2. Telegram — awaiting the operator's first `/start` to @akush233bot to seed the chat allowlist.
3. cc/* models — pending OmniRoute claude connection (models route not yet serving through the CC model list).
4. SMS-worker :8770 plain HTTP phone switch to :8771 TLS (operator), Duo live-push drill (operator).
