"""Tests for core.kai_event_bus."""

import pytest
import threading
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.kai_event_bus import (
    KAIEventBus, event_bus, subscribe, publish,
    CRITICAL, IMPORTANT, INFORMATIONAL,
    JOURNAL_FILE, JOURNAL_MAX_SIZE,
)


# ----------------------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------------------

def _fresh_bus(tmp_path):
    """Create a fresh bus with temp journal dir."""
    import threading
    bus = KAIEventBus.__new__(KAIEventBus)
    bus._subscribers = {}
    bus._recent_events = []
    bus._journal_dir = tmp_path
    bus._journal_dir.mkdir(parents=True, exist_ok=True)
    bus._started = False
    bus._lock = threading.RLock()
    bus._flusher_thread = None
    bus._stop_flusher_event = threading.Event()
    return bus


# ----------------------------------------------------------------------------------------
# Subscribe / Unsubscribe
# ----------------------------------------------------------------------------------------

class TestSubscribeUnsubscribe:
    def test_subscribe_returns_uuid(self, tmp_path):
        bus = _fresh_bus(tmp_path)
        sub_id = bus.subscribe("foo.*", lambda t, e: None)
        assert isinstance(sub_id, str)
        assert len(sub_id) == 36  # UUID

    def test_unsubscribe_true_when_exists(self, tmp_path):
        bus = _fresh_bus(tmp_path)
        sub_id = bus.subscribe("foo.*", lambda t, e: None)
        assert bus.unsubscribe(sub_id) is True

    def test_unsubscribe_false_when_not_found(self, tmp_path):
        bus = _fresh_bus(tmp_path)
        assert bus.unsubscribe("not-a-real-id") is False

    def test_unsubscribe_stops_deliveries(self, tmp_path):
        bus = _fresh_bus(tmp_path)
        received = []
        sub_id = bus.subscribe("capability.*", lambda t, e: received.append(e))
        bus.publish("capability.health.test", {"n": 1}, source="test")
        assert len(received) == 1
        bus.unsubscribe(sub_id)
        bus.publish("capability.health.test2", {"n": 2}, source="test")
        assert len(received) == 1  # still 1


# ----------------------------------------------------------------------------------------
# Publish / Fan-out
# ----------------------------------------------------------------------------------------

class TestPublishFanOut:
    def test_publish_delivers_to_matching_subscriber(self, tmp_path):
        bus = _fresh_bus(tmp_path)
        called = []
        bus.subscribe("capability.*", lambda t, e: called.append((t, e)))
        bus.publish("capability.health.test", {"foo": "bar"}, source="test")
        assert len(called) == 1
        topic, envelope = called[0]
        assert topic == "capability.health.test"
        assert envelope["payload"]["foo"] == "bar"
        assert envelope["source"] == "test"
        assert envelope["severity"] == "informational"

    def test_publish_returns_fan_out_count(self, tmp_path):
        bus = _fresh_bus(tmp_path)
        results = []
        bus.subscribe("a.*", lambda t, e: results.append(1))
        bus.subscribe("a.*", lambda t, e: results.append(2))
        bus.subscribe("b.*", lambda t, e: results.append(3))
        count = bus.publish("a.test", {}, source="test")
        assert count == 2
        assert len(results) == 2

    def test_publish_no_match_returns_zero(self, tmp_path):
        bus = _fresh_bus(tmp_path)
        called = []
        bus.subscribe("x.*", lambda t, e: called.append(1))
        count = bus.publish("y.test", {}, source="test")
        assert count == 0
        assert len(called) == 0

    def test_multiple_subscribers_same_pattern(self, tmp_path):
        bus = _fresh_bus(tmp_path)
        results = []
        bus.subscribe("build.*", lambda t, e: results.append(1))
        bus.subscribe("build.*", lambda t, e: results.append(2))
        bus.publish("build.started", {}, source="test")
        assert sorted(results) == [1, 2]


# ----------------------------------------------------------------------------------------
# Pattern matching (fnmatch)
# ----------------------------------------------------------------------------------------

