# KAIBET_DISCOVERY_REPORT.md

**§50 audit deliverable** for the directive *KAI BET 2026 — KAI-Only Autonomous Sports
Intelligence, Prediction, Market & Betting Operations Fabric* (`directives/20260917T090921Z-pasted-directive.md`).

Audited 2026-09-17 against the runner (CT111 `/opt/ai-orchestrator/core/kai_betting`).

> Per §50: audit before modifying; preserve useful functionality; integrate rather than
> duplicate; replace only when demonstrably better. **No code was changed to produce this report.**

---

## 1. Executive summary

A substantial KAI Bet already exists (~9,400 LOC, 27 Python files, 26-table SQLite DB,
33 HTTP routes, a Telegram bot, and a built frontend). It covers data ingestion, a
statistical prediction engine, odds grouping, subscriptions/payments, and a three-tier
"AI" router.

**Two blocking findings against the directive:**

1. **§0 violation — external intelligence.** `ai/client.py` defaults to
   `BASE_URL=https://api.gpu.ai/v1`; `ai/router.py` drives Qwen/DeepSeek/K3 through it.
   The directive forbids external LLMs. This must be repointed to KAI's own fabric
   (local Ollama / model fabric) and the escalation logic preserved.
2. **Not running.** The betting worker (`workers.run_cycle`) is **not scheduled** —
   no systemd unit, and `core/scheduler.py` never calls it. The Telegram bot isn't
   running either. So despite existing code, KAI Bet is effectively **dormant**.

Most directive engines are **missing** (value, paper betting, walk-forward, risk,
strategy lab, drift, Second Brain, audit record, live betting). No Command Center
surface exists for betting.

---

## 2. Component map

| Component | Where | State |
|---|---|---|
| Module | `core/kai_betting/` (27 `.py`, ~9,400 LOC) | present |
| HTTP API | `api.py` (33 routes), mounted in `core/api.py:176` | mounted, VERIFIED reachable |
| DB | SQLite `memory/kai_betting.db` (26 tables) | present, populated |
| Prediction engine | `prediction_engine.py` (32 KB) | present |
| Odds engine | `odds_engine.py` (14 KB) | present |
| Data ingestion | `data_ingestion.py` (56 KB) + `data_sources/` | present |
| AI router | `ai/router.py`, `ai/client.py`, `ai/prompts.py`, `ai/budget.py`, `ai/cache.py` | present, **external** |
| Workers | `workers.py` (`KaiBettingWorkers.run_cycle`) | present, **not scheduled** |
| Telegram | `telegram_bot.py` (~23 KB, commands listed) | present, **not running** |
| Scope/leagues | `scope.py`, `scope_migration.py` | present |
| Security/sessions | `security.py`, `sessions.py`, `authz` routes | present |
| Subscriptions/payments | `subscriptions.py`, `payments.py` | present |
| Frontend | `frontend/` (+ `dist/`) | built |
| Command Center | — | **none** |
| Systemd units | — | **none** |

---

## 3. Database (SQLite `memory/kai_betting.db`)

26 tables. Live-ish content:

| Table | Rows | Table | Rows |
|---|---|---|---|
| events | 453 | teams | 787 |
| leagues | 98 | sports | 10 |
| predictions | 1590 | predictions_old | 18 |
| odds_groups | 32 | odds_group_selections | 157 |
| performance_metrics | 590 | betting_config | 21 |
| prediction_models | 1 | users | 1 |
| subscription_plans | 3 | ai_prediction_records / ai_usage | 0 |
| prediction_results | 0 | audit_logs | 0 |
| payments / subscriptions | 0 | notifications | 0 |
| event_statistics | 0 | telegram_accounts | 0 |

**Weaknesses:** `predictions_old` (duplicate/legacy table); `ai_usage`/`audit_logs` empty
(no cost or audit trail produced); single-file SQLite (no concurrent writers, weak for a
fabric-scale workload).

---

## 4. APIs (`api.py`, 33 routes)

- **Auth**: `/auth/register|login|logout`
- **Catalog**: `/sports`, `/sports/{key}/leagues`, `/sports/{key}/teams`, `/events`
- **Predictions**: `POST /predictions/generate`, `POST /predictions/batch`, `GET /predictions`,
  `GET /predictions/{id}`, `POST /predictions/{id}/settle`
- **Odds groups**: `POST /odds-groups/generate`, `GET /odds-groups`, `GET /odds-groups/{id}`
- **Commerce**: `/plans`, `POST /subscriptions/purchase`, `/subscriptions/{user}`, `POST /payments/callback`, `/payments/{user}`
- **Ops**: `/performance`, `/dashboard`, `/admin/config` (GET/PUT), `/admin/users` (+PUT), `/admin/audit`, `/preferences/{user}` (GET/PUT)
- **Health**: `/health`

Mounted into the orchestrator API, so it is reachable at `https://…:8000/<route>`.

---

## 5. Models / intelligence

- **`ai/router.py`** — three-tier escalation: **Qwen screens → DeepSeek adversarially
  challenges → K3 adjudicates** high-value/high-disagreement candidates. Returns a
  *recommendation*; the deterministic engine is meant to remain final authority.
