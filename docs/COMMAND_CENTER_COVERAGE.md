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
| `infrastructure` | Infra | loadInfra | /kai/infra* |
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