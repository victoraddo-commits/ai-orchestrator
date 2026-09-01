# KAI Core — Event Bus Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a central thread-safe pub/sub event bus with topic-pattern matching (fnmatch), journal persistence, and integration with notifications, capability registry, audit logger, and the orchestrator cycle.

**Architecture:** Singleton event bus accessed via `event_bus` import; subscribers register glob patterns (`fnmatch`), publishers emit to dotted topic paths; critical events journaled to `memory/kai_event_journal.jsonl` with 10MB rotation.

**Tech Stack:** Python standard library only (threading, fnmatch, uuid, json, pathlib); FastAPI for routes.

---

## Task 1: KAIEventBus Core

**Files:**
- Create: `core/kai_event_bus.py`
- Modify: `core/__init__.py` (import `event_bus`)

- [ ] **Step 1: Write the failing test — bus instantiation and subscribe/publish**

```python
# tests/test_kai_event_bus.py
def test_subscribe_and_publish():
    bus = KAIEventBus.__new__(KAIEventBus)
    bus._subscribers = {}  # bypass __init__ for isolated test
    received = []

    sub_id = bus.subscribe("capability.*", lambda topic, event: received.append((topic, event)))

    bus.publish("capability.health.test", {"foo": "bar"}, source="test")
    assert len(received) == 1
    assert received[0][0] == "capability.health.test"
    assert received[0][1]["foo"] == "bar"

    bus.unsubscribe(sub_id)
    bus.publish("capability.health.test2", {"baz": "qux"}, source="test")
    assert len(received) == 1  # still 1, unsubscribe worked
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_kai_event_bus.py::test_subscribe_and_publish -v`
Expected: FAIL — `KAIEventBus` not defined

- [ ] **Step 3: Write minimal KAIEventBus skeleton**

```python
# core/kai_event_bus.py
import fnmatch
import threading
import uuid
from typing import Callable

CRITICAL = "critical"
IMPORTANT = "important"
INFORMATIONAL = "informational"

class KAIEventBus:
    _instance = None

    @classmethod
    def get_instance(cls) -> "KAIEventBus":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._subscribers: dict[str, dict] = {}  # sub_id -> {pattern, handler, sources}
        self._lock = threading.RLock()

    def subscribe(self, topic_pattern: str,
                  handler: Callable[[str, dict], None],
                  sources: set[str] | None = None) -> str:
        sub_id = str(uuid.uuid4())
        with self._lock:
            self._subscribers[sub_id] = {
                "pattern": topic_pattern,
                "handler": handler,
                "sources": sources,
            }
        return sub_id

    def unsubscribe(self, sub_id: str) -> bool:
        with self._lock:
            if sub_id in self._subscribers:
                del self._subscribers[sub_id]
                return True
        return False

    def publish(self, topic: str, payload: dict,
                source: str = "unknown",
                severity: str = INFORMATIONAL,
                journal: bool | None = None) -> int:
        envelope = {
            "id": str(uuid.uuid4()),
            "topic": topic,
            "payload": payload,
            "source": source,
            "severity": severity,
            "journal": journal,
        }
        count = 0
        with self._lock:
            for sub in self._subscribers.values():
                if fnmatch.fnmatch(topic, sub["pattern"]):
                    if sub["sources"] is not None and source not in sub["sources"]:
                        continue
                    try:
                        sub["handler"](topic, envelope)
                        count += 1
                    except Exception:
                        pass
        return count

# Module-level singleton accessor
event_bus = KAIEventBus.get_instance()

# Convenience functions
def subscribe(topic_pattern, handler, sources=None):
    return event_bus.subscribe(topic_pattern, handler, sources)

def publish(topic, payload, source="unknown", severity=INFORMATIONAL, journal=None):
    return event_bus.publish(topic, payload, source, severity, journal)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_kai_event_bus.py::test_subscribe_and_publish -v`
Expected: PASS

- [ ] **Step 5: Write tests for pattern matching and multiple subscribers**

