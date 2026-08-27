"""Kai Voice Gateway — JARVIS Phase 1.

A Python package providing a WebSocket voice pipeline:
  wake word → VAD → STT → brain routing → TTS

Packaged here so it can be imported by core/api.py and mounted
alongside the orchestrator HTTP endpoints.
"""
from core.voice_gateway.events import (
    EVENTS,
    EventType,
    is_valid_event,
)
from core.voice_gateway.pipeline import VoicePipeline
from core.voice_gateway.telemetry import VoiceTurnTimer

__all__ = [
    "EVENTS",
    "EventType",
    "is_valid_event",
    "VoicePipeline",
    "VoiceTurnTimer",
]
