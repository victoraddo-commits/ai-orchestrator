"""Per-turn voice telemetry — STT / brain / TTS split.

Written to memory/voice_telemetry.json on turn.end events.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Path resolved relative to the orchestrator root
_MEMORY_DIR = Path(__file__).parent.parent.parent / "memory"
_TELEMETRY_FILE = _MEMORY_DIR / "voice_telemetry.json"

# Rolling window — keep last N turns
_MAX_TURNS = 200


def _now_ms() -> int:
    return int(time.time() * 1000)


class VoiceTurnTimer:
    """Context manager / pair tracker for a single voice turn's timing."""

    __slots__ = ("session_id", "_t0_stt", "_t0_brain", "_t0_tts",
                 "_stt_ms", "_brain_ms", "_tts_ms", "_provider", "_text")

    def __init__(self, session_id: str = ""):
        self.session_id = session_id
        self._t0_stt: Optional[int] = None
        self._t0_brain: Optional[int] = None
        self._t0_tts: Optional[int] = None
        self._stt_ms: int = 0
        self._brain_ms: int = 0
        self._tts_ms: int = 0
        self._provider: str = ""
        self._text: str = ""

    # -------------------------------------------------------------------------
    # Markers called by pipeline stages
    # -------------------------------------------------------------------------
    def stt_done(self) -> None:
        self._stt_ms = _now_ms()

    def brain_done(self) -> None:
        self._brain_ms = _now_ms()

    def tts_done(self) -> None:
        self._tts_ms = _now_ms()

    @property
    def stt_end_ms(self) -> int:
        return self._stt_ms or _now_ms()

    @property
    def brain_end_ms(self) -> int:
        return self._brain_ms or _now_ms()

    @property
    def tts_end_ms(self) -> int:
        return self._tts_ms or _now_ms()

    def record(
        self,
        text: str = "",
        provider: str = "",
    ) -> dict:
        """Compute final deltas and return a telemetry dict."""
        now = _now_ms()
        if self._t0_stt:
            # Deltas from turn start (wake word detection)
            stt_total = self.stt_end_ms - self._t0_stt
            brain_total = (self.brain_end_ms - self._t0_stt) if self._brain_ms else 0
            tts_total = (now - self._t0_stt) if self._tts_ms else 0
        else:
            stt_total = self._stt_ms
            brain_total = self._brain_ms
            tts_total = self._tts_ms

        entry = {
            "session_id": self.session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "text": text,
            "provider": provider,
            "stt_ms": stt_total,
            "brain_ms": brain_total,
            "tts_ms": tts_total,
            "total_ms": now - (self._t0_stt or now),
        }
        self._text = text
        self._provider = provider
        return entry

    def write(self, text: str = "", provider: str = "") -> None:
        """Append a telemetry entry to voice_telemetry.json."""
        entry = self.record(text=text, provider=provider)
        try:
            _TELEMETRY_FILE.parent.mkdir(parents=True, exist_ok=True)
            if _TELEMETRY_FILE.exists():
                try:
                    data = json.loads(_TELEMETRY_FILE.read_text())
                except (json.JSONDecodeError, OSError):
                    data = []
            else:
                data = []
            data.append(entry)
            # Rolling window
            if len(data) > _MAX_TURNS:
                data = data[-_MAX_TURNS:]
            tmp = _TELEMETRY_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2))
            tmp.replace(_TELEMETRY_FILE)
        except Exception:
            # Non-fatal — telemetry is diagnostic only
            pass

    # Backward-compat helpers so pipeline code can call .stt_ms etc.
    @property
    def stt_ms(self) -> int:
        return self._stt_ms

    @property
    def brain_ms(self) -> int:
        return self._brain_ms

    @property
    def tts_ms(self) -> int:
        return self._tts_ms
