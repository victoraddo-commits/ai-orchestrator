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
| `legal` | Legal Brain | loadLegal | /api/legal/* (brain proxy), /api/juris-kai/reports, /cc/legal |
| `payments` | Pricing & Payments | loadPayments | /api/juris-kai/cc/plans, /cc/pricing, /cc/payments |
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

## Payments + Legal Groups additions (2026-09-19)

New top-level **`payments`** panel ("Pricing & Payments") — deliberately NOT
buried in Legal: provider status (mode, key presence), tier→plan mapping with a
**Create / sync plans** button, the pricing editor, a test checkout, local
subscription counts and recent transactions. The same Pricing view remains in
Legal → Juris (`jurisPricing`).

Legal tab gains a **legal groups** card (`loadLegalGroups`) for admin- and
user-created groups: create/rename/archive, member add/remove/role, user
search + bulk add, and **"Ask Kai to audit documents"** (group-scoped audit that
stores a report). User administration gains a **Create user** form in the Juris
Accounts sub-tab.

### Endpoints added (all auth-gated, `X-Kai-User`/`X-Kai-User-Id` accepted)

| Method | Path | Gate |
|---|---|---|
| GET | `/api/juris-kai/cc/plans` | `require_cc_read` |
| POST | `/api/juris-kai/cc/plans/sync` | `require_juris_write` + rate limit + audit |
| GET/POST | `/api/juris-kai/cc/groups` | read / write |
| GET/PATCH | `/api/juris-kai/cc/groups/{group_id}` | read / write |
| POST | `/api/juris-kai/cc/groups/{group_id}/members` | `require_juris_write` |
| POST | `/api/juris-kai/cc/groups/{group_id}/members/bulk` | `require_juris_write` |
| PUT/DELETE | `/api/juris-kai/cc/groups/{group_id}/members/{account_id}` | `require_juris_write` |
| POST | `/api/juris-kai/cc/groups/{group_id}/audit` | `require_juris_write` + audit |
| GET | `/api/juris-kai/cc/groups/{group_id}/reports` | `require_cc_read` |
| GET | `/api/juris-kai/cc/users/search` | `require_cc_read` |
| POST | `/api/juris-kai/cc/accounts` | `require_juris_write` + audit |
| POST | `/api/juris-kai/cc/accounts/{id}/deactivate\|activate\|grant-days\|subscription` | `require_juris_write` + audit |

Guarded by `scripts/cc_contract_check.py` (endpoint contract) so a panel can
never call a missing route.

## Roadmap gaps closed (2026-09-21)

### Diagnostics panel (`diagnostics`)

- Sidebar `nav-item[data-hash="diagnostics"]`, `<section id="panel-diagnostics">`,
  `PANEL_TITLES.diagnostics`, `loadPanel()` → `loadDiagnostics()`.
- Loader `loadDiagnostics()` renders metric row (health / anomalies / open
  breakers / providers), lifecycle-object counts, circuit-breaker table,
  provider-health table and the telemetry summary from `GET /api/diagnostics`.
- Backend `core/cc_extra_routes.py::api_diagnostics` — operator-gated
  (session OR bridge token OR `X-Kai-User`/`X-Kai-User-Id`). Aggregates
  `core.observability.snapshot()`, the enriched circuit breakers (reusing
  `circuit_breakers_list_endpoint`), `core.telemetry.snapshot()` and
  `provider_health.get_all_quota_snapshots()`. One failed section degrades to
  an `error` field; it never 500s the page.
- Was `404`; now `401` without an operator and `200` with one.

### Audit panel (`audit`)

- Sidebar `nav-item[data-hash="audit"]`, `<section id="panel-audit">`,
  `PANEL_TITLES.audit`, `loadPanel()` → `loadAudit()`.
- `loadAudit()` renders a server-filtered table (source / severity / actor /
  action) from `GET /kai/audit`, with colour-plus-label severity badges.
- Backend: `GET /kai/audit` is now an alias of `/audit` (delegates to
  `get_audit_log`, so the shape and sources can never drift). `AUDIT_SOURCES`
  gained `command_bus_audit.json` (Command Bus) and `execution_audit.json`
  (remediation runner), with normalizers and a derived `severity`
  (info/warn/error) on every merged entry.

### Mission steering routes (no new panel)

`POST /kai/missions/{id}/steer` (pause/resume/redirect) and
`POST /kai/missions/{id}/execute` (stop) now exist and persist through the
Mission Engine (`core/kai/mission_engine.py::steer_mission`), so the existing
Missions panel and the Telegram `/pause /resume /stop /redirect` commands
(`core/mission_steering.py`) work end-to-end. `paused → stopped` was added to
the mission state machine so a paused mission can be stopped. Operator-gated;
unknown mission `404`, forbidden transition `409`, malformed redirect `422`.

### Infra event publishers (change-only, deduped)

Three workers that had **no** `kai_event_bus` publishes now emit on genuine
state change only (no steady-state spam):

| Module | Topic | Trigger / dedupe |
|---|---|---|
| `core/health_worker.py` | `infra.health.changed` | container-state signature differs from previous sample (signature compare) |
| `core/network_discovery_cycle.py` | `network.node.changed` | each diff returned by `detect_changes` (already change-only) |
| `core/provider_health_monitor.py` | `provider.health.changed` | a provider's health differs from its previous check (`_last_health` map) |

Tests: `tests/test_infra_event_publishers.py` (7 cases incl. dedupe and
no-publish-on-baseline).

### World Model panel (`world`)

- Sidebar `nav-item[data-hash="world"]`, `<section id="panel-world">`,
  `PANEL_TITLES.world`, `loadPanel()` → `loadWorld()`.
- `loadWorld()` renders the snapshot timestamp, entity/edge/change metric row,
  the change list (from→to), entities-by-type, the entity table
  (id/type/label/status) and the dependency-edge table from `GET /kai/world`.
- Backend `core/cc_extra_routes.py::kai_world` returns the full
  `core.world_model.get_snapshot()` (building one if absent) with entities
  flattened to a sorted list; read-only, same access policy as
  `/kai/tools/world`.

### Factory 500 fix

- Root cause: `FACTORY_HOST` was the stale `192.168.1.119`; CT109
  (kai-android-factory) is `192.168.1.120` (net0 verified 2026-09-21), and
  `_factory_ssh` raised an unhandled `RuntimeError` on the SSH auth failure.
- Fix: corrected the host and made `factory_status` / `factory_reports` /
  `factory_build` return an honest degraded payload
  (`available: false` + `error`) so the routes are **200, not 500**. The
  Factory panel renders the unreachable state with the error and a Retry.
- The factory host still rejects SSH (no key authorised for CT111); the
  endpoint is now honest about that instead of crashing.

## Legal Brain panel (2026-09-24, Phase 8 Task 3)

The `legal` panel is now the **Legal Brain** (sidebar label `Legal Brain`) and
leads with a native, tabbed Legal Brain card (`#legal-brain-card`) rendered by
`loadLegal()` → `legalBrainMount()`. The existing legal-app cards (Juris Kai
control plane, SUSU, Knowledge Engine, Legal Groups) remain below it.

| Tab | Loader | Backend (all via CT111 proxy) |
|---|---|---|
| Ask | `lbAskView()` / `lbAskRun()` | `POST /api/legal/ask` (quick grounding or `deep=true` → `reasoning.run_deep`); `POST /api/juris-kai/reports` for Export |
| Reports | `lbReportsView()` | `GET /api/juris-kai/reports`, `GET /api/juris-kai/reports/{id}.{pdf,docx}` |
| Gaps (Ask-to-Acquire) | `lbGapsView()` / `lbGapAcquire()` | `GET /api/legal/gaps`, `POST /api/legal/gaps/{id}/acquire` |
| Everyday Law | `lbEverydayView()` / `lbEverydayFetch()` | `GET /api/legal/everyday`, `GET /api/legal/everyday/{topic}` |
| Corpus health | `lbHealthView()` | `GET /api/legal/health` + `GET /api/legal/coverage` |
| Licences | `lbLicencesView()` | `GET /api/legal/licences` |

Wiring points (all present): sidebar `nav-item[data-hash="legal"]`,
`<section class="panel" id="panel-legal">`, `PANEL_TITLES.legal = "Legal Brain"`,
`loadPanel()` → `loadLegal`, and `loadLegal()`.

### Proxy routes added (`core/cc_extra_routes.py`, operator-gated)

| Method | Path | Brain call |
|---|---|---|
| GET | `/api/legal/health` | `legal_brain_client.legal_health` (30s timeout) |
| GET | `/api/legal/coverage` | `.coverage` |
| GET | `/api/legal/gaps` | `.list_gaps` |
| POST | `/api/legal/gaps/{id}/acquire` | `.acquire_gap` (write) |
| GET | `/api/legal/everyday` | `.everyday_topics` |
| GET | `/api/legal/everyday/{topic}` | `.everyday` |
| GET | `/api/legal/licences` | `.licences` |
| GET | `/api/legal/relations/{doc_id}` | `.relations` |
| GET | `/api/legal/status/{doc_id}` | `.status` |
| POST | `/api/legal/ask` | `grounding.build_grounded_plan` / `reasoning.run_deep` (write) |

Every route requires an operator (bridge token, CC session, or the auth-proxy
`X-Kai-User`/`X-Kai-User-Id` identity) and `401`s otherwise; the brain base URL
and token are injected server-side and never reach the browser.

### Brain endpoint added (CT100)

`GET /licences` (token-gated) serves `core.legal.licenses` as
`{version, register, markdown}` so the Licences tab can render the source
commercial-use register as a read-only table. Verified `401` unauth / `200`
auth (29 sources, 1 commercial-cleared).

### Verification

- `tests/test_cc_legal_routes.py` (25 cases): auth gate + delegation + ask
  quick/deep/refusal. `tests/test_licences_api.py` (2 cases) on CT100.
- CT111 `tests/test_juris_*.py tests/test_cc_*.py tests/test_legal_*.py` →
  **683 passed**. `scripts/cc_contract_check.py --identity` → **CONTRACT OK**
  (172 CC endpoints).
- Live: served `/command-center` contains the `legal` nav-item, `panel-legal`,
  `legal:loadLegal`, `loadLegal()`, `legalBrainMount()`, `lbAskView()`,
  `#legal-brain-card`; each tab loaded live (Ask GROUNDED on
  `qwen3-coder:kai`; reports list + PDF/DOCX download; 3 gaps; 7 everyday
  topics; health docs=1445; 29 licence sources). Deep ask + report export
  verified end-to-end (PDF `%PDF-`, DOCX `PK`).

## WireGuard device management (2026-09-24)

The `wireguard` panel is now a **device management** surface for the CT102
"device pool" (`wireguard` CT, `10.6.0.1/24`, UDP 51860), not just a read-only
exit-node view. The CT103 exit-node mesh (`headscale`, `192.168.66.0/29`,
R1/R2) remains available via the panel's **Exit node (CT103)** button, which
still calls the read-only `GET /api/wg/status` + `GET /api/wg/raw` kaidash
bridge.

**Why CT102**: it is the only server with a `/24` pool (~250 devices); CT103's
`/29` is the small exit-node mesh. Confirmed live: CT102 `wg0 = 10.6.0.1/24`,
listen 51860, 3 existing peers (`test`, `S21 Ultra`, `MTN router`).

**Design**: the CC/CT111 never reaches CT102 directly — CT102 is *not*
reachable on the LAN from the CC (ARP/TCP to `192.168.1.182` fail from PVE-A,
PVE-B and CT111; only `pct exec` works). Management therefore runs over the
existing key-based SSH chain `CT111 -> PVE-B -> PVE-A -> pct exec 102`, calling
a stdlib-only agent (`/opt/kai-wg-agent/wg_agent.py`, source `core/wg_agent.py`).
No new listener is exposed on the WireGuard host. Controller:
`core/wg_peer_service.py` (injectable SSH transport, QR rendering via `segno`).
Every write backs up `wg0.conf` first and is audit-logged.

### Panel wiring points (all present)

- sidebar `nav-item[data-hash="wireguard"]` (unchanged)
- `<section class="panel" id="panel-wireguard">` (unchanged)
- `PANEL_TITLES.wireguard` (unchanged)
- `loadPanel()` → `loadWireguard` (unchanged key; body upgraded)
- `loadWireguard()` + helpers `wgAddForm/wgAdd/wgShowResult/wgExport/wgDownload/`
  `wgShowQR/wgPause/wgResume/wgDelete/wgExitNode/wgRaw`

### Proxy routes added (`core/cc_extra_routes.py`, operator- or token-gated)

| Method | Path | Delegates to |
|---|---|---|
| GET | `/api/wg/peers` | `wg_peer_service.list_peers` |
| POST | `/api/wg/peers` | `.add_peer` (allocate IP + keygen + config/QR) |
| POST | `/api/wg/peers/{pubkey}/pause` | `.pause_peer` |
| POST | `/api/wg/peers/{pubkey}/resume` | `.resume_peer` |
| DELETE | `/api/wg/peers/{pubkey}` | `.delete_peer` |
| GET | `/api/wg/peers/{pubkey}/config?type=wg\|ddwrt\|openwrt` | `.peer_config` |
| GET | `/api/wg/peers/{pubkey}/qr?type=wg[&raw=1]` | `.peer_qr` (segno PNG) |

`{pubkey}` path segments use a URL-safe opaque encoding (WireGuard pubkeys may
contain `/` and `+`); raw pubkeys without those chars also work. All routes
401 without an operator session, bridge token, trusted-proxy identity, or the
optional `WG_CTL_TOKEN` service token (`X-Kai-WG-Token`). The server private
key is never returned; a client's private key appears only in that client's own
config/QR. Peer list responses never include private keys.

### Tests

`tests/test_wg_agent.py` (22), `tests/test_wg_peer_service.py` (9),
`tests/test_cc_wg_routes.py` (14): IP allocation, keygen/rollback, pause/resume/
delete, all three export formats, no-key-leak in list, auth gates (401), error
mapping (400/502), URL-safe pubkey routing, backup-on-write.
