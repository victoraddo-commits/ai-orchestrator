# KAI 2.0 — MASTER COMMAND CENTER (DIRECTIVE v2 — AUDITED & EXECUTABLE)

> **Supersedes:** `directives/20260925T134345Z-pasted-directive.md` (v1, 3539 lines).
> v1 is **preserved unmodified** for provenance. This v2 is the *operative*
> directive: it keeps v1's vision and every hard rule, but removes ambiguity,
> binds every requirement to the real KAI estate, and adds binding operationally.

---

## 0. HOW v2 DIFFERS FROM v1 (read this first)

v1 was a strong vision document but under-specified and, in two places, factually
weak:

1. **It said "audit first" but gave no destination names.** v2 pre-loads the
   audit findings (Section 4A) so the implementer starts from truth, not a search.
2. **It said "reuse existing systems" without naming them.** v2 names the exact
   modules and their real state (Section 6A), so reuse cannot be confused with
   "not discovered, therefore rebuilt".
3. **Smart Home, UPS, and Voice are mostly greenfield in KAI today.** v1 implies
   they exist. v2 marks each as *present / absent / stub* and defines what
   "integrate" vs "build" means, so the plan is honest.
4. **It had no execution contract.** v2 adds phases, per-phase exit gates,
   evidence requirements, and a definition of done that a verifier can check.
5. **It had no guard for the hardest real constraint:** the Sollatek UPS may not
   speak NUT, and Proxmox B has no USB UPS device attached today. v2 makes
   "verify the protocol before writing any integration" a blocking gate.
6. **It risked scope sprawl.** v2 keeps "nothing is overkill" for *depth* but
   sequences the work so each phase ships something real and verifiable.

Everything in v1 Section 106 (ABSOLUTE RULES) and Section 105 (acceptance list)
is retained and reinforced below.

---

## 1. ACCESS & EXECUTION CHAIN (mandatory — do not rediscover)

- Runner repo: **CT111 `/opt/ai-orchestrator`** (branch `runner-kai-2.0-20260918`).
- Deploy: edit on the local working copy → push to CT111 → restart
  `ai-orchestrator-api.service`. Restart alone does **not** reload the served
  `core/kai/command_center.html`; verify the served bytes after every UI change.
- Host chain from LXC 113: `ssh -i /root/.ssh/pve2_deploy root@192.168.1.110`
  (PVE-B) → `pct exec 111 …`. Proxmox A: from PVE-B `ssh root@100.83.4.27`.
  Proxmox C: `ssh root@10.30.30.2` over the direct link.
- Never commit secrets. Vault is the credential source of truth (Section 88).

---

## 2. HARD RULES (carried from v1 §106, unchanged)

```text
DO NOT ASSUME. VERIFY.
PROXDASH DOES NOT EXIST. DO NOT RECREATE IT.
UPGRADE THE EXISTING KAI COMMAND CENTER. DO NOT CREATE A SECOND COMMAND CENTER.
DO NOT CREATE A SECOND SMART HOME ASSISTANT.
HOME ASSISTANT IS A DEVICE/INTEGRATION FABRIC; KAI IS THE INTELLIGENCE/CONTROL PLANE.
KAI BRAIN AND WORLD MODEL REMAIN AUTHORITATIVE.
DO NOT DUPLICATE: Command Bus, Event Bus, Mission Engine, Worker Registry,
  Model Fabric, Device Registry, AgentGuard, Duo, Vault, identity, audit,
  notification, monitoring.
AUTHORIZATION MUST NOT EXIST ONLY IN THE FRONTEND.
DO NOT PUT SECRETS IN THE FRONTEND.
DO NOT FABRICATE DATA / HEALTH / UPS TELEMETRY / DEVICE STATE / MISSION PROGRESS /
  NETWORK STATE / SECURITY STATE.
UNKNOWN MUST REMAIN UNKNOWN. STALE DATA MUST BE LABELED STALE.
COMMAND SENT != COMMAND SUCCESS. USE READBACK. AUDIT CRITICAL ACTIONS.
POWER ACTIONS ARE CRITICAL. THE SOLLATEK UPS IS CRITICAL PROXMOX B INFRASTRUCTURE.
DO NOT SEND UNSUPPORTED UPS COMMANDS.
DO NOT INTENTIONALLY DRAIN/SHUT DOWN THE LIVE UPS OR PROXMOX B TO TEST IT.
DO NOT DELETE EXISTING SYSTEMS WITHOUT EXPLICIT AUTHORIZATION.
DO NOT CLAIM VERIFIED WITHOUT EVIDENCE.
```