class TestPatternMatching:
    def test_star_matches_any_segment(self, tmp_path):
        bus = _fresh_bus(tmp_path)
        called = []
        bus.subscribe("capability.*", lambda t, e: called.append(t))
        bus.publish("capability.health.foo", {}, source="test")
        bus.publish("capability.updated.bar", {}, source="test")
        assert called == ["capability.health.foo", "capability.updated.bar"]

    def test_exact_match(self, tmp_path):
        bus = _fresh_bus(tmp_path)
        called = []
        bus.subscribe("service.down", lambda t, e: called.append(t))
        bus.publish("service.down", {}, source="test")
        bus.publish("service.down.foo", {}, source="test")
        assert called == ["service.down"]

    def test_service_down_wildcard(self, tmp_path):
        bus = _fresh_bus(tmp_path)
        called = []
        bus.subscribe("service.down.*", lambda t, e: called.append(t))
        bus.publish("service.down.my-svc", {}, source="test")
        bus.publish("service.down", {}, source="test")
        assert called == ["service.down.my-svc"]

    def test_no_match_different_prefix(self, tmp_path):
        bus = _fresh_bus(tmp_path)
        called = []
        bus.subscribe("service.*", lambda t, e: called.append(t))
        bus.publish("capability.health", {}, source="test")
        assert called == []


# ----------------------------------------------------------------------------------------
# Source filter
# ----------------------------------------------------------------------------------------

class TestSourceFilter:
    def test_source_filter_matches(self, tmp_path):
        bus = _fresh_bus(tmp_path)
        called = []
        bus.subscribe("audit.*", lambda t, e: called.append(e["source"]), sources={"foo", "bar"})
        bus.publish("audit.critical", {}, source="foo")
        bus.publish("audit.critical", {}, source="bar")
        assert called == ["foo", "bar"]

    def test_source_filter_excludes(self, tmp_path):
        bus = _fresh_bus(tmp_path)
        called = []
        bus.subscribe("audit.*", lambda t, e: called.append(e["source"]), sources={"foo"})
        bus.publish("audit.critical", {}, source="foo")
        bus.publish("audit.critical", {}, source="baz")
        assert called == ["foo"]

    def test_source_filter_none_allows_all(self, tmp_path):
        bus = _fresh_bus(tmp_path)
        called = []
        bus.subscribe("audit.*", lambda t, e: called.append(e["source"]), sources=None)
        bus.publish("audit.critical", {}, source="any")
        assert called == ["any"]


# ----------------------------------------------------------------------------------------
# Severity + journal defaults
# ----------------------------------------------------------------------------------------

class TestJournalDefaults:
    def test_critical_is_journaled_by_default(self, tmp_path, monkeypatch):
        import core.kai_event_bus as keb
        monkeypatch.setattr(keb, "_JOURNAL_BUFFER", [])
        monkeypatch.setattr(keb, "_BUFFER_LOCK", threading.Lock())
        bus = _fresh_bus(tmp_path)
        called = []
        bus.subscribe("audit.*", lambda t, e: called.append(e["journal"]))
        bus.publish("audit.critical.test", {}, source="test", severity=CRITICAL)
        assert called[-1] is True

    def test_important_not_journaled_by_default(self, tmp_path):
        bus = _fresh_bus(tmp_path)
        called = []
        bus.subscribe("capability.*", lambda t, e: called.append(e["journal"]))
        bus.publish("capability.health.test", {}, source="test", severity=IMPORTANT)
        assert called[-1] is False

    def test_service_down_is_journaled(self, tmp_path):
        bus = _fresh_bus(tmp_path)
        called = []
        bus.subscribe("service.down.*", lambda t, e: called.append(e["journal"]))
        bus.publish("service.down.my-svc", {}, source="test", severity=IMPORTANT)
        assert called[-1] is True

    def test_remediation_is_journaled(self, tmp_path):
        bus = _fresh_bus(tmp_path)
        called = []
        bus.subscribe("remediation.*", lambda t, e: called.append(e["journal"]))
        bus.publish("remediation.triggered.1", {}, source="test", severity=IMPORTANT)
        assert called[-1] is True

    def test_replay_flag_is_false_on_live_events(self, tmp_path):
        bus = _fresh_bus(tmp_path)
        called = []
        bus.subscribe("kai.*", lambda t, e: called.append(e["replay"]))
        bus.publish("kai.started", {}, source="test")
        assert called[-1] is False

    def test_replay_flag_is_true_on_replayed_events(self, tmp_path):
        bus = _fresh_bus(tmp_path)
        called = []
        bus.subscribe("kai.*", lambda t, e: called.append(e["replay"]))
        bus.publish("kai.started", {}, source="test", journal=False, replay=True)
        assert called[-1] is True


