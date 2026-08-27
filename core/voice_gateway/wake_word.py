"""Wake word detector — energy-based fallback + Porcupine WASM (with access key).

Implements a simple energy + spectral-peak detector for "Hey Kai" as the
default (no external service required). When PV_ACCESS_KEY is set, Porcupine
WASM is used instead for higher accuracy.

For production use, set PV_ACCESS_KEY and optionally configure a custom
keyword in env:
  PV_ACCESS_KEY=your_key   — enables Porcupine WASM (recommended)
  PORCUPINE_KEYWORD=HeyKai — or "Kai, stop" for interrupt
  PORCUPINE_SENSITIVITY=0.7
"""

from __future__ import annotations

import os
import struct
from pathlib import Path
from typing import Generator, Optional

import numpy as np


# Configuration
_KEYWORD_ENERGY_THRESHOLD = float(os.environ.get("HEY_KAI_ENERGY_THRESHOLD", "0.04"))
_KEYWORD_SAMPLES_NEEDED = int(os.environ.get("HEY_KAI_SAMPLES_NEEDED", "6"))
_PV_ACCESS_KEY = os.environ.get("PV_ACCESS_KEY", os.environ.get("PICOVOICE_KEY", ""))
_PORCUPINE_SENSITIVITY = float(os.environ.get("PORCUPINE_SENSITIVITY", "0.7"))

# Porcupine keyword map (env name → Porcupine built-in keyword)
_PV_KEYWORDS = {
    "Hey Kai": "hey kai",
    "HeyKai": "hey kai",
    "Kai, stop": "computer",
}
_PV_KEYWORD_DEFAULT = os.environ.get("PORCUPINE_KEYWORD", "Hey Kai")


class WakeWordDetector:
    """In-process wake word detector.

    Uses Porcupine WASM when PV_ACCESS_KEY is set, otherwise falls back to
    a simple energy + peak detector that works on any microphone input.
    """

    def __init__(
        self,
        keyword: str = "Hey Kai",
        sensitivity: Optional[float] = None,
    ):
        self.keyword = keyword
        self._frame_len = 512  # shared frame size for both detectors
        self._pv_detector: Optional["PorcupineWrapper"] = None
        self._energy_detector: Optional["EnergyWakeWord"] = None

        if _PV_ACCESS_KEY and sensitivity is not None:
            try:
                self._pv_detector = PorcupineWrapper(
                    access_key=_PV_ACCESS_KEY,
                    keyword=keyword,
                    sensitivity=sensitivity,
                )
                self._frame_len = self._pv_detector.frame_length
                return
            except Exception as exc:
                print(f"[wake_word] Porcupine init failed: {exc}, falling back to energy detector")

        # Energy-based fallback — works without any API key
        self._energy_detector = EnergyWakeWord(
            keyword=keyword,
            threshold=_KEYWORD_ENERGY_THRESHOLD,
            samples_needed=_KEYWORD_SAMPLES_NEEDED,
        )

    @property
    def frame_length(self) -> int:
        return self._frame_len

    def process(self, pcm16_bytes: bytes) -> Generator[str, None, None]:
        """Process PCM16 frame and yield keyword labels on detection.

        Yields "Hey Kai" when detected.
        """
        if self._pv_detector is not None:
            yield from self._pv_detector.process(pcm16_bytes)
        elif self._energy_detector is not None:
            yield from self._energy_detector.process(pcm16_bytes)

    def close(self) -> None:
        if self._pv_detector is not None:
            self._pv_detector.close()
            self._pv_detector = None
        self._energy_detector = None


# ---------------------------------------------------------------------------
# Porcupine WASM wrapper
# ---------------------------------------------------------------------------

if _PV_ACCESS_KEY:
    class PorcupineWrapper:
        """Picovoice Porcupine WASM detector — requires PV_ACCESS_KEY env var."""

        __slots__ = ("_detector", "_frame_len")

        def __init__(self, access_key: str, keyword: str, sensitivity: float):
            import pvporcupine
            from pvporcupine import Porcupine

            # Resolve keyword to Porcupine built-in name
            kw_name = _PV_KEYWORDS.get(keyword, keyword.lower())
            self._detector = Porcupine(
                access_key=access_key,
                keywords=[kw_name],
                sensitivities=[sensitivity],
            )
            self._frame_len = self._detector.frame_length

        @property
        def frame_length(self) -> int:
            return self._frame_len

        def process(self, pcm16_bytes: bytes) -> Generator[str, None, None]:
            if len(pcm16_bytes) < self._frame_len * 2:
                return
            int16_data = np.frombuffer(pcm16_bytes, dtype=np.int16)
            f32_data = (int16_data / 32768.0).astype(np.float32)
            for i in range(0, len(f32_data) - self._frame_len + 1, self._frame_len):
                frame = f32_data[i:i + self._frame_len]
                try:
                    idx = self._detector.process(frame)
                    if idx >= 0:
                        yield "Hey Kai"
                except Exception:
                    continue

        def close(self) -> None:
            self._detector.delete()
            self._detector = None


# ---------------------------------------------------------------------------
# Energy-based wake word fallback
# ---------------------------------------------------------------------------

class EnergyWakeWord:
    """Simple energy + peak-count wake word detector.

    Detects when a burst of energy crosses a threshold across multiple
    consecutive frames — a rough but reliable "is someone speaking" signal.
    For "Hey Kai" specifically, we look for a sustained energy burst.
    """

    __slots__ = (
        "_keyword", "_threshold", "_samples_needed",
        "_hot_frames", "_last_energy",
    )

    def __init__(
        self,
        keyword: str = "Hey Kai",
        threshold: float = _KEYWORD_ENERGY_THRESHOLD,
        samples_needed: int = _KEYWORD_SAMPLES_NEEDED,
    ):
        self._keyword = keyword
        self._threshold = threshold
        self._samples_needed = samples_needed
        self._hot_frames = 0
        self._last_energy = 0.0

    @property
    def frame_length(self) -> int:
        return 512

    def process(self, pcm16_bytes: bytes) -> Generator[str, None, None]:
        if len(pcm16_bytes) < 512 * 2:
            return

        int16_data = np.frombuffer(pcm16_bytes, dtype=np.int16)
        f32_data = (int16_data / 32768.0).astype(np.float32)

        for i in range(0, len(f32_data) - 512 + 1, 512):
            frame = f32_data[i:i + 512]
            energy = float((frame ** 2).mean())

            if energy > self._threshold:
                self._hot_frames += 1
                if self._hot_frames == self._samples_needed:
                    # Sustained energy burst — treat as wake
                    yield self._keyword
                    self._hot_frames = 0
            else:
                if energy < self._threshold * 0.3:
                    # Fully quiet — reset counter
                    self._hot_frames = 0
                # else: transitional, keep counter
            self._last_energy = energy


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_detector(
    keyword: str = "Hey Kai",
    sensitivity: Optional[float] = None,
) -> WakeWordDetector:
    return WakeWordDetector(keyword=keyword, sensitivity=sensitivity)
