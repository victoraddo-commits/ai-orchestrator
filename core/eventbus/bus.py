"""KAI Event Bus - Redis Streams implementation."""

import json
import redis
from datetime import datetime
from typing import Optional, Dict, Any
from dataclasses import dataclass, asdict, field
from uuid import uuid4
import logging

logger = logging.getLogger(__name__)


@dataclass
class KaiEvent:
    """KAI ecosystem event."""
    event_type: str
    source: str
    data: Dict[str, Any]
    event_id: str = field(default_factory=lambda: str(uuid4()))
    correlation_id: Optional[str] = None
    causation_id: Optional[str] = None
    version: str = "1.0"
    timestamp: Optional[str] = None
    actor: Optional[Dict[str, str]] = None
    metadata: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow().isoformat() + "Z"


class EventBus:
    """
    KAI Event Bus using Redis Streams.

    Usage:
        bus = EventBus()

        # Publish
        bus.publish(KaiEvent(
            event_type="agent.task.completed",
            source="ai-orchestrator",
            data={"task_id": "123", "result": "success"}
        ))
    """

    def __init__(self, redis_url: str = "redis://localhost:6379"):
        try:
            self.redis = redis.from_url(redis_url, decode_responses=True)
            self.stream_name = "kai:events"
            self.enabled = True
            # Test connection
            self.redis.ping()
            logger.info(f"EventBus connected to {redis_url}")
        except Exception as e:
            logger.warning(f"EventBus failed to connect: {e}. Events will be logged only.")
            self.enabled = False

    def publish(self, event: KaiEvent) -> str:
        """
        Publish event to the bus.
        Returns event_id.
        """
        event_dict = asdict(event)

        if not self.enabled:
            logger.info(f"Event (bus disabled): {event.event_type} | {event.source} | {event.data}")
            return event.event_id

        try:
            # Add to Redis Stream
            self.redis.xadd(
                self.stream_name,
                {"event": json.dumps(event_dict)},
                maxlen=100000  # Keep last 100k events
            )
            logger.debug(f"Event published: {event.event_type} | {event.event_id}")
        except Exception as e:
            logger.error(f"Failed to publish event: {e}")

        return event.event_id

    def get_recent_events(self, count: int = 10) -> list:
        """Get recent events from stream (for debugging)."""
        if not self.enabled:
            return []

        try:
            messages = self.redis.xrevrange(self.stream_name, "+", "-", count)
            events = []
            for event_id, event_data in messages:
                event_json = event_data.get("event")
                if event_json:
                    event_dict = json.loads(event_json)
                    events.append(KaiEvent(**event_dict))
            return events
        except Exception as e:
            logger.error(f"Failed to get recent events: {e}")
            return []


# Global event bus instance
_event_bus = None


def get_event_bus() -> EventBus:
    """Get global event bus instance."""
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus


def publish_event(
    event_type: str,
    source: str,
    data: Dict[str, Any],
    actor: Optional[Dict[str, str]] = None,
    **kwargs
) -> str:
    """
    Convenience function to publish an event.

    Example:
        publish_event(
            "agent.task.completed",
            "ai-orchestrator",
            {"task_id": "123", "result": "success"},
            actor={"user_id": "user-1", "agent_id": "agent-1"}
        )
    """
    bus = get_event_bus()
    event = KaiEvent(
        event_type=event_type,
        source=source,
        data=data,
        actor=actor,
        **kwargs
    )
    return bus.publish(event)