---

## 3. MISSION (unchanged)

Upgrade the **existing KAI Command Center** into one unified visual/operational
control plane for: KAI Brain, missions, workers/models, Smart Home, rooms,
automations, security, cameras, networks, Proxmox A/B/C, CTs/VMs, GPUs/resources,
power, the Sollatek UPS, KAI services, events, escalations, approvals, knowledge,
Voice, autonomous activity, "why", recovery, and verification.

It must feel like a premium AI operating system controlling an intelligent
environment — **not** a CRUD panel, a Home Assistant clone, a NOC, a card grid,
a monitoring-only dashboard, or a chatbot wrapper.

---

## 4. PHASE 0 — AUDIT (BLOCKING)

Produce an inventory with these columns for **every** system touched:

```text
SYSTEM | LOCATION | PURPOSE | API | DATA SOURCE | DEPENDENCIES |
AUTHORIZATION | CURRENT STATE | REUSE/MODIFY/REPLACE | RISKS | VERIFICATION METHOD
```

### 4A. PRE-LOADED FINDINGS (verified 2026-09-25 — confirm, don't rebuild)

**Command Center / UI**
- `core/kai/command_center.html` — single-file SPA (~7.5k lines), vanilla JS,
  hash routing via the `loadPanel()` dispatcher, `PANEL_TITLES`, `api()/apiT()/
  apiSoft()` fetch helpers with session token `X-Kai-Session`, dark design tokens
  (`kai-design-system` skill), Playwright visual-QA harness at `/tmp/opencode/pw`.
  Coverage doc: `docs/COMMAND_CENTER_COVERAGE.md`.
- Backend: `core/api.py` (FastAPI, HTTPS :8000) + `core/cc_extra_routes.py`
  (drift-safe extras, operator-gated). WebSocket support exists.

**Existing KAI subsystems to REUSE (do not duplicate)**
| Capability | Module | State |
|---|---|---|
| Command Bus (+AgentGuard, audit) | `core/command_bus.py` | present, used by `kai_control_commands` |
| Event Bus | `core/eventbus/bus.py` | present (thin) |
| Device Registry | `core/device_registry.py` + `device_registry_routes.py` | present — **mobile device** registry (device_id, bearer tokens, heartbeat, pending commands). **This is NOT a smart-home device registry.** Treat as a separate concern; do not overload it. |
| World Model | `core/world_model.py`, `world_model_sync.py`, `/kai/world`, `/kai/tools/world` | present, 82 entities |
| Missions | `core/kai_missions.py`, `mission_steering.py` | present |
| Workforce | `core/workforce/`, `kai/workforce*` | present |
| Model fabric | `core/weighted_routing.py`, provider health monitor, `/api/betting/sources` pattern | present |
| AgentGuard | `core/agentguard/guard.py` | present |
| Vault | `core/vault/`, `/kai/vault/metadata` | present |
| Second Brain | `core/second_brain/` | present |
| Knowledge / legal | `core/knowledge/`, `/mcp/*`, legal-brain proxy | present |
| Telegram | `core/telegram/*`, `/api/cc/telegram/*` | present |
| Voice | `core/voice_gateway/` (whisper/piper/elevenlabs, wake word, VAD, WSS) | present, optional |
| Health / diagnostics | `core/health.py`, `/kai/doctor`, `/kai/guardian`, observability | present |
| Infra usage | `core/infra_usage.py`, `/api/infra/usage[/history]` | present (PVE-B + PVE-C) |
| Network | `core/network_inventory.py`, `/api/network/*`, topology engine | present |
| WireGuard | `core/wg_agent.py`, `wg_peer_service.py`, `/api/wg/*`, `/api/wg/mesh` | present (CT102-PA pool + A/B/C mesh) |
| Proxmox monitor | `core/proxmox_monitor.py`, `proxmox_discovery.py` | present (A via Tailscale forward, B, C via direct link) |

