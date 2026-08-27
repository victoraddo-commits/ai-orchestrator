"""Picovoice Porcupine wake word detector — WASM, CPU-only.

Detects "Hey Kai" (and "Kai, stop" for hard interrupt) using the Picovoice
Porcupine WASM engine. No GPU required, runs in-process.

The .pv model file is required; it is bundled with the pvporcupine package.
"""

from __future__ import annotations

import os
import struct
from pathlib import Path
from typing import Generator, Optional

# Porcupine is installed system-wide
import pvporcupine
from pvporcupine import Porcupine


# Keyword IDs — hardcoded for the bundled keywords
KEYWORD_HELLO_KAI = "Hey Kai"
KEYWORD_STOP = "Kai, stop"

# Default sensitivity — can be overridden via env
_SENSITIVITY = float(os.environ.get("PORCUPINE_SENSITIVITY", "0.7"))


class WakeWordDetector:
    """In-process Picovoice Porcupine detector.

    Processes raw PCM16 16kHz mono audio frames and yields keyword labels
    when a wake word is detected.
    """

    def __init__(
        self,
        keyword: str = KEYWORD_HELLO_KAI,
        sensitivity: Optional[float] = None,
    ):
        self.keyword = keyword
        sens = sensitivity if sensitivity is not None else _SENSITIVITY

        # Porcupine comes with bundled keyword models
        # Access them via the package data
        import pvporcupine
        package_dir = Path(pvporcupine.__file__).parent
        keyword_paths = {
            KEYWORD_HELLO_KAI: package_dir / "lib/common/hey_kai-linux-x86_64.pv",
            KEYWORD_STOP: package_dir / "lib/common/kai_stop-linux-x86_64.pv",
        }

        keyword_path = keyword_paths.get(keyword)
        if not keyword_path or not keyword_path.exists():
            # Fall back to built-in keyword detection
            if keyword == KEYWORD_HELLO_KAI:
                keywords = ["Hey Kai"]
            else:
                keywords = ["Kai, stop"]
            self._detector: Optional[Porcupine] = Porcupine(
                keywords=keywords,
                sensitivities=[sens],
            )
        else:
            self._detector = Porcupine(
                keyword_paths=[str(keyword_path)],
                sensitivities=[sens],
            )

        self._frame_len = self._detector.frame_length  # typically 512

    @property
    def frame_length(self) -> int:
        return self._frame_len

    def process(self, pcm16_bytes: bytes) -> Generator[str, None, None]:
        """Process a PCM16 frame and yield keyword labels on detection.

        Args:
            pcm16_bytes: Raw PCM16 signed-int 16kHz mono audio, exactly
                         frame_length * 2 bytes.

        Yields:
            Keyword label on detection, e.g. "Hey Kai"
        """
        import numpy as np

        if len(pcm16_bytes) < self._frame_len * 2:
            return

        int16_data = np.frombuffer(pcm16_bytes, dtype=np.int16)
        f32_data = (int16_data / 32768.0).astype(np.float32)

        # Process in chunks of frame_length
        for i in range(0, len(f32_data) - self._frame_len + 1, self._frame_len):
            frame = f32_data[i:i + self._frame_len]
            try:
                keyword_idx = self._detector.process(frame)
                if keyword_idx >= 0:
                    yield self.keyword
            except Exception:
                # Skip frames that cause processing errors
                continue

    def close(self) -> None:
        if self._detector is not None:
            self._detector.delete()
            self._detector = None


def create_detector(
    keyword: str = KEYWORD_HELLO_KAI,
    sensitivity: Optional[float] = None,
) -> WakeWordDetector:
    return WakeWordDetector(keyword=keyword, sensitivity=sensitivity)
