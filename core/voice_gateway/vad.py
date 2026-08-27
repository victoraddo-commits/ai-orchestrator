"""Silero VAD — voice activity detection via @ricky0123/vad-web.

The Silero VAD is a WebAssembly module that runs fully in-browser/CPU.
We use it to detect the end of speech (trailing silence) and enforce the
15-second hard cap.

Note: This module requires the vad package which wraps the WebAssembly module.
For the Phase 1 CLI test, we use a simpler energy-based fallback.
"""

from __future__ import annotations

import os
import struct
from typing import Generator, Optional

# The vad package (ricky0123/vad) wraps Silero VAD as a Python package
try:
    from vad import SileroVAD
    _VAD_AVAILABLE = True
except ImportError:
    _VAD_AVAILABLE = False


# Configuration
VAD_SAMPLE_RATE = 16000
VAD_THRESHOLD = float(os.environ.get("VAD_THRESHOLD", "0.5"))
VAD_MIN_SILENCE_MS = int(os.environ.get("VAD_MIN_SILENCE_MS", "800"))
VAD_SPEECH_PAD_MS = int(os.environ.get("VAD_SPEECH_PAD_MS", "300"))
VAD_HARD_CAP_MS = int(os.environ.get("VAD_HARD_CAP_MS", "15000"))


class VoiceActivityDetector:
    """Silero VAD wrapper for the Kai voice pipeline.

    Detects speech vs silence using Silero VAD running in Python (WebAssembly).
    Enforces trailing silence endpointing and the 15-second hard cap.
    """

    def __init__(
        self,
        threshold: float = VAD_THRESHOLD,
        min_silence_ms: int = VAD_MIN_SILENCE_MS,
        speech_pad_ms: int = VAD_SPEECH_PAD_MS,
        hard_cap_ms: int = VAD_HARD_CAP_MS,
        sample_rate: int = VAD_SAMPLE_RATE,
    ):
        self.threshold = threshold
        self.min_silence_samples = (min_silence_ms * sample_rate) // 1000
        self.speech_pad_samples = (speech_pad_ms * sample_rate) // 1000
        self.hard_cap_samples = (hard_cap_ms * sample_rate) // 1000
        self.sample_rate = sample_rate
        self._frame_size = sample_rate // 100  # 10ms frame for Silero

        if _VAD_AVAILABLE:
            self._vad = SileroVAD()
        else:
            self._vad = None

        # State
        self._silence_frames = 0
        self._speech_frames = 0
        self._total_frames = 0
        self._speaking = False
        self._ended = False

    @property
    def frame_size(self) -> int:
        return self._frame_size

    @property
    def is_speaking(self) -> bool:
        return self._speaking

    @property
    def is_ended(self) -> bool:
        return self._ended

    def process(self, pcm16_bytes: bytes) -> Generator[str, None, None]:
        """Process PCM16 16kHz mono frames and yield events.

        Yields:
            "speech_start" — first detected speech frame
            "speech_end"  — trailing silence reached or hard cap hit
        """
        import numpy as np

        if self._ended:
            return

        # Convert PCM16 to float32
        int16_data = np.frombuffer(pcm16_bytes, dtype=np.int16)
        f32_data = (int16_data / 32768.0).astype(np.float32)

        # Process in frame-sized chunks
        for i in range(0, len(f32_data), self._frame_size):
            frame = f32_data[i:i + self._frame_size]
            if len(frame) < self._frame_size:
                break

            self._total_frames += len(frame)

            # Hard cap check
            if self._total_frames >= self.hard_cap_samples:
                self._ended = True
                yield "speech_end"
                return

            # VAD check
            if self._vad is not None:
                prob = self._vad.predict(frame)
                has_speech = prob > self.threshold
            else:
                # Energy-based fallback
                energy = float((frame ** 2).mean())
                has_speech = energy > 0.01

            if has_speech:
                self._silence_frames = 0
                if not self._speaking:
                    self._speaking = True
                    yield "speech_start"
            else:
                if self._speaking:
                    self._silence_frames += len(frame)
                    if self._silence_frames >= self.min_silence_samples:
                        self._ended = True
                        yield "speech_end"
                        return

    def reset(self) -> None:
        """Reset state for a new turn."""
        self._silence_frames = 0
        self._speech_frames = 0
        self._total_frames = 0
        self._speaking = False
        self._ended = False

    def close(self) -> None:
        if self._vad is not None:
            # Silero VAD doesn't have a close() in its Python wrapper
            self._vad = None


def create_vad(
    threshold: float = VAD_THRESHOLD,
    min_silence_ms: int = VAD_MIN_SILENCE_MS,
    hard_cap_ms: int = VAD_HARD_CAP_MS,
) -> VoiceActivityDetector:
    return VoiceActivityDetector(
        threshold=threshold,
        min_silence_ms=min_silence_ms,
        hard_cap_ms=hard_cap_ms,
    )