```python
def test_pattern_matching():
    bus = KAIEventBus.__new__(KAIEventBus)
    bus._subscribers = {}
    called = {"a": [], "b": [], "c": []}

    bus.subscribe("capability.*", lambda t, e: called["a"].append(t))
    bus.subscribe("capability.health.*", lambda t, e: called["b"].append(t))
    bus.subscribe("service.down.*", lambda t, e: called["c"].append(t))

    bus.publish("capability.health.telegram", {}, source="src")
    assert called["a"] == ["capability.health.telegram"]
    assert called["b"] == ["capability.health.telegram"]
    assert called["c"] == []

def test_multiple_subscribers_same_pattern():
    bus = KAIEventBus.__new__(KAIEventBus)
    bus._subscribers = {}
    results = []

    bus.subscribe("build.*", lambda t, e: results.append(1))
    bus.subscribe("build.*", lambda t, e: results.append(2))
    bus.publish("build.started", {}, source="test")
    assert sorted(results) == [1, 2]

def test_source_filter():
    bus = KAIEventBus.__new__(KAIEventBus)
    bus._subscribers = {}
    called = []

    bus.subscribe("audit.*", lambda t, e: called.append(e["source"]), sources={"foo", "bar"})
    bus.publish("audit.critical", {}, source="foo")
    bus.publish("audit.critical", {}, source="baz")
    assert called == ["foo"]

def test_unsubscribe_unknown_id():
    bus = KAIEventBus.__new__(KAIEventBus)
    bus._subscribers = {}
    assert bus.unsubscribe("unknown-id") is False
```

- [ ] **Step 6: Run these tests**

Run: `pytest tests/test_kai_event_bus.py -v -k "pattern or multiple or source or unsubscribe"`
Expected: PASS

- [ ] **Step 7: Add journal persistence to publish()**

First update the test:
```python
def test_journal_write(tmp_path, monkeypatch):
    # Patch journal path to tmp_path
    monkeypatch.setattr("core.kai_event_bus.JOURNAL_DIR", str(tmp_path))
    bus = KAIEventBus()
    bus.start()
    called = []
    bus.subscribe("service.down.*", lambda t, e: called.append(e))
    bus.publish("service.down.test-svc", {"svc": "test"}, source="test_src",
                severity=CRITICAL, journal=True)
    assert len(called) == 1
    # Check journal file
    journal_file = tmp_path / "kai_event_journal.jsonl"
    assert journal_file.exists()
    entries = [json.loads(l) for l in journal_file.read().splitlines() if l.strip()]
    assert any(e["topic"] == "service.down.test-svc" for e in entries)
```

- [ ] **Step 8: Implement journal write with atomic os.replace**

```python
import os
import json as _json

JOURNAL_DIR = Path(__file__).parent.parent / "memory"
JOURNAL_FILE = "kai_event_journal.jsonl"
JOURNAL_MAX_SIZE = 10 * 1024 * 1024  # 10 MB
JOURNAL_MAX_ROTATIONS = 3
_JOURNAL_BUFFER: list[dict] = []
_BUFFER_LOCK = threading.Lock()
_BUFFER_SIZE = 100
_BUFFER_FLUSH_INTERVAL = 1.0  # seconds

def start(self):
    self._journal_dir.mkdir(parents=True, exist_ok=True)

def _journal_write(self, envelope: dict):
    global _JOURNAL_BUFFER
    journal_path = self._journal_dir / JOURNAL_FILE
    with _BUFFER_LOCK:
        _JOURNAL_BUFFER.append(envelope)
        if len(_JOURNAL_BUFFER) >= _BUFFER_SIZE:
            self._flush_journal()

def _flush_journal(self):
    global _JOURNAL_BUFFER
    with _BUFFER_LOCK:
        if not _JOURNAL_BUFFER:
            return
        entries = _JOURNAL_BUFFER
        _JOURNAL_BUFFER = []
    journal_path = self._journal_dir / JOURNAL_FILE
    if journal_path.exists() and journal_path.stat().st_size >= JOURNAL_MAX_SIZE:
        self._rotate_journal()
    tmp = journal_path.with_suffix(".tmp")
    with open(tmp, "a") as fh:
        for entry in entries:
            fh.write(_json.dumps(entry) + "\n")
    os.replace(tmp, journal_path)

def _rotate_journal(self):
    for i in range(JOURNAL_MAX_ROTATIONS, 0, -1):
        src = self._journal_dir / f"{JOURNAL_FILE}.{i}"
        dst = self._journal_dir / f"{JOURNAL_FILE}.{i+1}"
        if dst.exists():
            dst.unlink()
        if src.exists():
            src.rename(dst)
    src = self._journal_dir / JOURNAL_FILE
    dst = self._journal_dir / f"{JOURNAL_FILE}.1"
    if dst.exists():
        dst.unlink()
    if src.exists():
        src.rename(dst)

# In publish(), after building envelope:
if journal is None:
    journal = envelope["severity"] == CRITICAL or \
              envelope["topic"].startswith("remediation.") or \
              envelope["topic"].startswith("recovery.") or \
              envelope["topic"] == "service.down"
if journal:
    self._journal_write(envelope)
```

