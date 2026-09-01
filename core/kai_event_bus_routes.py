"""FastAPI routes for the KAI Core Event Bus."""

import fnmatch
from fastapi import APIRouter, Query, Body
from typing import Optional

from core.kai_event_bus import event_bus, INFORMATIONAL

router = APIRouter(prefix="/kai/events", tags=["kai", "events"])


@router.get("")
def get_events(
    topic_pattern: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
):
    """Query recent events from the in-memory event store."""
    events = list(event_bus._recent_events)[-limit:]
    if topic_pattern:
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
    payload: dict = Body(...),
    source: str = "api",
    severity: str = "informational",
):
    """Publish a custom event (operator capability required)."""
    count = event_bus.publish(topic, payload, source, severity)
    return {"published": count, "topic": topic}
