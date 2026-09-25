# KAI 2.0 — Smart Home Fabric: Design Spec

**Date:** 2026-09-25
**Status:** Approved design (pre-plan)
**Directive:** `directives/20260925T134345Z-command-center-v2.md` Phase E–F (Smart Home)
**Decision:** Home Assistant is the device fabric; **KAI remains the intelligence and
authority.** HA is NOT the authoritative controller; KAI drives and audits it.

---

## 1. Problem

KAI has no Smart Home capability. The directive requires KAI to monitor and
control rooms, devices, automations, cameras, sensors and power — as part of the
*one* Command Center, without creating a second assistant/brain/registry.

**Verified facts that shape the design:**
- No Home Assistant (or any smart-home host) exists on the LAN today.
- A live **Tuya/Smart Life** device exists at `192.168.1.16`
  (MAC `50:8A:06:6A:3C:36`, Tuya Smart OUI, TCP **6668** open, broadcasting on
  UDP **6667**). So discovery + local control are demonstrably possible.
- `core/device_registry.py` is the **mobile-device** registry — NOT a smart-home
  registry. It must not be overloaded.
- KAI already has: Command Bus (AgentGuard+audit), Event Bus, World Model,
  Vault, AgentGuard, audit, `/api/*` FastAPI on CT111.

## 2. Architecture

```
                    KAI COMMAND CENTER (one UI)
                              │
                    Command / Event Bus (existing)
                              │
                    ┌─────────┴─────────┐
                    │   KAI SmartHome    │  (new, in KAI — not beside it)
                    │   Service         │
                    └─────────┬─────────┘
      ┌───────────┬───────────┼───────────┬────────────┐
      ▼           ▼           ▼           ▼            ▼
  HA adapter  Tuya adapter  MQTT      RTSP/cams   (future: eWeLink,
  (REST+WS)   (local+cloud) adapter   (V380 etc.)  Alexa, TTLock, eero)
      │           │           │           │
      └───────────┴───────────┴───────────┘
                      │
        Canonical SmartHome Device Registry (new)
                      │
                 World Model entities (rooms, devices, links)
                      │
        Event Bus (state) + Command Bus (control → AgentGuard → audit)
```

**Home Assistant deployment (fabric provider):**
- New LXC (VMID 115, `kai-homeassistant`) on Proxmox B, Debian 12 + HA Container
  (docker) *or* HA OS VM — decision recorded in plan; resources: 2 vCPU / 2–4 GB.
- HA in **local network** mode; KAI talks to HA via the **REST + WebSocket API**
  using a long-lived token stored in **Vault** (never env, never frontend).
- HA never holds authority: KAI issues intents, HA executes/observes, KAI reads
  back and audits. HA's own automations are allowed but KAI's registry is canonical.

## 3. Components (each one responsibility, testable in isolation)

1. `core/smarthome/registry.py` — canonical device registry (JSON + flock, schema
   versioned), keyed by a stable KAI device id; stores provider bindings, room,
   capabilities, last-seen. Distinct from `core/device_registry.py`.
2. `core/smarthome/discovery.py` — 3-layer discovery:
   - passive: mDNS 5353 / SSDP 1900 / Tuya 6666-6667 capture;
   - active: LAN port fingerprint (8123 HA, 6668 Tuya, 1883 MQTT, 554 RTSP) +
     OUI vendor lookup;
   - provider: query adapters for identity/state.
3. `core/smarthome/adapters/` — `base.py` (interface), `homeassistant.py`,
   `tuya.py` (local first, cloud fallback), `mqtt.py`, `camera.py`.
4. `core/smarthome/rooms.py` — room mapping; devices without a room render
   "Unassigned".
5. `core/smarthome/service.py` — command execution via Command Bus; state
   subscription via Event Bus; read-back verification; audit.
6. `core/smarthome/routes.py` — CC API under `/api/smarthome/*`, operator-gated.
7. CC panel `smarthome` + sub-panels: Home, Rooms, Devices, Automations, Cameras,
   Sensors, Energy — wired per `kai-design-system` rules.
8. `scripts/smarthome_discover.py` — one-shot full discovery, writes the registry
   and emits a report; the "discover all smart devices" deliverable.

## 4. Data flow

- **Discovery:** LAN → adapters → registry (upsert) → World Model sync → Event Bus.
- **Read:** CC → `/api/smarthome/*` → registry (live state from adapter cache).
- **Control:** CC/NL → Command Bus → AgentGuard (risk) → adapter action →
  read-back → verify → audit event. Sensitive ops (locks, power, cameras) require
  step-up per directive §37.

## 5. Safety / no-fabrication

- A field is shown only if the provider returns it; otherwise `UNKNOWN`.
- Stale state (older than a TTL) is labeled **STALE**, never shown as live.
- Locks/cameras/power are "sensitive": Duo step-up + AgentGuard + audit + read-back.
- Discovery is read-only; it never joins/commissions a device automatically.

## 6. Testing

- Unit: registry CRUD/atomicity, OUI mapping, discovery parsers (recorded
  mDNS/SSDP/Tuya packets), adapter interface contract (fake provider).
- Integration: registry ↔ World Model sync; Command Bus → adapter → audit.
- E2E/visual: Playwright panel checks at 360/768/1280, zero console errors.
- Live proof: discover the known Tuya device at `192.168.1.16`; HA halves if the
  HA container is up.

## 7. Scope decisions (locked)

- **HA = fabric** (user decision). KAI = authority.
- Discovery/protocol work stays **inside KAI**; only device breadth is delegated
  to HA.
- No duplication: Command Bus, Event Bus, World Model, Vault, AgentGuard, audit
  all reused. Only the registry + adapters + rooms are new.
- Phase E ships first (foundation + one provider end-to-end), then F (rooms/
  devices/automations/cameras), then G (voice/knowledge), then P (UPS, gated).

## 8. Open questions to resolve during planning

- HA deployment form: HA Container (docker, lighter) vs HA OS VM (broader
  add-ons). Default: HA Container on LXC 115.
- Tuya control path: local protocol (may need the device's local key via the
  Smart Life cloud account once) vs cloud API only. Default: cloud account →
  retrieve local keys → local control thereafter.
- Camera protocol (V380/eero) specifics — deferred to discovery results.
