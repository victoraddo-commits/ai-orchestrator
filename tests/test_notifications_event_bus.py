"""Tests for notifications.py event bus integration."""
import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestNotificationsEventBus:
    def test_register_subscriptions_creates_subscriptions(self, monkeypatch):
        """register_event_subscriptions subscribes to expected topics."""
        subscribed = []
        def mock_subscribe(pattern, handler):
            subscribed.append(pattern)
            return "sub-id"

        monkeypatch.setattr("core.kai_event_bus.event_bus.subscribe", mock_subscribe)

        from core.notifications import NotificationManager
        nm = NotificationManager()
        nm.register_event_subscriptions()

        expected = ["service.down.*", "capability.health.*", "build.failed", "remediation.*"]
        assert sorted(subscribed) == sorted(expected)

    def test_handle_service_event_enqueues_notification(self, monkeypatch):
        """_handle_service_event calls enqueue with correct args."""
        enqueued = []
        def mock_enqueue(severity, title, body, source, **kw):
            enqueued.append({"severity": severity, "title": title, "body": body, "source": source})

        from core.notifications import NotificationManager
        nm = NotificationManager()
        nm.enqueue = mock_enqueue

        envelope = {
            "payload": {"service_id": "test-svc", "message": "Test message"},
            "source": "test_src",
        }
        nm._handle_service_event("service.down.test-svc", envelope)

        assert len(enqueued) == 1
        assert enqueued[0]["severity"] == "critical"
        assert "test-svc" in enqueued[0]["title"]

    def test_handle_capability_event_only_enqueues_on_degraded(self, monkeypatch):
        """_handle_capability_event only enqueues when status is degraded."""
        enqueued = []
        def mock_enqueue(severity, title, body, source, **kw):
            enqueued.append({"severity": severity})

        from core.notifications import NotificationManager
        nm = NotificationManager()
        nm.enqueue = mock_enqueue

        # Non-degraded — should not enqueue
        nm._handle_capability_event("capability.health.test", {
            "payload": {"capability_id": "test-cap", "new_status": "healthy"},
            "source": "test",
        })
        assert len(enqueued) == 0

        # Degraded — should enqueue
        nm._handle_capability_event("capability.health.test", {
            "payload": {"capability_id": "test-cap", "new_status": "degraded"},
            "source": "test",
        })
        assert len(enqueued) == 1
        assert enqueued[0]["severity"] == "important"

    def test_handle_build_event_enqueues(self, monkeypatch):
        """_handle_build_event calls enqueue with build failure info."""
        enqueued = []
        def mock_enqueue(severity, title, body, source, **kw):
            enqueued.append({"severity": severity, "title": title, "body": body, "source": source})

        from core.notifications import NotificationManager
        nm = NotificationManager()
        nm.enqueue = mock_enqueue

        envelope = {
            "payload": {"build_id": "build-123", "error": "compilation failed"},
            "source": "test_src",
        }
        nm._handle_build_event("build.failed", envelope)

        assert len(enqueued) == 1
        assert enqueued[0]["severity"] == "important"
        assert "build-123" in enqueued[0]["title"]
        assert enqueued[0]["body"] == "compilation failed"

    def test_handle_remediation_event_enqueues(self, monkeypatch):
        """_handle_remediation_event calls enqueue with remediation info."""
        enqueued = []
        def mock_enqueue(severity, title, body, source, **kw):
            enqueued.append({"severity": severity, "title": title, "body": body, "source": source})

        from core.notifications import NotificationManager
        nm = NotificationManager()
        nm.enqueue = mock_enqueue

        envelope = {
            "payload": {"remediation_id": "rem-456", "message": "Restarted service"},
            "source": "test_src",
        }
        nm._handle_remediation_event("remediation.completed", envelope)

        assert len(enqueued) == 1
        assert enqueued[0]["severity"] == "critical"
        assert "rem-456" in enqueued[0]["title"]
