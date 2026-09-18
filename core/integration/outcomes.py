"""§37 MODULE INTEGRATION — mission outcome recorder.

Subscribes to the KAI event bus and, for any mission that originated from a
module capability request, records the outcome back into the requesting
module's Second Brain record (through :class:`ModuleBridge`). Events for
missions that were not module-originated are ignored.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

_TOPICS = ("mission.completed", "mission.failed")


class OutcomeRecorder:
    def __init__(self, engine: Any, bridge: Any, bus: Any = None) -> None:
        self.engine = engine
        self.bridge = bridge
        self.bus = bus
        self._subs: list[str] = []
        if bus is not None:
            for topic in _TOPICS:
                try:
                    self._subs.append(bus.subscribe(topic, self._on_event))
                except Exception:
                    logger.debug("could not subscribe to %s", topic, exc_info=True)

    def _on_event(self, topic: str, envelope: dict) -> None:
        payload = envelope.get("payload") or {}
        mission_id = payload.get("mission_id")
        if not mission_id:
            return
        mission = None
        try:
            mission = self.engine.get_mission(mission_id)
        except Exception:
            logger.debug("mission lookup failed for %s", mission_id, exc_info=True)
        try:
            self.bridge.record_outcome(mission_id, mission, topic)
        except Exception:
            logger.warning("module outcome recording failed for %s", mission_id,
                           exc_info=True)

    def stop(self) -> None:
        if self.bus is None:
            return
        for sub_id in self._subs:
            try:
                self.bus.unsubscribe(sub_id)
            except Exception:
                pass
        self._subs = []