- [ ] **Step 9: Implement replay_journal()**

```python
def replay_journal(self) -> int:
    """Re-play journal events on startup. Returns count of events replayed."""
    count = 0
    journal_path = self._journal_dir / JOURNAL_FILE
    rotation_files = sorted([
        self._journal_dir / f"{JOURNAL_FILE}.{i}"
        for i in range(1, JOURNAL_MAX_ROTATIONS + 1)
    ])
    all_journals = [journal_path] + rotation_files
    for jpath in all_journals:
        if not jpath.exists():
            continue
        for line in jpath.read().splitlines():
            if not line.strip():
                continue
            try:
                event = _json.loads(line)
                self.publish(
                    event["topic"],
                    event["payload"],
                    source=event["source"],
                    severity=event["severity"],
                    journal=False,
                    replay=True,
                )
                count += 1
            except Exception:
                pass
    return count

# Add replay=True to publish signature and pass through envelope
def publish(self, ..., replay: bool = False) -> int:
    envelope["replay"] = replay
    ...
```

- [ ] **Step 10: Run all tests**

Run: `pytest tests/test_kai_event_bus.py -v`
Expected: PASS (all tests including journal tests)

- [ ] **Step 11: Commit**

```bash
git add core/kai_event_bus.py core/__init__.py tests/test_kai_event_bus.py
git commit -m "feat(kai-event-bus): KAIEventBus core — pub/sub, fnmatch patterns, journal"
```

---

## Task 2: FastAPI Routes

**Files:**
- Create: `core/kai_event_bus_routes.py`
- Modify: `core/api.py` (register router)

- [ ] **Step 1: Write failing test for route GET /kai/events**

```python
def test_get_events_requires_auth(client, authenticated_headers):
    resp = client.get("/kai/events", headers=authenticated_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "events" in data
    assert isinstance(data["events"], list)

def test_get_events_filter_by_topic(client, authenticated_headers):
    # Seed some events via bus directly first...
    event_bus.publish("test.topic.a", {"n": 1}, source="test")
    resp = client.get("/kai/events?topic_pattern=test.topic.*", headers=authenticated_headers)
    assert resp.status_code == 200
    assert all("test.topic" in e["topic"] for e in resp.json()["events"])
```

- [ ] **Step 2: Write minimal route handler (no auth first to isolate)**
```python
# core/kai_event_bus_routes.py
from fastapi import APIRouter, Query
from typing import Optional
from core.kai_event_bus import event_bus

router = APIRouter(prefix="/kai/events", tags=["kai", "events"])

@router.get("")
def get_events(
    topic_pattern: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
):
    """Query recent events from the in-memory event store."""
    # Collect recent events (need to store last N events in memory too)
    events = list(event_bus._recent_events)[-limit:]
    if topic_pattern:
        import fnmatch
        events = [e for e in events if fnmatch.fnmatch(e["topic"], topic_pattern)]
    if source:
        events = [e for e in events if e["source"] == source]
    if severity:
        events = [e for e in events if e["severity"] == severity]
    return {"events": events, "count": len(events)}

@router.get("/stats")
def get_event_stats():
    """Return bus statistics."""
    return event_bus.get_stats()

@router.post("/publish")
def publish_event(
    topic: str,
    payload: dict,
    source: str = "api",
    severity: str = "informational",
):
    """Publish a custom event (operator capability required)."""
    from core.authz import check_capability
    # check_capability injected via dependency
    count = event_bus.publish(topic, payload, source, severity)
    return {"published": count, "topic": topic}
```

- [ ] **Step 3: Add recent events store to KAIEventBus**

```python
# In __init__:
self._recent_events: list[dict] = []  # last 1000 events
self._MAX_RECENT = 1000

# In publish(), after matching subscribers:
self._recent_events.append(envelope)
if len(self._recent_events) > self._MAX_RECENT:
    self._recent_events = self._recent_events[-self._MAX_RECENT:]

# get_stats():
def get_stats(self) -> dict:
    with self._lock:
        return {
            "subscriber_count": len(self._subscribers),
            "recent_event_count": len(self._recent_events),
            "journal_size_bytes": (self._journal_dir / JOURNAL_FILE).stat().st_size
                                 if (self._journal_dir / JOURNAL_FILE).exists() else 0,
        }
```

