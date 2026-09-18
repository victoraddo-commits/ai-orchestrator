# KAI 2.0 — Phase 2: Unified Command Center + Per-Model Pages

**Date:** 2026-09-18 · **Host:** LXC 111 (`/opt/ai-orchestrator`, canonical) ·
**Branch:** `main` · **Directive:** §30 (Command Center) · **Plan:**
`docs/superpowers/plans/2026-09-18-kai-2.0-unified-brain-teammate-factory.md` (Phase 2)

## Status

| # | Deliverable | Status |
|---|---|---|
| 1 | One canonical Command Center; React SPA retired + 301 redirect | DONE |
| 2 | Per-model dedicated pages (+ index) | DONE |
| 3 | Directive §30 panels real (Workforce, Missions, Model Fabric, Security, Infra, Communication) | DONE |
| 4 | Responsive 360 / 768 / 1280 | DONE (verified) |

Commits (runner `main`):

- `938e6eb` feat(cc): Model Fabric catalog + per-model test-call + security overview
- `ab055fd` fix(cc): operator `/auth/status` 500 (lazy caps) + network reads accept operator session
- `151d220` perf(infra): background-refresh + cache proxmox registry snapshot
- `0bf9830` feat(workforce): teammate retire endpoint
- `105070d` perf(cc): stale-while-revalidate model catalog cache + tests
- `a5376c9` feat(cc): Model Fabric per-model pages, real §30 panels, responsive
- `41d2157` docs(cc): declare FastAPI CC canonical; retire React SPA; Phase 2 coverage
- `c47639d` fix(security): never echo raw session token from operator dependencies

## 1 — Canonical Command Center

Decision: the **FastAPI Command Center** (`core/kai/command_center.html`, LXC 111,
`orchestrator.tail82a9ca.ts.net/command-center`) is canonical. The React SPA on
PVE-A CT100 is **retired**: `kai-command-center.service` stopped+disabled, nginx
301-redirects `command.tail82a9ca.ts.net` / `command.kai` / `command.deerude.com`
to the canonical CC, and the tailnet `svc:command` service proxies to that nginx.
Source preserved. Full evidence and revert steps:
`docs/COMMAND_CENTER_CANONICAL.md`.

```
command.tail82a9ca.ts.net/command-center -> 301 -> orchestrator.tail82a9ca.ts.net/command-center (200)
akaiT markers in followed page: 7   SPA bundle markers: 0
kai-command-center.service: inactive / disabled
```

## 2 — Per-model pages

`GET /api/models/catalog` aggregates provider registry, `/models/registry`,
provider dashboard, routing weights, telemetry, and live Ollama (`/api/tags`,
`/api/ps`) + llama.cpp probes. The CC registers, at runtime, for every model:
sidebar sub-item, `<section class="panel" id="panel-model-<slug>">`,
`PANEL_TITLES` entry, `loadPanel()` dispatch, and `loadModelPage()`.

Page detail: title, provider, kind, artefact, **params**, quantization, size,
**context window** + context loaded, endpoint, cost tier, routing
weight/roles/task types, health, usage (attempts, success rate, avg/EMA latency,
telemetry calls, cost), GPU/VRAM, and a live **Test call**
(`POST /api/models/{id}/test`). Verified live:

```
POST /api/models/kai_brain/test -> {"ok":true,"response":"...","latency_ms":...}
```

The `Models` panel is the Model Fabric **index**: model cards (link to pages),
resource utilization (GPU permits/VRAM/loaded), routing chains, provider
management (enable/disable/reset/delete/register) and operator overrides.

## 3 — §30 panels (all render real data; no raw-JSON dumps)

- **Workforce** — `/api/workforce/teammates|teams`, `/workers`; create teammate,
  form team, retire, worker health. Live: 6 teammates, 6 READY, 6 teams, 9 workers.
- **Missions** — `/api/missions`, `/api/missions/{id}`; active/history, task
  graph, verification checks, artifacts, checkpoints. Live: 3 missions.
- **Model Fabric** — catalog + `/api/fabric/summary` + `/providers/chains`.
- **Security** — `/api/security/overview`: AgentGuard, RBAC, policy, vault, events.
- **Infrastructure** — `/proxmox/registry`, Docker, service directory, model-fabric
  utilization, network topology, VPN; each source parallel + timeout-bounded.
- **Communication** — Telegram + approvals panels.

New/extended endpoints: `GET /api/models/catalog`, `POST /api/models/{id}/test`,
`GET /api/fabric/summary`, `GET /api/security/overview`,
`POST /api/workforce/teammates/{id}/retire`. All return **200** (verified).

## 4 — Responsive

Playwright captures at 360/768/1280 for `home, ai-workforce, missions, models,
security, infrastructure, model-kai-brain`: **21/21 panels settled, no horizontal
scroll** (`documentElement.scrollWidth == innerWidth`), tables scroll, mobile
bottom nav + auto-generated "More" sheet. Only console noise is the service
worker fetched over the self-signed QA tunnel (not present on the real tailnet).

## Fixes required along the way

- `auth_status` 500 for operator sessions (lazy `ROLE_CAPABILITIES` → `_get_role_caps`).
- `/network/topology|connectivity|changes` now accept an operator session; they
  previously 401'd the CC and **logged the operator out**.
- `/proxmox/registry` background-refreshed snapshot (was a ~100s blocking request).
- Operator dependencies no longer echo the raw session token.

## Remaining / notes

- `command-ui.tail82a9ca.ts.net` (vite dev, :3011) is now 502/dead — a second
  retired SPA surface; documented.
- `/proxmox/registry` inventory populates from a 300s background refresh; while a
  Proxmox API is unreachable the CC shows "discovery in progress" rather than
  blocking.
- 4 tests in `tests/test_network_command_center.py` require `playwright` in the
  runner venv and are skipped-by-error in this environment (pre-existing).
- `coder-01` (legacy teammate with empty capabilities, flagged in the Phase 1
  report) was retired via the new endpoint.
