"""
KAI Core Event Bus — thread-safe pub/sub with fnmatch patterns and journal persistence.

Publish: event_bus.publish(topic, payload, source, severity, journal)
Subscribe: event_bus.subscribe(topic_pattern, handler) → sub_id
Unsubscribe: event_bus.unsubscribe(sub_id) → bool
Start: event_bus.start()  # initializes journal directory, runs replay
Stats: event_bus.get_stats() → dict
"""

import fnmatch
import json as _json
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Callable, Optional

CRITICAL = "critical"
IMPORTANT = "important"
INFORMATIONAL = "informational"
SEVERITY_ORDER = {CRITICAL: 0, IMPORTANT: 1, INFORMATIONAL: 2}

JOURNAL_FILE = "kai_event_journal.jsonl"
JOURNAL_MAX_SIZE = 10 * 1024 * 1024  # 10 MB
JOURNAL_MAX_ROTATIONS = 3
_MAX_RECENT = 1000

_logger = logging.getLogger(__name__)

# Journal buffer (module-level for simplicity)
_JOURNAL_BUFFER: list[dict] = []
_BUFFER_LOCK: threading.RLock = threading.RLock()
_BUFFER_SIZE = 100


def _timestamp() -> float:
    import time
    return time.time()


class KAIEventBus:
    """Thread-safe pub/sub event bus with fnmatch topic patterns and journal persistence."""

    _instance: Optional["KAIEventBus"] = None

    @classmethod
    def get_instance(cls) -> "KAIEventBus":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        # subscribers: sub_id -> {pattern, handler, sources}
        self._subscribers: dict[str, dict] = {}
        self._recent_events: list[dict] = []
        self._lock = threading.RLock()
        self._journal_dir = Path(__file__).parent.parent / "memory"
        self._started = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Initialize journal dir and replay missed events. Call once at startup."""
        if self._started:
            return
        self._started = True
        self._journal_dir.mkdir(parents=True, exist_ok=True)
        self._flush_journal_buffer()  # flush any buffered writes from imports
        self.replay_journal()

    # ------------------------------------------------------------------
    # Subscription management
    # ------------------------------------------------------------------

    def subscribe(
        self,
        topic_pattern: str,
        handler: Callable[[str, dict], None],
        sources: Optional[set[str]] = None,
    ) -> str:
        """Register a handler for a fnmatch glob pattern. Returns sub_id."""
        sub_id = str(uuid.uuid4())
        with self._lock:
            self._subscribers[sub_id] = {
                "pattern": topic_pattern,
                "handler": handler,
                "sources": sources,
            }
        return sub_id

    def unsubscribe(self, sub_id: str) -> bool:
        """Remove a subscription. Returns True if found."""
        with self._lock:
            if sub_id in self._subscribers:
                del self._subscribers[sub_id]
                return True
        return False

    # ------------------------------------------------------------------
    # Publish
    # ------------------------------------------------------------------

    def publish(
        self,
        topic: str,
        payload: dict,
        source: str = "unknown",
        severity: str = INFORMATIONAL,
        journal: Optional[bool] = None,
        replay: bool = False,
    ) -> int:
        """Publish an event to all matching subscribers.

        journal defaults: CRITICAL severity OR topic starts with remediation./recovery. OR topic == service.down
        Returns fan-out count (number of subscribers that received the event).
        """
        envelope = {
            "id": str(uuid.uuid4()),
            "topic": topic,
            "payload": payload,
            "source": source,
            "timestamp": _timestamp(),
            "severity": severity,
            "replay": replay,
        }
        if journal is None:
            journal = (
                severity == CRITICAL
                or topic.startswith("remediation.")
                or topic.startswith("recovery.")
                or topic.startswith("service.down")
            )
        envelope["journal"] = journal

        # Deliver to subscribers
        count = 0
        with self._lock:
            for sub_id, sub in self._subscribers.items():
                if not fnmatch.fnmatch(topic, sub["pattern"]):
                    continue
                if sub["sources"] is not None and source not in sub["sources"]:
                    continue
                try:
                    sub["handler"](topic, envelope)
                    count += 1
                except Exception as exc:
                    _logger.warning("Event handler error [%s]: %s", sub_id, exc)

            self._recent_events.append(envelope)
            if len(self._recent_events) > _MAX_RECENT:
                self._recent_events = self._recent_events[-_MAX_RECENT:]

        # Journal asynchronously (buffer and flush)
        if journal and not replay:
            self._journal_buffer_append(envelope)

        return count

    # ------------------------------------------------------------------
    # Journal
    # ------------------------------------------------------------------

    def _journal_buffer_append(self, envelope: dict) -> None:
        global _JOURNAL_BUFFER
        with _BUFFER_LOCK:
            _JOURNAL_BUFFER.append(envelope)
            if len(_JOURNAL_BUFFER) >= _BUFFER_SIZE:
                self._flush_journal_buffer()

    def _flush_journal_buffer(self) -> None:
        global _JOURNAL_BUFFER
        with _BUFFER_LOCK:
            if not _JOURNAL_BUFFER:
                return
            entries = list(_JOURNAL_BUFFER)
            _JOURNAL_BUFFER = []

        journal_path = self._journal_dir / JOURNAL_FILE
        if journal_path.exists() and journal_path.stat().st_size >= JOURNAL_MAX_SIZE:
            self._rotate_journal()

        tmp = journal_path.with_suffix(".tmp")
        try:
            with open(tmp, "a") as fh:
                for entry in entries:
                    fh.write(_json.dumps(entry) + "\n")
            os.replace(tmp, journal_path)
        except OSError as exc:
            _logger.warning("Failed to flush journal: %s", exc)

    def _rotate_journal(self) -> None:
        """Rotate journal: .1, .2, .3, discard .3."""
        for i in range(JOURNAL_MAX_ROTATIONS, 0, -1):
            src = self._journal_dir / f"{JOURNAL_FILE}.{i}"
            dst = self._journal_dir / f"{JOURNAL_FILE}.{i + 1}"
            if dst.exists():
                dst.unlink(missing_ok=True)
            if src.exists():
                src.rename(dst)
        src = self._journal_dir / JOURNAL_FILE
        dst = self._journal_dir / f"{JOURNAL_FILE}.1"
        if dst.exists():
            dst.unlink(missing_ok=True)
        if src.exists():
            src.rename(dst)

    def replay_journal(self) -> int:
        """Re-play journal events to current subscribers. Returns replayed count."""
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
            for line in jpath.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    event = _json.loads(line)
                    self.publish(
                        event["topic"],
                        event.get("payload", {}),
                        source=event.get("source", "unknown"),
                        severity=event.get("severity", INFORMATIONAL),
                        journal=False,
                        replay=True,
                    )
                    count += 1
                except Exception as exc:
                    _logger.warning("Journal replay error [%s]: %s", jpath, exc)
        return count

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        """Return bus statistics."""
        with self._lock:
            subscriber_count = len(self._subscribers)
            recent_count = len(self._recent_events)
        journal_path = self._journal_dir / JOURNAL_FILE
        journal_size = journal_path.stat().st_size if journal_path.exists() else 0
        return {
            "subscriber_count": subscriber_count,
            "recent_event_count": recent_count,
            "journal_size_bytes": journal_size,
        }


# ---------------------------------------------------------------------------
# Module-level singleton + convenience functions
# ---------------------------------------------------------------------------

event_bus = KAIEventBus.get_instance()


def subscribe(
    topic_pattern: str,
    handler: Callable[[str, dict], None],
    sources: Optional[set[str]] = None,
) -> str:
    return event_bus.subscribe(topic_pattern, handler, sources)


def publish(
    topic: str,
    payload: dict,
    source: str = "unknown",
    severity: str = INFORMATIONAL,
    journal: Optional[bool] = None,
) -> int:
    return event_bus.publish(topic, payload, source, severity, journal)
