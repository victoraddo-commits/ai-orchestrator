# KAI Core — Event Bus Design

## Spec Version
- **Written**: 2026-09-01
- **Status**: Approved

---

## 1. Problem Statement

The KAI ecosystem has many modules that produce events (health changes, approvals, builds, audit entries, remediation triggers) and many that need to react to them (notifications, audit logging, Telegram bridges, dashboards). Today these connections are hardcoded: `notifications.py` is called directly by specific callers, audit logging is called from API middleware, and there is no way for a new module to subscribe to events without modifying the publishing module.

KAI Core Phase 7 introduces a central event bus that decouples event producers from consumers through a pub/sub system with topic-pattern matching and optional persistence.

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     EVENT BUS                           │
│  core/kai_event_bus.py — singleton, thread-safe         │
│                                                           │
│  publish(topic, payload, severity, journal)               │
│  subscribe(topic_pattern, handler) → sub_id               │
│  unsubscribe(sub_id)                                     │
│  replay_journal() — on startup, catch up missed events  │
└───────────────┬─────────────────────────────────────────┘
                │ fnmatch pattern matching
                │ topic: "capability.health.telegram-bots"
                │         "audit.critical.approval"
                │         "service.down.*"
                ▼
┌─────────────────────────────────────────────────────────┐
│  Registered Subscribers:                                │
│  • notifications.py      → "service.*", "capability.*"  │
│  • audit_logger.py      → "audit.critical.*"            │
│  • capability_registry  → "service.health.*"             │
│  • approval_manager     → "approval.*"                   │
│  • kai_telegram_bridge → "build.*", "remediation.*"     │
└─────────────────────────────────────────────────────────┘
```

**Key design decisions:**
- **Singleton pattern**: one `KAIEventBus` instance, accessed via `event_bus` import. Matches the `ServiceRegistry` / `CapabilityRegistry` pattern already in use.
- **Thread-safe**: all subscriber dict access guarded by `threading.RLock`.
- **Topic patterns**: `fnmatch`-style glob patterns (`*` matches any segment, `?` matches single char). E.g., `capability.*` matches `capability.health` and `capability.updated`; `service.down.*` matches nothing (no sub-topics under `service.down`).
- **No in-process event delivery guarantee**: handlers are invoked synchronously in the publishing thread. Slow handlers should hand off to a background thread internally.
- **Fan-out**: one published event reaches all matching subscribers.

---

## 3. Event Envelope

Every event has the following envelope:

```python
{
    "id": "uuid4-string",          # unique per event
    "topic": "capability.health.telegram-bots",  # dotted hier
    "payload": {...},              # event-specific data (dict)
    "source": "capability_registry",  # publishing module name
    "timestamp": 1725148800.0,      # unix epoch float (UTC)
    "severity": "important",       # critical | important | informational
    "journal": True,               # whether written to journal
}
```

### Severity levels

| Level | Value | Use case |
|-------|-------|----------|
| `critical` | 0 | Service down, security event, recovery triggered, budget exceeded |
| `important` | 1 | Health transitions, approvals requested/decided, build state changes |
| `informational` | 2 | Kai cycle completed, configuration updated, informational FYIs |

### Severity constants

```python
CRITICAL     = "critical"
IMPORTANT    = "important"
INFORMATIONAL = "informational"
SEVERITY_ORDER = {CRITICAL: 0, IMPORTANT: 1, INFORMATIONAL: 2}
```

---

## 4. Topic Hierarchy

All topics are dot-separated lowercase strings. Unknown topics are allowed — no central registry needed.

| Topic prefix | Examples | Default severity | Journal |
|---|---|---|---|
| `capability.*` | `capability.health.<id>`, `capability.updated.<id>` | important | yes |
| `service.*` | `service.down.<id>`, `service.recovered.<id>`, `service.health.<id>` | critical (down) / important (recovered/health) | yes |
| `approval.*` | `approval.requested.<id>`, `approval.decided.<id>`, `approval.rejected.<id>` | important | yes |
| `audit.*` | `audit.critical.<source>`, `audit.warning.<source>` | critical | yes |
| `build.*` | `build.started.<id>`, `build.failed.<id>`, `build.deployed.<id>` | important | no |
| `remediation.*` | `remediation.triggered.<id>`, `remediation.completed.<id>`, `remediation.failed.<id>` | important | yes |
| `kai.*` | `kai.started`, `kai.cycle_completed.<id>`, `kai.error.<type>` | informational | no |
| `config.*` | `config.updated.<key>` | important | yes |
| `recovery.*` | `recovery.started.<id>`, `recovery.failed.<id>` | critical | yes |
| `health.*` | `health.liveness`, `health.readiness` | informational | no |

**Journal defaults**: events with `journal=True` are appended to the event journal. Events with `journal=False` are delivered to subscribers but not persisted. The `journal` parameter on `publish()` overrides the default.

---

## 5. API

### `core/kai_event_bus.py`

```python
class KAIEventBus:
    """Thread-safe pub/sub event bus with optional journal persistence."""

    def subscribe(
        self,
        topic_pattern: str,
        handler: Callable[[str, dict], None],
        sources: set[str] | None = None,
    ) -> str:
        """Register a handler for topic_pattern (fnmatch glob).
        
        Returns a subscription ID (UUID string) for later unsubscribe.
        
        If sources is set, only events from those source modules match.
        """

    def unsubscribe(self, sub_id: str) -> bool:
        """Remove a subscription by ID. Returns True if found."""

    def publish(
        self,
        topic: str,
        payload: dict,
        source: str = "unknown",
        severity: str = INFORMATIONAL,
        journal: bool | None = None,
    ) -> int:
        """Publish an event to all matching subscribers.
        
        Returns number of subscribers that received the event (fan-out count).
        
        journal defaults: CRITICAL and remediation.*, recovery.*, service.down.*
        events are journaled unless journal=False is passed explicitly.
        """

    def replay_journal(self) -> int:
        """Re-play the journal on startup. Returns number of events replayed.
        
        Called automatically by start(). Each subscriber processes missed
        events that match its pattern. Events without matching subscribers
        are skipped.
        """

    def start(self) -> None:
        """Initialize journal. Call once at application startup."""

    def get_stats(self) -> dict:
        """Return bus statistics: subscriber count, journal size, etc."""
