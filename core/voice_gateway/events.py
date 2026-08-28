"""12-event vocabulary for the Kai Voice Gateway WSS protocol.

All events are JSON frames. Binary frames carry raw audio (PCM16 16kHz mono).

Client → Gateway:
  wake.detected   — wake word or push-to-talk activated
  vad.stop        — VAD detected end of speech
  tts.done        — client finished playing a TTS segment
  ping            — heartbeat (1-byte payload, any content)

Gateway → Client:
  stt.partial     — interim transcription
  stt.final       — final transcription, fires brain.start
  brain.start     — brain routing started
  tts.chunk       — TTS audio segment (binary frame follows JSON)
  tts.done        — all TTS audio delivered
  turn.end        — turn complete, includes latency breakdown
  error           — non-fatal error (e.g. STT failed to produce text)
  wake.interrupt  — "Kai, stop" was detected, current turn cancelled
  vad.stop        — VAD endpoint reached (15s hard cap or silence)
  pong            — heartbeat echo (echoes whatever client sent)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
import json
import time
from typing import Any, Optional


class EventType(str, Enum):
    # Client → Gateway
    WAKE_DETECTED = "wake.detected"
    VAD_STOP = "vad.stop"           # bidirectional: client sends vad.stop
    TTS_DONE = "tts.done"           # bidirectional: client sends tts.done
    PING = "ping"

    # Gateway → Client
    STT_PARTIAL = "stt.partial"
    STT_FINAL = "stt.final"
    BRAIN_START = "brain.start"
    BRAIN_THINKING = "brain.thinking"   # LLM is generating tokens (after initial planning)
    BRAIN_TOOL_CALL = "brain.tool_call"  # tool use in progress
    TTS_CHUNK = "tts.chunk"
    TURN_END = "turn.end"
    ERROR = "error"
    WAKE_INTERRUPT = "wake.interrupt"
    PONG = "pong"

    # Aliases for bidirectional events (same string value as above)
    VAD_STOP_CLIENT = "vad.stop"     # same value as VAD_STOP
    TTS_DONE_CLIENT = "tts.done"     # same value as TTS_DONE


# All valid outbound (Gateway → Client) event types
EVENTS = [e.value for e in EventType]


def is_valid_event(name: str) -> bool:
    return name in EVENTS


@dataclass
class VoiceEvent:
    """Structured voice event with timestamp and session tracking."""
    type: str
    session_id: str = ""
    data: dict = field(default_factory=dict)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = _iso_now()

    def to_json(self) -> str:
        """Serialize to NDJSON-safe string."""
        return json.dumps({
            "type": self.type,
            "session_id": self.session_id,
            "data": self.data,
            "timestamp": self.timestamp,
        }, separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> "VoiceEvent":
        obj = json.loads(raw)
        return cls(
            type=obj.get("type", ""),
            session_id=obj.get("session_id", ""),
            data=obj.get("data", {}),
            timestamp=obj.get("timestamp", ""),
        )


def make_event(type: str, session_id: str = "", **data) -> VoiceEvent:
    return VoiceEvent(type=type, session_id=session_id, data=data)


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Outbound event builders (Gateway → Client)
# ---------------------------------------------------------------------------

def stt_partial(session_id: str, text: str) -> VoiceEvent:
    return make_event("stt.partial", session_id, text=text)


def stt_final(session_id: str, text: str) -> VoiceEvent:
    return make_event("stt.final", session_id, text=text)


def brain_start(session_id: str, provider: str = "") -> VoiceEvent:
    return make_event("brain.start", session_id, provider=provider)


def brain_thinking(session_id: str, provider: str = "") -> VoiceEvent:
    """LLM is actively generating tokens — stream has started."""
    return make_event("brain.thinking", session_id, provider=provider)


def brain_tool_call(session_id: str, tool_name: str = "", call_id: str = "") -> VoiceEvent:
    """A tool call was invoked during the turn."""
    return make_event("brain.tool_call", session_id, tool_name=tool_name, call_id=call_id)


def tts_chunk(session_id: str, index: int, is_last: bool = False) -> VoiceEvent:
    return make_event("tts.chunk", session_id, index=index, is_last=is_last)


def tts_done(session_id: str) -> VoiceEvent:
    return make_event("tts.done", session_id)


def turn_end(
    session_id: str,
    stt_ms: int,
    brain_ms: int,
    tts_ms: int,
    text: str = "",
    provider: str = "",
) -> VoiceEvent:
    return make_event("turn.end", session_id,
        text=text,
        provider=provider,
        stt_ms=stt_ms,
        brain_ms=brain_ms,
        tts_ms=tts_ms,
    )


def voice_error(session_id: str, code: str, message: str) -> VoiceEvent:
    return make_event("error", session_id, code=code, message=message)


def wake_interrupt(session_id: str) -> VoiceEvent:
    return make_event("wake.interrupt", session_id)


def vad_stop(session_id: str, reason: str = "silence") -> VoiceEvent:
    return make_event("vad.stop", session_id, reason=reason)


def pong(payload: bytes = b"") -> str:
    """Return raw pong bytes (echoes whatever client sent)."""
    return payload.decode("latin-1") if payload else "pong"
