"""Event/audit sinks for the browser operator.

The manager never imports the orchestrator directly. It emits through a sink:

* :class:`JsonlSink` — standalone CT110 service: append-only JSONL on disk.
* :class:`RecordingSink` — tests: captures emissions in memory.
* :class:`KaiBusSink` — CT111: publishes on the real ``core.kai_event_bus`` and
  writes the HMAC ``core.audit_logger`` record.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Optional


class EventSink:
    def emit(self, topic: str, payload: dict) -> None:  # pragma: no cover - interface
        pass

    def audit(self, event_type: str, details: dict, *, method: str = "ACTION",
              status_code: int = 200) -> None:  # pragma: no cover - interface
        pass


class RecordingSink(EventSink):
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []
        self.audits: list[dict] = []

    def emit(self, topic: str, payload: dict) -> None:
        self.events.append((topic, dict(payload)))

    def audit(self, event_type: str, details: dict, *, method: str = "ACTION",
              status_code: int = 200) -> None:
        self.audits.append(
            {"event_type": event_type, "details": dict(details),
             "method": method, "status_code": status_code}
        )

    def topics(self) -> list[str]:
        return [t for t, _ in self.events]


class JsonlSink(EventSink):
    def __init__(self, data_dir: Path, source: str = "browser_operator") -> None:
        self.events_path = Path(data_dir) / "events.jsonl"
        self.audit_path = Path(data_dir) / "audit.jsonl"
        self.source = source
        self._lock = threading.Lock()
        Path(data_dir).mkdir(parents=True, exist_ok=True)

    def _append(self, path: Path, record: dict) -> None:
        try:
            with self._lock, open(path, "a") as fh:
                fh.write(json.dumps(record, default=str) + "\n")
        except OSError:
            pass

    def emit(self, topic: str, payload: dict) -> None:
        self._append(self.events_path, {"topic": topic, "payload": payload, "source": self.source})

    def audit(self, event_type: str, details: dict, *, method: str = "ACTION",
              status_code: int = 200) -> None:
        self._append(self.audit_path, {
            "event_type": event_type, "operator": self.source,
            "method": method, "status_code": status_code, "details": details,
        })


class KaiBusSink(EventSink):
    """CT111 sink: real event bus + HMAC audit log. Imported lazily."""

    def __init__(self, source: str = "browser_operator") -> None:
        self.source = source

    def emit(self, topic: str, payload: dict) -> None:
        try:
            from core import kai_event_bus

            kai_event_bus.publish(topic, payload, source=self.source)
        except Exception:
            pass

    def audit(self, event_type: str, details: dict, *, method: str = "ACTION",
              status_code: int = 200) -> None:
        try:
            from core import audit_logger

            audit_logger.log_audit_event(
                event_type=event_type, operator=self.source,
                endpoint=details.get("session_id") and f"browser/{details['session_id']}" or "browser",
                method=method, status_code=status_code, details=details,
            )
        except Exception:
            pass


def default_sink(data_dir: Optional[Path] = None) -> EventSink:
    if data_dir is not None:
        return JsonlSink(data_dir)
    if os.environ.get("AI_ORCHESTRATOR_MEMORY_DIR"):
        # Running inside the orchestrator process — use the real bus/audit.
        return KaiBusSink()
    return JsonlSink(Path(os.environ.get("KAI_BROWSER_DATA_DIR", "/opt/kai-browser/data")))