```

**Global singleton accessor:**

```python
event_bus: KAIEventBus = KAIEventBus.get_instance()
```

Convenience functions (module-level):

```python
def subscribe(
    topic_pattern: str,
    handler: Callable[[str, dict], None],
    sources: set[str] | None = None,
) -> str:
    return event_bus.subscribe(topic_pattern, handler, sources)

def publish(
    topic: str,
    payload: dict,
    source: str = "unknown",
    severity: str = INFORMATIONAL,
    journal: bool | None = None,
) -> int:
    return event_bus.publish(topic, payload, source, severity, journal)
```

---

## 6. Journal Persistence

**File**: `memory/kai_event_journal.jsonl`

- Append-only JSON Lines format (one event envelope per line)
- Rotated when file exceeds 10 MB — rotated file named `kai_event_journal.jsonl.1`, `kai_event_journal.jsonl.2` (max 3 rotations kept)
- Written synchronously on publish for journal=True events (using `os.replace` for atomic append, or accumulated buffer flushed every 1s)
- **Simplified approach**: buffer writes in a queue, flush to disk every 1 second or on 100 events. On startup, replay reads all journal files in order.

**Replay on startup:**
1. Read all journal files in order (`.1`, `.2`, main) — each line parsed as a JSON event envelope
2. For each event, call `publish()` internally with `replay=True` so subscribers can distinguish replay from live events
3. Subscribers that process replayed events update their in-memory state but do NOT re-trigger side effects (e.g., notifications.py should not send Telegram on replayed `service.down`)

---

## 7. Integration with Existing Modules

### `notifications.py` — becomes a subscriber

Before (hardcoded callers):
```python
# In various callers:
notification_manager.enqueue("service.down", title, message, CRITICAL)
```

After (event-driven):
```python
# notifications.py on init:
event_bus.subscribe("service.down.*", _handle_service_event, sources={"service_registry"})
event_bus.subscribe("capability.health.*", _handle_capability_event)
event_bus.subscribe("build.failed", _handle_build_event)
event_bus.subscribe("budget_alert", _handle_budget_event)  # from budget alerts
```

`notifications.py` stops being called directly by other modules. It reacts to events.

### `capability_registry.py` — publishes health transitions

```python
# In refresh_health() when status changes:
event_bus.publish(
    f"capability.health.{cap_id}",
    {
        "capability_id": cap_id,
        "old_status": old_status,
        "new_status": new_status,
        "implementations": [i["service_id"] for i in impls],
    },
    source="capability_registry",
    severity=IMPORTANT,
    journal=True,
)
```

### `audit_logger.py` — subscribes to external audit events

```python
# audit_logger.py already writes its own internal log.
# Subscribe to audit.critical.* to receive structured audit events from
# any module that publishes to the event bus:
event_bus.subscribe(
    "audit.critical.*",
    _handle_external_audit_event,
    sources=None,  # receive from all sources
)
```

### `scheduler.py` (orchestrator cycle) — publishes lifecycle events

```python
# After each cycle:
event_bus.publish(
    "kai.cycle_completed",
    {
        "cycle_id": trace_id,
        "duration_seconds": duration,
        "incidents": incidents_count,
        "decisions": decisions_count,
    },
    source="scheduler",
    severity=INFORMATIONAL,
    journal=False,
)