**Absent or stub (this directive may need to BUILD, inside KAI — never beside it)**
| Capability | State | Implication |
|---|---|---|
| Smart Home fabric (HA/Tuya/SmartLife/eWeLink/Alexa/TTLock/V380/eero) | **no integration found** | Phase H builds an adapter layer on the Event/Command bus |
| Canonical Smart-Home device registry | **absent** (mobile device_registry is unrelated) | Build one registry keyed into the World Model; do not touch the mobile registry |
| UPS (Sollatek) telemetry/control | **absent**; PVE-B shows **no USB UPS device** and no `hidraw*` today | Phase P is blocked on the protocol audit (Section 7) |
| Rooms / automations | **absent** | Build on the device registry + event bus |

**Rule:** if Phase 0 finds any of the above actually exists elsewhere, REUSE it
and record the correction in the inventory. Search the whole repo first.

---

## 5. PHASE PLAN (each phase ships working, testable software)

Every phase: TDD, evidence captured, `docs/` updated, no dead routes/buttons.

### Phase A — Design language & shell unification
- Enforce the `kai-design-system` tokens; ONE product feel across every panel.
- Global Command Bar (NL input), KPI strip, adaptive priority, global status,
  command palette, wall-display mode, offline/degraded banner.
- Exit gate: Playwright screenshots at 360/768/1280; zero console errors; every
  existing panel still reachable (regression list from `COMMAND_CENTER_COVERAGE.md`).

### Phase B — Brain / Mission / Workforce command surface
- Wire real endpoints: `/kai/world`, `/kai/missions`, `/kai/workforce*`,
  `/kai/doctor`, `/kai/guardian`, `/approvals`, event/command bus status.
- Live coordination map + self-healing panel + "why did this happen?".
- Exit gate: every tile backed by a real endpoint; UNKNOWN rendered as UNKNOWN.

### Phase C — Infrastructure / Network / Power(non-UPS)
- Proxmox A/B/C, CTs/VMs (host-qualified `CT102-PA` labels already established),
  GPUs, storage, network, WireGuard pool+mesh, data usage.
- Exit gate: real numbers only; stale data labeled; host labels consistent.

### Phase D — Security center
- Reuse Duo step-up, AgentGuard, Vault, audit, sessions (no duplicates).
- Sensitive-action pipeline: authorize → execute → readback → verify → audit.
- Exit gate: a destructive action is impossible without an audited, verified path.

### Phase E — Smart Home foundation
- Provider adapter layer (HA first, then Tuya/SmartLife/eWeLink/Alexa/TTLock/
  V380/eero) on the Event/Command bus; canonical device registry → World Model.
- Exit gate: one provider end-to-end (discover → register → control → verify).

### Phase F — Rooms / Devices / Automations / Cameras / Climate / Media
- Room mapping, device controls, automation builder + visualization, safety gates.
- Exit gate: an automation can be created, run, read back, and audited.

### Phase G — Voice / Knowledge / Activity / Search / Programs
- Reuse `core/voice_gateway` (WSS), knowledge/legal, activity feed, global search.
- Exit gate: NL command routed through the Command Bus with a real result.

### Phase P — Sollatek UPS (BLOCKED until Section 7 passes)
- Authoritative KAI UPS telemetry/control, UPS data model, states, state machine,
  failure scenarios, Proxmox B protection relationship, power safety, dashboard,
  event history, energy data.
