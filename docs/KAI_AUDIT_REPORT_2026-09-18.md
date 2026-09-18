# KAI ECOSYSTEM — COMPREHENSIVE VERIFICATION & AUDIT REPORT

Date: 2026-09-18 · Protocol: `docs/KAI_AUDIT_PROTOCOL.md` · Method: 5 parallel read-only domain audits
Statuses: VERIFIED | PARTIALLY_VERIFIED | UNVERIFIED | MISSING | BLOCKED | DEGRADED | FAILED

---

## EXECUTIVE VERDICT

The ecosystem is **running but not healthy**. It self-reports more capability than it has.
Three headline facts:

1. **The orchestrator has not completed a working cycle since 2026-09-13 17:19** — 193 of 197
   cycles today errored on a missing `docker` binary; a single stale remediation request
   deadlocks the whole cycle. Heartbeat is 5 days stale.
2. **119 of 120 builds FAILED** (1.2% success). The roadmap claims 98.6% complete with
   0 pending — bookkeeping has diverged from reality.
3. **No verified restorable backup exists.** PVE-B root is 100% full; CT backups fail
   (ENOSPC/locks); the only "off-host" copy is another disk on the same node.

Security has a **critical** issue (root web shell with weak creds) and several high issues.

---

## ANSWERS TO THE STANDING QUESTIONS

**1. Does Kai self-healing work? — NO (detect only; remediation fails).**
Incidents are auto-detected every cycle, but: the self-heal loop isn't scheduled
(on-demand API only, empty action log); the structured engine is unwired; the
remediation executor fails 54/56 times because Docker is absent on CT111; and the one
approved remediation request is what wedges the cycle. No real detect→remediate→verify
event exists. (Positive: the VM104 Ollama tunnel watchdog *does* self-heal.)

**2. Can Kai find ways to upgrade itself? — PARTIALLY (discovers/plans, never implements).**
Live failure-derived proposals exist (GEN-001..003 from `builds.json`); one historical
proposal was promoted to a roadmap phase (gemini→17X). But `kai_evolution` has one change
ever, marked `needs_human`, never applied; recent proposals are test data; no phase links
to a proposal; capabilities lack health; skills are static. No autonomous *applied* upgrade.

**3. Is Kai working optimally? — NO.**
- 0 completed cycles since 2026-09-13; heartbeat stale.
- 1.2% build success; top cause "no available provider for task_type='coding'" (49).
- Model fabric is one model on one GPU (`qwen3-coder:kai` on the P40), no live fallback;
  cloud paths dead (FreeLLMAPI OpenRouter 401→502; groq/google no key; OmniRoute 402/429
  and orphaned from the router) while the key table still reports "healthy" (stale).
- Cost is unmeasurable: `/api/budget`, `/api/costs/*` all HTTP 500; ~4 days of no
  production AI activity (idle).
- Second Brain writes ~27 MB/hr of full-file snapshots requiring hourly compaction.

**4. Is the Second Brain working? — PARTIALLY.**
Ingest (operational 750 records + project 21) and *unfiltered* retrieval work
(`/kai/brain/query?limit=5` returns real records; compaction reclaimed 26.8 MB).
But **entity-filtered retrieval is broken** (`?entity=…` returns 0 due to a supersedes
merge bug — the path most consumers use), the knowledge corpus is 2 test docs with no
embeddings, the event bus journal is empty since Sep-1 (Redis layer down), and nothing
except RAG consumes the Second Brain.

**5. What is happening to Jarvis? — PARTIALLY_VERIFIED.**
"Jarvis" is a **codename, not a service** (no `core/jarvis*`). Its parts: **Tool Bus +
Mission Control work** (`/kai/tools` 46 tools; mission sweep ran today). **Voice is not
running**: the standalone gateway (:8130) has no systemd unit and isn't listening;
`faster-whisper` is not installed; the piper voice model is missing; `/kai/voice/*`
returns 503; the HUD is absent (404). Roadmap: KX1 completed, 17W/13K/13T completed,
**17N (voice/telephony) cancelled**. Note `kai_services.json` falsely reports voice "running".