# ----------------------------------------------------------------------------------------
# Journal write + rotation
# ----------------------------------------------------------------------------------------

class TestJournalWrite:
    def test_journal_file_created_on_critical_event(self, tmp_path, monkeypatch):
        import core.kai_event_bus as keb
        monkeypatch.setattr(keb, "_JOURNAL_BUFFER", [])
        monkeypatch.setattr(keb, "_BUFFER_LOCK", threading.RLock())  # must be RLock for re-entrancy
        monkeypatch.setattr(keb, "_BUFFER_SIZE", 1)  # flush immediately
        bus = _fresh_bus(tmp_path)
        bus.subscribe("service.down.*", lambda t, e: None)
        bus.publish("service.down.test", {"svc": "test"}, source="test_src", severity=CRITICAL)
        bus._flush_journal_buffer()
        journal_file = tmp_path / JOURNAL_FILE
        assert journal_file.exists()
        lines = [l for l in journal_file.read_text().splitlines() if l.strip()]
        assert len(lines) == 1
        import json
        entry = json.loads(lines[0])
        assert entry["topic"] == "service.down.test"
        assert entry["source"] == "test_src"

    def test_journal_rotation_at_max_size(self, tmp_path, monkeypatch):
        import core.kai_event_bus as keb
        monkeypatch.setattr(keb, "_JOURNAL_BUFFER", [])
        monkeypatch.setattr(keb, "_BUFFER_LOCK", threading.RLock())  # must be RLock for re-entrancy
        monkeypatch.setattr(keb, "_BUFFER_SIZE", 1000)
        monkeypatch.setattr(keb, "JOURNAL_MAX_SIZE", 50)  # tiny for test
        bus = _fresh_bus(tmp_path)
        bus.subscribe("kai.*", lambda t, e: None)
        for i in range(5):
            bus.publish(f"kai.started.{i}", {"n": i}, source="test", severity=CRITICAL)
            bus._flush_journal_buffer()
        journal_file = tmp_path / JOURNAL_FILE
        assert journal_file.exists()
        rotated = tmp_path / f"{JOURNAL_FILE}.1"
        assert rotated.exists()


# ----------------------------------------------------------------------------------------
# Replay
# ----------------------------------------------------------------------------------------

class TestReplay:
    def test_replay_delivers_events_to_current_subscribers(self, tmp_path, monkeypatch):
        import core.kai_event_bus as keb
        monkeypatch.setattr(keb, "_JOURNAL_BUFFER", [])
        monkeypatch.setattr(keb, "_BUFFER_LOCK", threading.RLock())  # must be RLock for re-entrancy
        monkeypatch.setattr(keb, "_BUFFER_SIZE", 1)
        bus = _fresh_bus(tmp_path)
        bus.subscribe("kai.*", lambda t, e: None)
        bus.publish("kai.started", {"msg": "hello"}, source="test", severity=CRITICAL)
        bus._flush_journal_buffer()
        bus2 = _fresh_bus(tmp_path)
        received = []
        bus2.subscribe("kai.*", lambda t, e: received.append(e))
        count = bus2.replay_journal()
        assert count == 1
        assert received[0]["payload"]["msg"] == "hello"
        assert received[0]["replay"] is True