- Exit gate: protocol verified, telemetry real, **no fabricated field ever shown**,
  no live drain test.

**Sequencing:** A→B→C→D can proceed now. E→F→G follow. P is gated on the UPS
protocol audit. Do not start P before its gate passes.

---

## 6. REUSE MAP (v1 §6 expanded — names included)

- Assistant/Brain/Command Bus/Event Bus/World Model/Device Registry/Mission
  Engine/Worker Registry/Model Router/Authorization/Audit/Vault/Identity/
  AgentGuard/MFA/Notifications/Monitoring: **all exist** — integrate, never clone.
- The only genuinely new subsystems are: **Smart Home adapter layer**, **canonical
  smart-home registry**, **rooms/automations**, and **UPS integration**. Each must
  route through the existing Command/Event bus, AgentGuard, Vault, and audit.

---

## 7. UPS PROTOCOL GATE (blocking — replaces v1 §44-45 ambiguity)

Before writing any UPS code, produce a signed evidence note:

1. Is a UPS physically attached to Proxmox B today? (Currently: no `hidraw*`,
   no USB device — confirm on the live node.)
2. If attached: capture `lsusb -v`, `/dev` node, `dmesg`, and the exact protocol
   (USB HID Power Device, Megatec/Q1 over RS-232, Voltronic/QPIGS, NUT driver).
3. Test with NUT (`nut-scanner`, `upsdrvctl -D`) **read-only** on a non-critical
   port first. Capture which fields the device actually returns.
4. Only fields actually returned may be displayed. Every other field renders
   `UNKNOWN` — never a plausible default.
5. Shutdown/control commands: only those proven supported; every power action
   runs the Section D sensitive-action pipeline.

**Do not proceed to Phase P until steps 1-5 are evidenced.**

---

## 8. EVIDENCE CONTRACT (v1 §105 made checkable)

A phase is DONE only when, for each of its claims, the implementer can paste:

- the command run and its output (tests: pass count; routes: live status codes;
  UI: Playwright JSON + screenshot paths);
- the served file hash comparison (UI actually deployed);
- the audit event id for any sensitive action;
- an explicit list of anything still UNKNOWN/blocked with the reason.

"No dead buttons / no dead routes / no console errors / no fake data" are verified
by automation (`scripts/cc_contract_check.py`, contract tests, Playwright), not by
assertion. Add tests for every new route and every new control.

---

## 9. ROLLBACK & PRESERVATION

- Preserve the pre-change file for every meaningful edit (established `.bak-<ts>`
  pattern); keep DB/config backups; prefer reversible migrations.
- Never delete a subsystem without explicit authorization (v1 §102).
- C already maintains a pre-maintenance root snapshot; keep that discipline for
  PVE-side changes.

---

## 10. ACCEPTANCE (v1 §105 — retained)

All items in v1 Section 105 apply. Additionally, v2 requires:

```text
[ ] Phase 0 inventory complete, with corrections where v1 guessed wrong
[ ] UPS protocol gate passed with evidence BEFORE Phase P
[ ] Every new subsystem routed through Command/Event bus + AgentGuard + audit
[ ] Canonical smart-home registry distinct from the mobile device registry
[ ] Every displayed metric traceable to a real source (or shown UNKNOWN)
[ ] Contract check + Playwright + per-route tests green
[ ] Served-bytes match the edited source (deploy verified)
```

---

## 11. FINAL VISION (v1 §107 — unchanged)

One Command Center where the operator instantly sees what KAI, the home, the
infrastructure, the network, the UPS/power, and the workforce are doing; what is
broken; what KAI is recovering; what needs a decision; why something happened;
what KAI intends next; and whether an action actually succeeded — controlling the
environment from the same interface without parallel systems.

**One unified KAI operating/control center.** Build nothing that competes with it.
