---
name: media-factory
description: Operate the KAI Media Revenue Factory — the media pipeline, revenue/rights ledgers, pattern library and capability status under core/media_factory and /api/media/*. Use when working on media cycles, trends, content, publishing, rights, revenue, experiments, strategy, workers, or the Command Center Media panel.
metadata:
  origin: kai
---

# KAI Media Revenue Factory

Control plane for turning trend signals into content, publishing it and
tracking revenue. Lives on CT111 (`/opt/ai-orchestrator/core/media_factory`),
served by the orchestrator API (HTTPS :8000) under `/api/media/*`.

## Honest capability plane

The factory reports status, never guesses. Vocabulary
(`core/media_factory/config.py`): `VERIFIED`, `PARTIALLY_VERIFIED`,
`UNVERIFIED`, `MISSING`, `BLOCKED`, `DEGRADED`, `FAILED`.

Verified today: database (`kai_media`, 39 tables), trend discovery/validation,
opportunity engine, model fabric (`qwen3-coder:kai` via Ollama),
rights/provenance, revenue ledger, strategy versions, pattern library,
IP portfolio.

Bluntly BLOCKED (with reasons in `/api/media/status`): asset generation
(no image/video models, no ffmpeg), voice/audio, editing, captions, live
platform publishing (no OAuth tokens), platform analytics ingestion.
Publishing only runs `dry_run` and is gated by `MEDIA_PUBLISH_ENABLED=false`.
Never claim these work.

## API

Reads are open; writes go through `media_operator` (enforced only when
`MEDIA_AUTH_REQUIRED=true`).

- `GET /api/media/status` — capability summary + per-capability reasons.
- `GET /api/media/dashboard` — counts, latest cycle, revenue/cost/profitability.
- `GET /api/media/trends|content|production|publishing|analytics|revenue|costs|profitability|experiments|strategy|patterns|rights|workers|models|policies|audit|ip|factories`
- `POST /api/media/trends` `{geo}` — run discovery.
- `POST /api/media/content` `{opportunity_id, kind}`
- `POST /api/media/publishing` `{content_id, mode, dispatch}`
- `POST /api/media/revenue|costs`, `POST /api/media/experiments`
- `POST /api/media/strategy`, `POST /api/media/strategy/{version}/activate`

Queries: `limit`/`offset` (1–200).

## Cycle and scheduler

`core.media_factory.worker.run_media_cycle(force=False)` runs one cycle. It is
self-throttled to `MEDIA_CYCLE_MIN_INTERVAL` (default 900s, persisted in
`MEDIA_DATA_DIR/worker_state.json`) and never raises. `core/scheduler.py` calls
it every 60s inside its own try/except; the 60s cadence only checks the
throttle. `engine.run_cycle()` records every stage in `media_events` and the
append-only audit ledger; a failed stage does not stop the cycle.

`GET /api/media/workers` reports `scheduler_registered`.

## Flags (env, runtime-readable)

`MEDIA_ENABLED`, `MEDIA_DRY_RUN` (default true), `MEDIA_PUBLISH_ENABLED`
(default false), `MEDIA_CYCLE_MIN_INTERVAL`, `MEDIA_AUTH_REQUIRED`,
`MEDIA_LLM_BASE_URL`/`MEDIA_LLM_MODEL`, `MEDIA_TREND_GEO`.
DB: `MEDIA_DB_HOST/PORT/NAME/USER/PASSWORD` (vault fallback, never hardcode).

## Notifications

`core/media_factory/notify.py` POSTs events to the orchestrator `/notify`
endpoint using `MEDIA_NOTIFY_TOKEN`/`KAI_NOTIFY_TOKEN` (or
`KAI_NOTIFY_TOKENS_FILE`). Emits cycle completed/blocked, publish blocked and
new pattern learned; experiment creation is emitted from `routes.py`. Failures
log only — notifications never break a cycle.

## Command Center

`core/kai/command_center.html` → sidebar `#media` → `loadMedia()` with
sub-tabs Overview, Trends, Content, Production, Publishing, Analytics, Revenue,
Experiments, Strategy, Rights, Workers. Registered in the Service Directory as
`media-factory` and `media-*` pages (category `media`).

## Operate safely

- Inspect `/api/media/status` before trusting any number.
- Do not fabricate revenue/analytics rows to make reports look good.
- Live publishing requires OAuth tokens AND `MEDIA_PUBLISH_ENABLED=true`; the
  rights gate must pass first.