**6. Are the teams working? — NO.**
Creation persisted exactly one teammate (`coder-01`, READY) — but it was never assigned
or dispatched (`assigned_missions: []`, metrics `{}`), **zero completed tasks**, and
**zero `teammate.*` events** ever. The library is real (52 unit tests pass) but nothing
instantiates the Factory/Dispatcher at runtime; `Team.execute` needs a caller-supplied
runner that doesn't exist. Operator commands are dead (`/create-team` → "no team
dispatcher configured"). Roadmap 21A–21T claim "completed" — false-completion drift.

**7. Can Kai create teams on its own? — NOT in the running system.**
The capability exists as code (`Factory.create_from_requirement`,
`assemble_engineering_team`) but no scheduler loop or API invokes it; the operator path
is broken. Status: code VERIFIED (unit), integration MISSING.

**8. Can OpenCode utilize Kai's teams? — NO.**
The full OpenAPI (422 paths) has **no** `/teams`, `/teammate`, `/workforce`, or
`/kai/missions/{id}/team` endpoint; `/kai/tools` has no team tool; `/kai/agents` is the
model-provider registry, not teams. The nearest interfaces are `GET /kai/tools` and
`POST /kai/tools/{id}/execute` (auth-gated) + `/kai/missions.create`. A "create team"
call does not exist. Verdict: **MISSING**.

---

## FINDINGS BY DOMAIN (highest impact)

### A/B/C — Orchestration, self-healing, self-upgrade
- **P0** Scheduler cycles abort on missing `docker` (remediation for a stale
  `restart_container`/`proxmox-node` request) → cycle never completes, heartbeat dead,
  incident `cd219fd1` open with 1,684 occurrences.
- **P0** Master↔runner drift: divergent git histories; "master is source of truth" is false.
- Roadmap 277/281 "completed" vs builds 119 FAILED — statuses need reconciliation.
- Self-heal remediation: 54/56 failed; VPN monitor disabled; incidents bulk-closed by hand.
- Self-upgrade: proposals discovered but not persisted/promoted; `kai_evolution` never applied.
- Missions engine VERIFIED (daily sweep ran today). Emergency stop idle/OK.

### D/E — Model fabric, Second Brain
- **VERIFIED:** VM104 Ollama (`qwen3-coder:kai`, ~53 tok/s), VM112 llama.cpp, GPU arbiter,
  provider health monitor (216 checks), VM104 tunnel self-heal (recovered 2026-09-13).
- **FAILED:** cost tracker (null-`usage` crash → `/api/budget`, `/api/costs/*` 500);
  entity-filtered SB retrieval; Redis event bus (down).
- **DEGRADED:** all roles = one local model (SPOF); context 8192 vs 262144 capability;
  FreeLLMAPI cloud providers dead but reported healthy; OmniRoute orphaned; GPU 91% VRAM,
  0% util; SB operational-store churn (~27 MB/hr).

### F/G — Jarvis, teams
- Jarvis voice not deployed; STT package missing; TTS model missing; HUD 404; 17N cancelled.
- Teammate subsystem unwired; no dispatcher; no API; no UI; operator team commands dead;
  52 unit tests pass but the system does nothing. Workforce registry (39) and AI agent
  registry (12) work — but those are providers, not teams.

### H/I — Command Center, services
- FastAPI CC (40 panels) 40/40 structurally wired; **two competing Command Centers** exist
  (FastAPI @ orchestrator.tail… vs React SPA @ command.tail…). 4 panels broken
  (`airdrop` wrong port 4201→4202, `arbitra` dead host .118, `secondbrain` 404 path,
  `docker` httpx transport error); 6 panels are raw JSON dumps; telemetry/observability
  not surfaced.
- Service Directory: conformance "green" is trivially true (stored ⊃ discovered); 84/106
  statuses `unknown` (no scheduled refresh); PVE-A services falsely `down` (no route);
  dead `.118` entries (notify/arbitra/money); junk `dep-test`/`test-service`.
- Services working: legal-brain, it-manager, kai-docs, bet-susu, talent, kai-vault,
  android-factory, orchestrator API, directives, freellmapi, opencode, inbox, directory,
  proxdash, airdrop, React CC, headscale.