- [ ] **Step 4: Wire routes into api.py**

```python
# In core/api.py
from core.kai_event_bus_routes import router as event_bus_router

app.include_router(event_bus_router)

# In lifespan:
from core.kai_event_bus import event_bus
event_bus.start()
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_kai_event_bus.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add core/kai_event_bus_routes.py core/api.py core/__init__.py tests/test_kai_event_bus.py
git commit -m "feat(kai-event-bus): FastAPI routes + in-memory event history"
```

---

## Task 3: Integrate notifications.py as Subscriber

**Files:**
- Modify: `core/notifications.py`

- [ ] **Step 1: Write failing test**

```python
def test_notifications_receives_service_down_event(tmp_path, monkeypatch):
    """notifications.py should react to service.down.* via event bus."""
    # Setup temp memory dir
    monkeypatch.setattr("core.notifications.MEMORY_DIR", str(tmp_path))
    from core.notifications import NotificationManager
    nm = NotificationManager()
    # Publish a service.down event
    from core.kai_event_bus import event_bus
    bus = KAIEventBus()  # fresh instance
    bus.subscribe("service.down.*", nm._handle_service_event)
    bus.publish("service.down.test-svc", {"service_id": "test-svc"}, source="test_src")
    # Check notification was created
    history = nm.get_history(limit=10)
    assert any("test-svc" in str(h) for h in history)
```

- [ ] **Step 2: Add event bus handler to notifications.py**

In `NotificationManager.__init__` or a new `register_event_subscriptions()` method:

```python
def register_event_subscriptions(self):
    """Subscribe to event bus topics. Call once after bus.start()."""
    from core.kai_event_bus import event_bus
    event_bus.subscribe("service.down.*", self._handle_service_event)
    event_bus.subscribe("capability.health.*", self._handle_capability_event)
    event_bus.subscribe("build.failed", self._handle_build_event)
    event_bus.subscribe("remediation.*", self._handle_remediation_event)

def _handle_service_event(self, topic: str, envelope: dict):
    payload = envelope["payload"]
    svc_id = payload.get("service_id", topic.split(".")[-1])
    self.enqueue(
        source="event_bus",
        title=f"Service down: {svc_id}",
        message=payload.get("message", f"Service {svc_id} is down"),
        severity=CRITICAL,
    )

def _handle_capability_event(self, topic: str, envelope: dict):
    payload = envelope["payload"]
    cap_id = payload.get("capability_id", "unknown")
    new_status = payload.get("new_status", "unknown")
    if new_status == "degraded":
        self.enqueue(
            source="event_bus",
            title=f"Capability degraded: {cap_id}",
            message=f"{cap_id} is degraded",
            severity=IMPORTANT,
        )

def _handle_build_event(self, topic: str, envelope: dict):
    payload = envelope["payload"]
    self.enqueue(
        source="event_bus",
        title=f"Build failed: {payload.get('build_id', 'unknown')}",
        message=payload.get("error", "Build failed"),
        severity=IMPORTANT,
    )

def _handle_remediation_event(self, topic: str, envelope: dict):
    payload = envelope["payload"]
    self.enqueue(
        source="event_bus",
        title=f"Remediation: {payload.get('remediation_id', topic)}",
        message=payload.get("message", str(payload)),
        severity=CRITICAL,
    )
```

- [ ] **Step 3: Register subscriptions in API lifespan (after bus.start())**

```python
# In api.py lifespan, after event_bus.start():
from core.notifications import NotificationManager
nm = NotificationManager.get_instance()
nm.register_event_subscriptions()
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_kai_event_bus.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/notifications.py core/api.py
git commit -m "feat(kai-event-bus): notifications.py subscribes to event bus"
```

---

## Task 4: Integrate capability_registry.py and audit_logger.py

**Files:**
- Modify: `core/capability_registry.py`
- Modify: `core/audit_logger.py`

- [ ] **Step 1: Write failing test for capability_registry publishing**