- **`ai/prompts.py`** — `PROMPT_VERSION="BETTING_AI_PROMPT_V1.0"`, model catalog
  `gpuai/qwen3.7-plus`, `gpuai/deepseek-v4-pro`, `gpuai/kimi-k3`.
- **`ai/client.py`** — `BASE_URL` default **`https://api.gpu.ai/v1`** → **external LLM** (violates §0).
- `ai/budget.py` (budget control), `ai/cache.py` (inference cache) — reusable.
- **`prediction_engine.py`** — statistical model (implied prob → model → quality → edge).
- `prediction_models` table has 1 row (model registry exists but thin).

---

## 6. Workers & scheduled tasks

- `workers.py → KaiBettingWorkers.run_cycle()`: refreshes events, syncs odds/results,
  interval-gated. 300s cycle intended.
- `run_worker.py`: standalone entry point ("run via systemd").
- **Reality:** no systemd unit; `core/scheduler.py` does not import or call it. **Nothing runs.**

---

## 7. Telegram

`telegram_bot.py` defines: `/start /help /day /picks /odds /results /performance /sports
/subscribe /myaccount /stats /predictions`. No running bot process found. (The orchestrator's
own `telegram_bridge` is separate.)

---

## 8. Data providers (allowed by §0 — data only)

`data_sources/`: `odds_api.py` (the-odds-api.com), `odds_api_io.py` (api.odds-api.io),
`sportsgameodds.py` (api.sportsgameodds.com). These are **evidence/data** sources — permitted.

---

## 9. Verification status (§51)

| Area | Status |
|---|---|
| DB schema + data | **PARTIALLY VERIFIED** (tables exist, 1590 predictions; no results) |
| HTTP API | **PARTIALLY VERIFIED** (routes mounted; not load-tested) |
| Data ingestion | **UNVERIFIED** (providers configured; no fresh run observed) |
| Prediction engine | **PARTIALLY VERIFIED** (1590 predictions; 0 results → unproven) |
| AI router/intelligence | **FAILED §0** (external api.gpu.ai) |
| Workers/scheduling | **FAILED** (not scheduled) |
| Telegram | **BLOCKED** (bot not running) |
| Frontend | **UNVERIFIED** (dist present; not served/wired) |
| Command Center | **MISSING** |
| Value engine | **MISSING** |
| Paper betting | **MISSING** |
| Walk-forward / calibration job | **PARTIAL** (calibration refs; no job) |
| Risk engine | **MISSING** |
| No-bet gate | **PARTIAL** (references; not enforced as a gate) |
| Champion/challenger | **PARTIAL** (refs; not operational) |
| SportyBet verification | **PARTIAL** (refs; not operational) |
| Strategy lab / drift | **MISSING** |
| Second Brain / audit record | **MISSING** |
| Real-money execution | **MISSING** (correctly not enabled) |

---

## 10. Duplicates / overlaps

- `predictions` vs `predictions_old` tables.
- Two odds providers (`the-odds-api` and `odds-api.io`) + `sportsgameodds` — needs a single
  normalized odds layer (§ odds normalization).
- AI escalation overlaps the "KAI Brain adversarial review" (§11) — must be merged, not doubled.

---

## 11. Security observations (to verify in PHASE 1)

- `/admin/*`, `/payments/callback`, `/subscriptions/purchase` need explicit authz review.
- SQLite file holds users/subscriptions; permissions + backup posture unverified.
- Payments callback signature/verification unverified.
- AgentGuard/Vault integration (§37/§38) not present in the module.

---

## 12. Recommended phase plan (matches directive)

1. **Audit → this report** (done).
2. **PHASE 1 — KAI-only intelligence + running.** Repoint `ai/client.py` (and the escalation
   router) at KAI's local model fabric (e.g. VM104 Ollama `qwen3-coder:kai` via the existing
   tunnel), delete the external dependency, preserve the three-tier escalation semantics;
   add a systemd unit and schedule `run_cycle` in the KAI scheduler (fail-safe, like the
   money cycle). Verify predictions flow end-to-end with **local** intelligence only.
3. **PHASE 2 — Missing engines.** Value engine, paper betting (default), walk-forward
   validation, risk engine, no-bet gate enforcement, drift detection, Second Brain +
   audit record. Tests per §52.
4. **PHASE 3 — Surfaces + gates.** Command Center KAI Bet dashboard (wired like Arbitra),
   Telegram bot restored, AgentGuard/Vault, real-money gate **off by default**.

---

## 13. Extension points

- Reuse `ai/budget.py`, `ai/cache.py`, `prompts.py` (swap the model catalog to local IDs).
- Reuse `prediction_engine.py` + `odds_engine.py` as the deterministic authority.
- Keep the SQLite → consider migration to Postgres (CT108) when going multi-writer.
- Wire surfaces through the orchestrator API (`api.py`) and Command Center like Arbitra.

---

*Generated by the §50 audit. No production code was modified to produce this report.*