- Idle/dead: CT102 network-core-b, CT110 kai-browser, PVE-A CT102 (no workload);
  CT108 destroyed but still referenced; PVE-A CT100 duplicate it-manager/kai-docs
  (units active, endpoints dead); deerude.com 502.

### J/K — Infra, security
- **P0** PVE-B root **100% full**; `kai-c` backup storage is not an NFS mount (it's a dir
  on root); vzdump fails ENOSPC; CT100 `mounted` lock, CT107 `snapshot-delete` lock;
  vzdump job still lists destroyed CT108.
- **P0** App-state backup failed 2026-09-18 (TOCTOU on `builds.json.tmp`); only
  incomplete `.part`; no verified restorable backup. Off-host copy is same node.
- **P1** Single-node SPOF; `claude-code` (master/gateway) offline 5 days; stale `.109` refs.
- **CRITICAL** `ttyd` root web shell on `0.0.0.0:7681` (`root:changeme123`, unit 0644,
  no node firewall).
- **HIGH** cloudflared tunnel secret world-readable (0644); passwords in unit files
  (opencode-serve, kai-command-center-auth); unauthenticated upload server on CT113
  `:8333`; many unauthenticated `0.0.0.0` services on a default-ACCEPT LAN; `:8000`
  firewall rule not persistent across reboot.
- **MED** vault offers plaintext HTTP reveal on 0.0.0.0:8120; AgentGuard approval/audit are
  TODOs; orchestrator has no audit chain; Proxmox health false-negative; world-readable
  `memory/api_keys.json` + 62–85 MB stale `.tmp` fragments.
- **INFO** Tailnet exposes 18 services (no funnel). **Disclosure:** during the audit,
  extension-less token files were printed into this transcript (redaction only caught
  `KEY=value`); **rotate** `android_factory_token`, `arbitra_token`, `deerude_bot_token`,
  `duo_session_secret`, `kai_docs_token`, `talent_token`, `vault_lease_key` if retained.

---

## PRIORITIZED REMEDIATION BACKLOG

**P0 — stop the bleeding**
1. Fix/clear the stale `restart_container` approval so the scheduler cycles complete; guard
   `container_status()` on hosts without Docker; restore the heartbeat.
2. Free PVE-B root; mount PVE-C NFS (`100.116.165.100:/srv/kai-backups`) at `/mnt/kai-c`;
   clear CT100/107 locks + snapshots; remove CT108 from vzdump; run and **verify a restore**.
3. Rotate the leaked tokens (disclosure) and the `ttyd` credential; disable/restrict ttyd.
4. Fix entity-filtered Second Brain retrieval; fix the cost tracker crash.

**P1 — restore capability**
5. Reconcile roadmap statuses vs builds; reopen failed phases; re-queue work.
6. Wire teammates at runtime (Factory+Dispatcher in the cycle), add `/api/teams` +
   `/kai/missions/{id}/team`, inject the dispatcher into operator commands → teamwork + OpenCode access.
7. Restore a real model fallback (Fund OpenRouter or wire OmniRoute) and fix stale health.
8. Fix broken CC panels; schedule Directory health refresh + probe PVE-A over tailnet;
   clean dead/junk directory entries.
9. Move secrets out of unit files; `chmod 600` cloudflared + memory state; persist CT111 firewall.

**P2 — harden & optimize**
10. Resolve the two-Command-Center duplication; wire telemetry/observability; build/retire idle CTs.
11. Raise Ollama context; bound Second Brain churn; ingest a real knowledge corpus + embeddings.
12. Mount off-node backups; test node-loss recovery; validate tailnet ACLs.

---

## WHAT OPENCODE CAN DO NEXT (gate-free)
- Fix the scheduler wedge + Docker guard (unblocks the whole autonomy loop).
- Fix the SB entity-query bug and the cost-tracker 500s.
- Reconcile roadmap↔builds and clean the Service Directory.
- Rotate the leaked tokens and lock down ttyd/cloudflared/units.
- Restore the backup path (mount + fix backup_manager race + verify restore).
- Wire the teammate Factory/Dispatcher + `/api/teams` so teams and OpenCode integration exist.
