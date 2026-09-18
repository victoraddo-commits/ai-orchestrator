# Command Center — Module ↔ Full Page Coverage

**Updated**: 2026-09-16

Source of truth for "every app/module has a full page inside the Command
Center". A module counts as *wired* when all four exist and match:
sidebar `nav-item[data-hash]`, `<section id="panel-<name>">`, `PANEL_TITLES`
entry, and a loader in the `loadPanel()` dispatcher map.

## Full pages (wired)

| Panel key | Title | Loader | Backend (main feeder) |
|---|---|---|---|
| `home` | Home | loadHome | /api/status, /api/summary |
| `modules` | Modules | loadModules | /kai/modules |
| `legal` | Legal | loadLegal | /kai/legal-brain |
| `ai-workforce` | Workforce | loadWorkforce | /kai/workforce* |
| `money` | Money | loadMoney | /kai/money* |
| `talent` | Talent | loadTalent | /kai/talent* |
| `android` | Android | loadAndroid | /kai/android* |
| `docs` | Documents | loadDocs | /kai/knowledge, /kai/secondbrain |
| `hubtel` | Hubtel | loadHubtel | /kai/hubtel* |
| `docker` | Docker | loadDocker | /api/docker |
| `telegram` | Telegram | loadTelegram | /kai/telegram* |
| `airdrop` | Airdrop | loadAirdrop | /kai/airdrop* |
| `infrastructure` | Infra | loadInfra | /kai/infra*, /api/infra/usage |
| `security` | Security | loadSecurity | /kai/security* |
| `settings` | Settings | loadSettings | /kai/settings, /auth/status |
| `logs` | Logs | loadLogs | /kai/logs |
| `kai-chat` | Kai Chat | loadKaiChat | /kai/chat* |
| `devices` | Devices | loadDevices | /kai/devices |
| `network` | Network | loadNetwork | /network/*, /api/vpn/status |
| `models` | Models | loadModels | /providers, /models/* |
| `vault` | Vault | loadVault | /kai/vault |
| `secondbrain` | 2nd Brain | loadSecondBrain | /kai/secondbrain |
| `knowledge` | Knowledge | loadKnowledge | /kai/knowledge |
| `approvals` | Approvals | loadApprovals | /kai/approvals |
| `notifications` | Notify | loadNotifications | /kai/notifications |
| `backups` | Backups | loadBackups | /kai/backups |
| `sessions` | Sessions | loadSessions | /kai/sessions |
| `emergency` | Emergency | loadEmergency | /api/emergency/* |
| `roadmap` | Roadmap | loadRoadmap | /kai/roadmap |
| `learning` | Learning | loadLearning | /kai/learning |
| `enterprise` | Enterprise | loadEnterprise | /kai/enterprise |
| `missions` | Missions | loadMissions | /kai/missions |
| `executive` | Executive | loadExecutive | /kai/missions, /kai/authz* |
| `enhancements` | Enhancements | loadEnhancements | /kai/enhancements |
| `factory` | Factory | loadFactory | /kai/tools/factory* |
| `evolution` | Evolution | loadEvolution | /kai/evolution |
| `wireguard` | WireGuard | loadWireguard | **NEW 2026-09-16** · /api/wg/status, /api/wg/raw |
| `directory` | Service Directory | loadDirectory | /api/directory/services, /api/directory/conformance |

## Fixes 2026-09-16

- `notifications` (Notify) panel pointed at non-existent `/api/notifications`
  (404) → corrected to `/kai/notifications`.
- `approvals` panel pointed at non-existent `/api/approvals/pending` (404)
  → corrected to `/approvals` (also in the META command map).
- `wireguard` panel added; renders live wg0 peers + Tailscale (deerude) peers.

## R2 online monitor

`kai-r2-monitor.service` (CT111) polls `http://192.168.1.110:51880/api` every
20s. R2 is considered **online** when either the R2 WireGuard peer has a
handshake *or* Tailscale peer `100.64.0.3` is Online. On OFFLINE→ONLINE it
enqueues a **critical** notification (`source=r2_monitor`) — which lands in the
Notify panel and raises a Telegram alert — and writes state to
`/var/lib/kai/r2-monitor.json` + `/var/log/kai-r2-monitor.log`.

Code: `core/r2_monitor.py`; unit `kai-r2-monitor.service`; test
`/tmp/test_r2_monitor.py` (offline fake-dashboard test, no real alerts).

## Wiring rule (mandatory)

Every new module/surface MUST be wired into the Command Center as a full page
following the pattern above; the mobile "More" sheet is auto-generated from the
sidebar (`buildMoreMenu()`), so no separate mobile wiring is needed.

## Verification

- `PANEL_TITLES` covers every panel key (no raw-name fallbacks).
- `loadPanel()` dispatcher has a loader for every `nav-item[data-hash]`.
- Each loader fetches its module's backend endpoint(s) and renders a real
  panel (not a stub).

## Notes

- `WireGuard` page proxies the live exit-node dashboard (kaidash on CT103)
  via `cc_extra_routes.wg_status` → `http://192.168.1.110:51880/api`
  (socat bridge chain PVE-B → PVE-A → CT103).
- Coverage doc history: previously audit at
  `docs/kai-command-center-audit-2026-08-03.md` (implementation strategy).
- Runner copy lives at `/opt/ai-orchestrator`; master at `/project/ai-orchestrator`.

## KAI 2.0 Phase 2 (2026-09-18) — one CC + per-model pages + §30 panels

The canonical CC is the FastAPI app (see `docs/COMMAND_CENTER_CANONICAL.md`);
the React SPA at `command.tail82a9ca.ts.net` now 301-redirects to it and its
service is stopped+disabled.

### Runtime per-model pages (directive: Model Fabric)

Each registered model gets its **own page** generated at runtime from
`GET /api/models/catalog`:

- sidebar sub-item under `Models` (`data-hash="model-<slug>"`)
- `<section class="panel" id="panel-model-<slug>">`
- `PANEL_TITLES[model-<slug>] = "<Title> · Model"`
- `loadPanel()` dispatcher → `loadModelPage(id)`
- content: title, provider, kind, artefact, **parameters**, **quantization**,
  size, **context window** (capability) + context loaded, endpoint, cost tier,
  routing weight/roles/task types, **health**, **usage** (attempts, success
  rate, avg/EMA latency, telemetry calls, cost), GPU/VRAM, and a live
  **Test call** action (`POST /api/models/{id}/test`).

Local models (`qwen3-coder:kai` on VM104, VM112 CPU) and configured providers
are all included. The `Models` panel is the **index** (Model Fabric): model
cards, resource utilization, routing chains, provider management, overrides.

### §30 panels (all real, not raw JSON)

| Panel | Source(s) |
|---|---|
| Workforce (`ai-workforce`) | `/api/workforce/teammates`, `/api/workforce/teams`, `/workers`; create teammate, form team, retire, worker health |
| Missions (`missions`) | `/api/missions`, `/api/missions/{id}`; active/history, task graph, verification checks, artifacts, checkpoints |
| Model Fabric (`models`) | `/api/models/catalog`, `/api/fabric/summary`, `/providers`, `/providers/chains`, `/providers/config` |
| Security (`security`) | `/api/security/overview`, `/auth/status`, `/kai/vault/metadata`; AgentGuard, RBAC, policy, vault, events, logins |
| Infrastructure (`infrastructure`) | `/proxmox/registry`, `/api/docker/containers`, `/api/directory/services`, `/api/fabric/summary`, `/network/*`, `/api/vpn/status`, `/api/infra/usage` |
| Communication (`telegram`, `approvals`) | `/kai/telegram*`, `/approvals`, `/api/telegram/webapp-auth` |

### Data Usage panel (2026-09-18)

`Infrastructure` now includes a full-width **Data Usage** card rendered by
`loadInfraUsage()` from `GET /api/infra/usage` (`core/cc_extra_routes.py`).
It shows per-host/CT/VM disk **used/total/%** (with usage bars), network
**rx/tx totals**, and **byte/s rates** derived from successive samples.

Collector: `core/infra_usage.py` — one bounded SSH call to Proxmox B runs host
`df -P -B1` + `/proc/net/dev`, `pct list`/`qm list`, fans out `pct exec <id> df
/proc/net/dev` to every running CT **in parallel**, and hops to Proxmox C via
the node (`100.116.165.100`). Units are pure functions (`parse_df`,
`parse_netdev`, `compute_rates`, `build_usage`) with fixture tests in
`tests/test_infra_usage.py`. Results are cached (TTL 8s) and refreshed in a
background thread; `?refresh=1` does a synchronous refresh (Refresh button).
Mounts `/mnt/wd`, `/mnt/evo`, `/mnt/vm104-nvme`, `/mnt/kai-c` plus the new
`/mnt/sandisk128` are surfaced automatically from the host `df`.

### New/extended endpoints

- `GET  /api/models/catalog[?refresh=1]` — per-model detail (stale-while-revalidate cache)
- `POST /api/models/{model_id}/test` — live fabric test call (operator session / bridge token)
- `GET  /api/fabric/summary` — models/providers/health/routing/utilization
- `GET  /api/security/overview` — AgentGuard/permissions/policy/vault/events
- `GET  /api/infra/usage[?refresh=1]` — disk + network usage per host/CT/VM, with byte/s rates
- `POST /api/workforce/teammates/{id}/retire` — operator retirement

### Responsive

Shell rules verified by Playwright screenshots at **360 / 768 / 1280** for
`home, ai-workforce, missions, models, security, infrastructure,
model-kai-brain`: panel visible and settled, **no horizontal scroll**
(`documentElement.scrollWidth == innerWidth`), tables scroll, mobile bottom
nav + auto-generated "More" sheet. New model pages follow the same shell.

### Related fixes

- `/auth/status` no longer 500s for operator sessions (lazy `ROLE_CAPABILITIES`).
- `/network/topology|connectivity|changes` accept an operator session (the CC
  uses sessions, not the bridge token) — previously logged users out.
- `/proxmox/registry` is served from a background-refreshed snapshot so it no
  longer blocks for ~100s when a Proxmox node API is unreachable.
- Infrastructure `loadInfra()` fetches each source in parallel with hard
  timeouts, so one slow upstream can no longer wedge the panel.

## Juris Kai control plane (Part B, 2026-09-18)

The `legal` panel's Juris Kai surface is now a real control plane
(`core/juris_kai/cc_routes.py`, mounted in `core/api.py`), replacing the old
read-only account card. Card `#juris-cc` renders tabs via `jurisMount()`:

| Tab | Loader | Backend |
|---|---|---|
| Overview | `jurisOverview()` | `GET /api/juris-kai/{health,routing,metrics}` |
| Corpus | `jurisCorpus()` | `GET /api/juris-kai/corpus/{stats,search,documents,document/{id}}` |
| Ingest | `jurisIngest()` | `POST /api/juris-kai/corpus/ingest` (write-gated) |
| Accounts | `jurisAccounts()` | `GET /api/juris-kai/cc/accounts[|/{id}]`, `/cc/usage`, `/cc/payments`; grant-days/tier/ban via existing `/api/juris-kai/accounts/*` |
| Referrals | `jurisReferrals()` | `GET /api/juris-kai/cc/referrals`, `POST /api/juris-kai/referrals/generate` |
| Bot | `jurisBot()` | `GET /api/juris-kai/cc/service`, `POST /api/juris-kai/cc/service/{action}`, `GET /api/juris-kai/activity` |
| Model | `jurisModel()` | `GET /api/juris-kai/routing`, `POST /api/juris-kai/cc/test-query` |
| Cache | `jurisCache()` | `GET/POST /api/juris-kai/cc/cache[/clear]` |

### Endpoints added / aliased (all auth-gated)

| Method | Path | Gate |
|---|---|---|
| GET | `/api/juris-kai/cc/service` | `require_cc_read` |
| POST | `/api/juris-kai/cc/service/{action}` | `require_juris_write` + rate limit + audit |
| POST | `/api/juris-kai/cc/test-query` | `require_juris_write` + rate limit |
| POST | `/api/juris-kai/cc/cache/clear` | `require_juris_write` + rate limit + audit |

Verified `200` with bridge token **and** operator session, `401` without
(13 reads + 3 writes + 2 status/clear; see `tests/test_juris_cc_routes.py`,
55 passed with Part A speed tests).

### Responsive

Playwright (`/opt/visual-qa`, operator JWT session, real API) captured all
8 tabs at **360 / 768 / 1280** — 24/24 rendered, **zero horizontal overflow**
(`documentElement.scrollWidth == innerWidth`), tabs scroll on mobile, tables
live in `.table-wrap`. Interactions verified: test-query returns a real
`qwen3-coder:kai` answer with latency/TTFT, cache clear (`success:true`),
account detail, corpus document detail (integrity + versions), bot state.

### Related fix

`loadLegal()`'s SUSU card hit bridge-only `/api/susu/stats`, whose 401 was
clearing the operator session and bouncing the CC to login. Added `apiSoft()`
(no session-clearing side effect) for that decorative read. Also fixed a tab
race: a slow initial `jurisOverview` fetch could overwrite the newly selected
tab; each async view now carries a per-switch token and drops stale renders.