# On incident:
event_bus.publish(
    "kai.incident",
    {"incident_id": incident_id, "type": incident_type},
    source="scheduler",
    severity=CRITICAL,
    journal=True,
)
```

### `approval_manager.py` — publishes approval events

```python
# On new approval request:
event_bus.publish(
    f"approval.requested.{approval_id}",
    {"approval_id": approval_id, "type": approval_type, "risk": risk_level},
    source="approval_manager",
    severity=IMPORTANT,
    journal=True,
)

# On decision:
event_bus.publish(
    f"approval.decided.{approval_id}",
    {"approval_id": approval_id, "decision": decision, "operator": operator},
    source="approval_manager",
    severity=IMPORTANT,
    journal=True,
)
```

---

## 8. Routing Events to Telegram

The notification manager maps topics + severity to delivery channels:

| Pattern | Severity | Telegram | Heartbeat | History |
|---|---|---|---|---|
| `service.down.*` | CRITICAL | ✅ | ✅ | ✅ |
| `recovery.*` | CRITICAL | ✅ | ✅ | ✅ |
| `capability.health.*` | IMPORTANT | ⚠️ degraded only | ✅ | ✅ |
| `approval.*` | IMPORTANT | ✅ | ✅ | ✅ |
| `build.failed` | IMPORTANT | ✅ | ✅ | ✅ |
| `budget_alert` | CRITICAL | ✅ | ✅ | ✅ |
| `kai.cycle_completed` | INFO | ❌ | ✅ | ✅ |
| `kai.incident` | CRITICAL | ✅ | ✅ | ✅ |

Deduplication: same `(topic, source, title_hash)` within 10 minutes → suppress.

---

## 9. File Structure

```
core/kai_event_bus.py          # KAIEventBus singleton + module-level API
core/kai_event_bus_routes.py   # FastAPI routes: GET /kai/events (history), POST /kai/events/publish (operator only)
core/__init__.py               # imports event_bus
memory/kai_event_journal.jsonl # event journal (gitignored)
tests/test_kai_event_bus.py     # unit + integration tests
```

---

## 10. API Routes

Mounted at `/kai/events` (same prefix as other kai routes):

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/kai/events` | viewer | Query recent events (last 100, filterable by topic pattern, source, severity) |
| GET | `/kai/events/stats` | viewer | Bus statistics (subscriber count, journal size, event counts) |
| POST | `/kai/events/publish` | operator + `kai.command` capability | Publish a custom event (for testing and manual interventions) |

