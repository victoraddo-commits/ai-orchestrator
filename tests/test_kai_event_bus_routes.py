"""Tests for KAI Core Event Bus FastAPI routes."""

import pytest
from fastapi.testclient import TestClient

from core.api import app
from core.kai_event_bus import event_bus


client = TestClient(app)


class TestGetEvents:
    """GET /kai/events — query recent events with optional filters."""

    def test_get_events_returns_list(self):
        """GET /kai/events returns 200 with events list."""
        response = client.get("/kai/events")
        assert response.status_code == 200
        data = response.json()
        assert "events" in data
        assert isinstance(data["events"], list)
        assert "count" in data

    def test_get_events_filter_by_topic_pattern(self):
        """topic_pattern query param filters events by fnmatch glob."""
        # Publish events with distinct topics
        event_bus.publish("test.topic.alpha", {"n": 1}, source="test_src")
        event_bus.publish("test.topic.beta", {"n": 2}, source="test_src")
        event_bus.publish("other.topic.gamma", {"n": 3}, source="test_src")

        response = client.get("/kai/events?topic_pattern=test.topic.*")
        assert response.status_code == 200
        events = response.json()["events"]
        assert all("test.topic" in e["topic"] for e in events)

    def test_get_events_filter_by_source(self):
        """source query param filters events by exact source match."""
        event_bus.publish("source.filter.test", {"x": 1}, source="source_a")
        event_bus.publish("source.filter.test", {"x": 2}, source="source_b")

        response = client.get("/kai/events?source=source_a")
        assert response.status_code == 200
        events = response.json()["events"]
        assert all(e["source"] == "source_a" for e in events)

    def test_get_events_filter_by_severity(self):
        """severity query param filters events by exact severity match."""
        event_bus.publish("severity.filter.test", {"y": 1}, source="test", severity="important")
        event_bus.publish("severity.filter.test", {"y": 2}, source="test", severity="informational")

        response = client.get("/kai/events?severity=important")
        assert response.status_code == 200
        events = response.json()["events"]
        assert all(e["severity"] == "important" for e in events)

    def test_get_events_limit(self):
        """limit query param restricts the number of returned events."""
        # Publish several events
        for i in range(5):
            event_bus.publish("limit.test.event", {"i": i}, source="test")

        response = client.get("/kai/events?limit=3")
        assert response.status_code == 200
        data = response.json()
        assert len(data["events"]) <= 3


class TestGetEventStats:
    """GET /kai/events/stats — bus statistics."""

    def test_get_event_stats(self):
        """GET /kai/events/stats returns subscriber_count, recent_event_count, journal_size_bytes."""
        response = client.get("/kai/events/stats")
        assert response.status_code == 200
        data = response.json()
        assert "subscriber_count" in data
        assert "recent_event_count" in data
        assert "journal_size_bytes" in data


class TestPublishEvent:
    """POST /kai/events/publish — publish a custom event."""

    def test_post_publish(self):
        """POST /kai/events/publish returns published count and topic."""
        response = client.post(
            "/kai/events/publish",
            params={
                "topic": "test.post.publish",
                "source": "test_client",
                "severity": "informational",
            },
            json={"foo": "bar"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "published" in data
        assert data["topic"] == "test.post.publish"