```python
def test_capability_registry_publishes_health_event(tmp_path, monkeypatch):
    """capability_registry publishes to event bus on status transition."""
    # Patch memory dir and event bus
    from core.capability_registry import CapabilityRegistry
    from core import kai_event_bus

    received = []
    orig_publish = kai_event_bus.event_bus.publish

    def mock_publish(topic, payload, **kw):
        received.append((topic, payload))
        return orig_publish(topic, payload, **kw)

    monkeypatch.setattr(kai_event_bus.event_bus, "publish", mock_publish)
    # ... create cap reg, trigger health transition, assert event published
```

- [ ] **Step 2: Modify capability_registry.py — publish on health transition**

In `refresh_health()` after computing `new_status` and calling `_record_health_event()`:

```python
# After: cap["status"] = new_status
from core.kai_event_bus import event_bus
severity = "critical" if new_status == "down" else "important"
event_bus.publish(
    f"capability.health.{cap_id}",
    {
        "capability_id": cap_id,
        "old_status": old_status,
        "new_status": new_status,
        "implementations": [
            {"service_id": i["service_id"], "health": i.get("health", "unknown")}
            for i in cap.get("implementations", [])
        ],
    },
    source="capability_registry",
    severity=severity,
    journal=True,
)
```

- [ ] **Step 3: Modify audit_logger.py — subscribe to audit.critical.* events**

Add to `audit_logger.py`:

```python
def _register_event_bus_subscription():
    """Subscribe to external audit events from the event bus."""
    from core.kai_event_bus import event_bus
    event_bus.subscribe("audit.critical.*", _handle_external_audit_event)

def _handle_external_audit_event(topic: str, envelope: dict):
    """Handle audit events published by other modules to the event bus."""
    payload = envelope["payload"]
    # Write to audit log with the external event's data
    _write_audit_entry({
        "event": "external_audit",
        "topic": topic,
        "source": envelope["source"],
        "operator": payload.get("operator", "unknown"),
        "action": payload.get("action", "unknown"),
        "resource": payload.get("resource", ""),
        "result": payload.get("result", "unknown"),
        "ip": payload.get("ip", "unknown"),
        "details": payload.get("details", {}),
        "trace_id": payload.get("trace_id", ""),
        "timestamp": envelope["timestamp"],
    })
```

- [ ] **Step 4: Call _register_event_bus_subscription() at end of audit_logger init**

```python
# At end of audit_logger module (after functions defined):
def init_event_bus_subscription():
    try:
        _register_event_bus_subscription()
    except Exception as exc:
        logging.getLogger("kai.audit").warning("Could not register event bus subscription: %s", exc)

# Called from api.py lifespan, after event_bus.start():
# (avoids importing audit_logger at module level)
```

- [ ] **Step 5: Add event bus startup ordering in api.py lifespan**

```python
# Order: service_registry → capability_registry → event_bus → notifications → audit
from core.service_registry import ServiceRegistry
sr = ServiceRegistry.get_instance()
sr.start()
from core.capability_registry import CapabilityRegistry
cr = CapabilityRegistry.get_instance()
cr.start()
from core.kai_event_bus import event_bus
event_bus.start()
from core.notifications import NotificationManager
nm = NotificationManager.get_instance()
nm.register_event_subscriptions()
from core import audit_logger
audit_logger.init_event_bus_subscription()
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_kai_event_bus.py tests/test_capability_registry.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add core/capability_registry.py core/audit_logger.py core/api.py
git commit -m "feat(kai-event-bus): capability_registry + audit_logger integrate with bus"
```

---

## Task 5: Integration Verification

**Files:**
- Run: full test suite
- Verify: API smoke test

- [ ] **Step 1: Run full test suite**

```bash
cd /project/ai-orchestrator
.venv/bin/python -m pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: All tests pass

- [ ] **Step 2: Smoke test the API routes**

```bash
curl -s http://localhost:8000/kai/events | python3 -m json.tool | head -20
curl -s http://localhost:8000/kai/events/stats | python3 -m json.tool
```

Expected: Valid JSON with events list and stats

- [ ] **Step 3: Verify journal file exists and has entries**

```bash
ls -la memory/kai_event_journal.jsonl
wc -l memory/kai_event_journal.jsonl
```

Expected: File exists, has entries

- [ ] **Step 4: Verify notifications integration by checking memory/notifications.json exists**

```bash
ls -la memory/notifications.json
```

Expected: File exists

- [ ] **Step 5: Commit integration fix**

```bash
git add -A
git commit -m "test(kai-event-bus): integration verification — full test suite + API smoke"
```