---

## 11. Edge Cases

1. **Slow subscriber handler**: handlers run in the publishing thread. Slow handlers block publish. Solution: handlers that need to do I/O should hand off to a background thread/queue internally. Document this in the module docstring.

2. **Subscriber raises exception**: caught, logged as warning, other subscribers still called. Publish never raises.

3. **Handler subscribes during event delivery**: the new subscription is not called for the in-progress event.

4. **Event published before bus is started**: buffered or dropped. `start()` must be called before any publish. In practice, call `event_bus.start()` in the API lifespan after `capability_registry.start()`.

5. **Journal replay with missing journal file**: skip gracefully. Start fresh.

6. **Topic with no subscribers**: publish returns 0. Journal still written if applicable.

7. **Circular publish**: Module A subscribes to Module B, Module B subscribes to Module A. This is a design smell, not a bug to prevent. Module B's handler should not publish events that would trigger Module A's handler which would call Module B again. Document this concern.

---

## 12. Out of Scope

- **Message queue or broker**: in-process pub/sub only. For cross-host communication, a future phase can add a network transport.
- **Event schema validation**: payloads are free-form dicts. Subscribers and publishers are trusted modules within the same process.
- **Dead letter queue**: failed handler deliveries are logged, not retried.
- **Event replay for consumers**: replay only restores missed events to subscribers. It does not rebuild external state (databases, etc.).
- **Event aggregation**: no batching or windowing of events. Each event is delivered individually.

---

## 13. Dependencies

- Python standard library only (no new packages)
- Uses `core.memory` for journal path resolution
- Compatible with existing `notifications.py`, `audit_logger.py`, `capability_registry.py`

---

## 14. Testing Requirements

### Unit tests (`tests/test_kai_event_bus.py`)

1. **Subscribe/unsubscribe**: subscribe returns sub_id; unsubscribe removes it
2. **Pattern matching**: `fnmatch` correctly matches `*`, `*.health`, `service.down.*`
3. **No match**: topic not matching pattern → no handler called
4. **Multiple subscribers same pattern**: all called on publish
5. **Publish returns fan-out count**: correct subscriber count returned
6. **Journal write**: event with journal=True appears in journal file after publish
7. **Journal rotation**: journal exceeds 10MB → rotated file created
8. **Replay**: after journal write + bus restart, subscribers receive replayed events
9. **Severity enum ordering**: CRITICAL > IMPORTANT > INFORMATIONAL ordering works
10. **Source filter**: handler with `sources={"foo"}` only called for events from "foo"

### Integration tests

1. **notifications.py integration**: publish `service.down.test` → verify notification history entry created
2. **capability_registry integration**: capability health transition → verify event published with correct topic
3. **Full chain**: publish `approval.requested.test` → verify audit log entry created

---

## 15. Acceptance Criteria

- [ ] `KAIEventBus` singleton accessible via `from core.kai_event_bus import event_bus`
- [ ] `subscribe(pattern, handler)` returns sub_id, handler called on matching publish
- [ ] `publish(topic, payload, source, severity)` fans out to all matching subscribers
- [ ] Critical events persisted to `memory/kai_event_journal.jsonl`
- [ ] Journal rotates at 10MB, max 3 rotation files
- [ ] On startup, `replay_journal()` delivers missed events to subscribers
- [ ] `notifications.py` reacts to `service.down.*` and `build.failed` via event bus (no direct caller)
- [ ] `capability_registry.py` publishes `capability.health.<id>` on status transitions
- [ ] API routes: `GET /kai/events`, `GET /kai/events/stats`, `POST /kai/events/publish`
- [ ] All tests pass: unit + integration
- [ ] No new dependencies — standard library only
