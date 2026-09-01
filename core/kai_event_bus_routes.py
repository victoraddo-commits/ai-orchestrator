"""FastAPI routes for the KAI Core Event Bus."""

import asyncio
import fnmatch
import json as _json
from fastapi import APIRouter, Query, Body, Request
from fastapi.responses import StreamingResponse
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


@router.get("/stream")
def stream_events(
    topic_pattern: Optional[str] = Query(None, description="fnmatch glob pattern to filter topics (e.g. 'orchestrator.*')"),
    source: Optional[str] = Query(None, description="Filter by event source"),
    severity: Optional[str] = Query(None, description="Filter by severity level"),
):
    """Server-Sent Events (SSE) stream of events matching the given filters.

    The stream stays open and pushes new events as they are published to the bus.
    Each event is sent as a JSON comment line prefixed with 'data: '.
    A heartbeat comment ': ping\\n\\n' is sent every 25s to keep the connection alive.

    Clients should reconnect on disconnect — the stream replays the last 5 events
    from the in-memory buffer on first connection.
    """
    async def event_generator(request: Request):
        # Yield the last 5 events so new clients don't miss recent state
        recent = list(event_bus._recent_events)[-5:]
        for event in recent:
            if not _matches_filters(event, topic_pattern, source, severity):
                continue
            yield f"data: {_json.dumps(event)}\n\n"

        queue: asyncio.Queue = asyncio.Queue()

        # The event bus publishes (topic, envelope) to handlers.
        # envelope already contains: topic, payload, source, severity, timestamp, id, journal
        def make_handler():
            def handler(topic: str, envelope: dict):
                asyncio.create_task(queue.put(envelope))
            return handler

        sub_id = event_bus.subscribe(
            topic_pattern or "*",
            make_handler(),
            sources={source} if source else None,
        )

        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=25.0)
                    if not _matches_filters(event, topic_pattern, source, severity):
                        continue
                    yield f"data: {_json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield f": ping\n\n"
        finally:
            event_bus.unsubscribe(sub_id)

    return StreamingResponse(
        event_generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _matches_filters(event, topic_pattern, source, severity):
    import fnmatch as _fnmatch
    topic = event.get("topic", "")
    if topic_pattern and not _fnmatch.fnmatch(topic, topic_pattern):
        return False
    if source and event.get("source") != source:
        return False
    if severity and event.get("severity") != severity:
        return False
    return True


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