# ----------------------------------------------------------------------------------------
# Module-level convenience API
# ----------------------------------------------------------------------------------------

class TestModuleAPI:
    def test_module_subscribe_works(self, tmp_path):
        import core.kai_event_bus as keb
        orig = keb.KAIEventBus.get_instance
        keb.KAIEventBus.get_instance = classmethod(lambda cls: _fresh_bus(tmp_path))
        try:
            keb.event_bus._journal_dir = tmp_path
            received = []
            keb.subscribe("foo.*", lambda t, e: received.append(t))
            keb.publish("foo.bar", {"x": 1}, source="test")
            assert received == ["foo.bar"]
        finally:
            keb.KAIEventBus.get_instance = orig

    def test_module_publish_works(self, tmp_path):
        import core.kai_event_bus as keb
        orig = keb.KAIEventBus.get_instance
        keb.KAIEventBus.get_instance = classmethod(lambda cls: _fresh_bus(tmp_path))
        try:
            keb.event_bus._journal_dir = tmp_path
            called = []
            keb.event_bus.subscribe("test.*", lambda t, e: called.append(t))
            keb.publish("test.one", {"y": 2}, source="test")
            assert called == ["test.one"]
        finally:
            keb.KAIEventBus.get_instance = orig


# ----------------------------------------------------------------------------------------
# Flusher thread (time-based)
# ----------------------------------------------------------------------------------------

class TestFlusher:
    def test_time_based_flush_writes_journal(self, tmp_path, monkeypatch):
        import core.kai_event_bus as keb
        monkeypatch.setattr(keb, "_JOURNAL_BUFFER", [])
        monkeypatch.setattr(keb, "_BUFFER_LOCK", threading.RLock())
        monkeypatch.setattr(keb, "_BUFFER_SIZE", 9999)  # disable count-based flush
        monkeypatch.setattr(keb, "_BUFFER_FLUSH_INTERVAL", 0.05)  # 50ms for test
        bus = _fresh_bus(tmp_path)
        # Patch replay_journal so start() doesn't try to replay during test
        bus.replay_journal = lambda: 0
        bus.start()  # launches flusher thread
        bus.subscribe("kai.*", lambda t, e: None)
        bus.publish("kai.started", {"msg": "time-flush"}, source="test", severity=CRITICAL)
        # Give flusher time to fire (4x interval)
        time.sleep(0.2)
        journal_file = tmp_path / JOURNAL_FILE
        assert journal_file.exists(), "journal file should exist after time-based flush"
        lines = [l for l in journal_file.read_text().splitlines() if l.strip()]
        assert len(lines) == 1
        import json
        entry = json.loads(lines[0])
        assert entry["topic"] == "kai.started"
        assert entry["payload"]["msg"] == "time-flush"
        bus.stop()  # clean shutdown


# ----------------------------------------------------------------------------------------
# Recent events
# ----------------------------------------------------------------------------------------

class TestRecentEvents:
    def test_recent_events_stored(self, tmp_path):
        bus = _fresh_bus(tmp_path)
        bus.subscribe("x.*", lambda t, e: None)
        for i in range(5):
            bus.publish(f"x.{i}", {"n": i}, source="test")
        assert len(bus._recent_events) == 5
        assert bus._recent_events[-1]["topic"] == "x.4"

    def test_recent_events_capped_at_max(self, tmp_path):
        bus = _fresh_bus(tmp_path)
        bus.subscribe("x.*", lambda t, e: None)
        for i in range(2000):
            bus.publish(f"x.{i}", {"n": i}, source="test")
        assert len(bus._recent_events) == 1000

    def test_get_stats(self, tmp_path):
        bus = _fresh_bus(tmp_path)
        bus.subscribe("x.*", lambda t, e: None)
        bus.publish("x.1", {}, source="test")
        stats = bus.get_stats()
        assert stats["subscriber_count"] == 1
        assert stats["recent_event_count"] == 1
        assert stats["journal_size_bytes"] == 0
