"""Silero VAD — voice activity detection via faster-whisper's SileroVADModel.

Silero VAD detects speech vs silence to enforce trailing silence endpointing
and the 15-second hard cap. Uses faster-whisper's bundled ONNX model.

Configuration (env):
  VAD_THRESHOLD=0.5         — speech probability threshold (0.0–1.0)
  VAD_MIN_SILENCE_MS=800    — trailing silence before speech_end
  VAD_SPEECH_PAD_MS=300     — prepend this much audio before detected speech start
  VAD_HARD_CAP_MS=15000     — hard cap on total speech duration
"""

from __future__ import annotations

import os
from typing import Generator

import numpy as np


# Configuration
VAD_SAMPLE_RATE = 16000
VAD_THRESHOLD = float(os.environ.get("VAD_THRESHOLD", "0.5"))
VAD_MIN_SILENCE_MS = int(os.environ.get("VAD_MIN_SILENCE_MS", "800"))
VAD_SPEECH_PAD_MS = int(os.environ.get("VAD_SPEECH_PAD_MS", "300"))
VAD_HARD_CAP_MS = int(os.environ.get("VAD_HARD_CAP_MS", "15000"))


def _load_silero():
    """Lazily load the faster-whisper SileroVADModel."""
    from faster_whisper.vad import SileroVADModel
    from pathlib import Path
    paths = [
        Path("/usr/local/lib/python3.12/dist-packages/faster_whisper/assets/silero_vad_v6.onnx"),
        Path("/usr/local/lib/python3.12/site-packages/faster_whisper/assets/silero_vad_v6.onnx"),
    ]
    for p in paths:
        if p.exists():
            return SileroVADModel(str(p))
    raise FileNotFoundError("Silero VAD ONNX model not found in faster-whisper assets")


class VoiceActivityDetector:
    """Silero VAD wrapper for the Kai voice pipeline.

    Uses faster-whisper's SileroVADModel to detect speech vs silence.
    Enforces trailing silence endpointing and the 15-second hard cap.

    Processes audio in chunks of 512 samples (32ms at 16kHz).
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
        self._frame_size = 512  # 32ms at 16kHz

        self._vad = _load_silero()

        # State
        self._silence_frames = 0
        self._total_samples = 0
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
        """Process PCM16 16kHz mono audio and yield events.

        Yields:
            "speech_start" — first detected speech frame
            "speech_end"   — trailing silence reached, hard cap hit, or silence
        """
        if self._ended:
            return

        int16_data = np.frombuffer(pcm16_bytes, dtype=np.int16)
        f32_data = (int16_data / 32768.0).astype(np.float32)

        # Run Silero VAD on full audio — returns speech probs per 512-sample frame
        try:
            probs = self._vad(f32_data)  # shape: (num_frames, 1)
        except Exception:
            # Fallback: energy-based detection
            probs = np.array([[1.0 if (f32_data[i:i+512]**2).mean() > 0.005 else 0.0]
                               for i in range(0, len(f32_data), 512)], dtype=np.float32)

        # probs is (num_frames, 1) — squeeze to 1D
        probs = probs.ravel()

        num_frames = len(probs)
        samples_per_frame = self.sample_rate // 100  # 160 at 16kHz (10ms frames... no, 512 samples = 32ms)

        for frame_idx, prob in enumerate(probs):
            frame_samples = frame_idx * 512
            self._total_samples += min(512, len(f32_data) - frame_samples)
            if len(f32_data) - frame_samples < 256:
                break

            # Hard cap check
            if self._total_samples >= self.hard_cap_samples:
                self._ended = True
                yield "speech_end"
                return

            has_speech = float(prob) > self.threshold

            if has_speech:
                self._silence_frames = 0
                if not self._speaking:
                    self._speaking = True
                    yield "speech_start"
            else:
                if self._speaking:
                    self._silence_frames += 512
                    if self._silence_frames >= self.min_silence_samples:
                        self._ended = True
                        yield "speech_end"
                        return

    def reset(self) -> None:
        """Reset state for a new turn."""
        self._silence_frames = 0
        self._total_samples = 0
        self._speaking = False
        self._ended = False

    def close(self) -> None:
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
