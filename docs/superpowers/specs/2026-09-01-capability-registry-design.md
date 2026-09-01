# Capability Registry Design

**Date:** 2026-09-01
**Status:** Approved
**Phase:** KAI 2.0 Phase 6 (Service Fabric)

---

## 1. Architecture

Federated model — Capability Registry is a thin layer over the Service Registry.

- **Service Registry** (`core/service_registry.py`) is authoritative for services, health, ownership
- **Capability Registry** (`core/capability_registry.py`) manages: capability definitions, implementation → service mapping, permissions, required identity, consumer tracking, capability-level health aggregation

```
Service Registry (47 live services)
    ↓ services provide capabilities
Capability Registry (capability definitions)
    ↓ capabilities track implementation services
    ↓ tracks consumers (auto from service deps + manual override)
    ↓ aggregates health: primary/secondary logic
```

---

## 2. Schema

Each capability record:

```json
{
  "capability_id": "telegram-bots",
  "name": "Telegram Bots",
  "canonical_owner": "ai-orchestrator",
  "priority": "P0",
  "status": "degraded",
  "version": "1.0",
  "description": "...",

  "implementations": [
    {
      "service_id": "service-kai-telegram",
      "role": "primary",
      "health": "healthy",
      "auto_detected": true,
      "override": false
    }
  ],

  "permissions_required": ["telegram.use"],
  "required_identity": "service:telegram-bot",
  "data_source": "telegram_messages",
  "depends_on": ["notifications", "ai-orchestrator"],
  "consumed_by": ["automation"],
  "consumed_by_override": [],

  "health_history": []
}
```

### Field Definitions

| Field | Type | Description |
|-------|------|-------------|
| `capability_id` | string | Unique identifier (kebab-case) |
| `name` | string | Human-readable name |
| `canonical_owner` | string | Team/system responsible |
| `priority` | string | P0/P1/P2 |
| `status` | string | Computed: healthy/degraded/down |
| `version` | string | Semantic version |
| `description` | string | What the capability does |
| `implementations` | list | Services providing this capability |
| `permissions_required` | list | Capability-level permissions needed |
| `required_identity` | string | Identity type needed to use |
| `data_source` | string | What data this consumes/produces |
| `depends_on` | list | Capability IDs this depends on (auto + manual) |
| `consumed_by` | list | Capability IDs that depend on this (auto + manual) |
| `consumed_by_override` | list | Manual additions/removals for consumed_by |
| `health_history` | list | Last N aggregated health events |

### Implementation Entry

| Field | Type | Description |
|-------|------|-------------|
| `service_id` | string | Reference to Service Registry entry |
| `role` | string | primary \| secondary |
| `health` | string | From Service Registry health check |
| `auto_detected` | bool | True if auto-linked |
| `override` | bool | True if manually added (skips auto-detection) |

---

## 3. Health Aggregation (primary/secondary)

```
capability_status =
  if any PRIMARY implementation is healthy → "healthy"
  elif any SECONDARY implementation is healthy → "degraded"
  else → "down"
```

---

## 4. Auto-Discovery Rules (priority order)

When a new service registers or `POST /kai/capabilities/discover` runs:

1. **Explicit mapping file** (`/project/uploads/kai-capability-mapping.json`) — admin-defined `service_id → capability_id` overrides everything
2. **Service name pattern** — `telegram-bots` in name → `telegram-bots` capability; `legal-brain` → `legal-brain`; etc.
3. **Port number** — port 8094 → `notifications`; port 8120 → `secret-management`; port 8443 → `telegram-bots`
4. **Health endpoint content** — `GET /health` returns `{"type": "telegram-bot"}` → `telegram-bots`
5. **Fallback** — unmatched services → capability `unknown`

Manual registrations always set `override: true` and skip auto-detection.

### Consumer Auto-Detection

```
For each capability A:
  For each capability B:
    For each service S in B.implementations:
      For each dep in S.depends_on:
        if dep.service_id in A.implementations:
          add A to B.consumed_by (deduplicated)
```

Manual overrides in `consumed_by_override` applied after auto-detection.

---

## 5. API Routes

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/kai/capabilities` | List all capabilities (filter: status/priority/owner) |
| GET | `/kai/capabilities/{cap_id}` | Detail with implementations + health history |
| GET | `/kai/capabilities/{cap_id}/health` | Trigger live health check |
| POST | `/kai/capabilities` | Register new capability (manual) |
| PUT | `/kai/capabilities/{cap_id}` | Update capability metadata |
| DELETE | `/kai/capabilities/{cap_id}` | Deregister |
| POST | `/kai/capabilities/{cap_id}/implementations` | Link a service as implementation |
| DELETE | `/kai/capabilities/{cap_id}/implementations/{service_id}` | Unlink |
| POST | `/kai/capabilities/discover` | Re-run auto-discovery |
| GET | `/kai/capabilities/{cap_id}/dependents` | Who depends on this capability |

All writes gated on `capabilities.manage` capability (Phase 15A).

---

## 6. Persistence

- `memory/kai_capabilities.json` — atomic write (temp file + rename), `.bak` backup
- `memory/kai_capabilities_health_history.json` — rolling 100-event health log
- `/project/uploads/kai-capability-mapping.json` — explicit admin mapping (not gitignored)

---

## 7. Startup Behavior

On API startup:
1. Load capabilities from JSON
2. Seed from explicit mapping (`kai-capability-mapping.json`)
3. Auto-detect remaining unlinked services
4. Start background health loop (60s interval, same as Service Registry)

---

## 8. Files

- `core/capability_registry.py` — CapabilityRegistry class
- `core/capability_registry_routes.py` — FastAPI routes
- `core/capability_registry_discovery.py` — Auto-discovery logic (name patterns, ports, health endpoint)
- `/project/uploads/kai-capability-mapping.json` — Admin explicit mapping
- `tests/test_capability_registry_routes.py` — API route tests
- `tests/test_capability_registry_discovery.py` — Auto-discovery tests
- `tests/test_capability_registry_health.py` — Health aggregation tests
