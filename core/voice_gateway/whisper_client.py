"""faster-whisper STT client — CPU-bound, small.en model.

Emits (text, is_final) tuples via a generator interface so the pipeline
can forward partials upstream without waiting for a final transcription.
"""

from __future__ import annotations

import io
import time
from typing import Generator, Optional

# faster-whisper is installed system-wide
from faster_whisper import WhisperModel


# Model size: small.en is ~500MB and accurate for English homelab use.
# Run in CPU (compute_type="int8") to avoid GPU contention with the AI work.
_MODEL: Optional[WhisperModel] = None


def _load_model() -> WhisperModel:
    global _MODEL
    if _MODEL is None:
        # Download + load small.en on first use (~500MB, cached by huggingface)
        _MODEL = WhisperModel(
            "small.en",
            device="cpu",
            compute_type="int8",
        )
    return _MODEL


def transcribe(
    pcm16_bytes: bytes,
    sample_rate: int = 16000,
) -> Generator[tuple[str, bool], None, None]:
    """Transcribe PCM16 16kHz mono audio.

    Yields (text, is_final) tuples:
      - is_final=False for streaming partial results (if model supports)
      - is_final=True for the final transcription of this chunk

    Raises:
        RuntimeError: if transcription fails
    """
    model = _load_model()

    # Wrap raw PCM in a WAV-like bytes buffer for the decoder
    # faster-whisper accepts raw PCM with language/hotword hints
    audio = _pcm16_to_f32le_array(pcm16_bytes, sample_rate)

    try:
        segments, info = model.transcribe(
            audio,
            language="en",
            vad_filter=False,          # VAD is handled separately in pipeline.py
            initial_prompt=None,
            condition_on_previous_text=False,
        )
        for segment in segments:
            text = segment.text.strip()
            if text:
                yield (text, True)
    except Exception as exc:
        raise RuntimeError(f"faster-whisper transcription failed: {exc}") from exc


def _pcm16_to_f32le_array(pcm_bytes: bytes, sample_rate: int):
    """Convert PCM16 signed-integer bytes to float32 numpy array."""
    import numpy as np
    # Read as signed 16-bit integers
    int16_data = np.frombuffer(pcm_bytes, dtype=np.int16)
    # Convert to float32 in [-1.0, 1.0]
    f32_data = int16_data.astype(np.float32) / 32768.0
    return f32_data


def quick_transcribe(pcm16_bytes: bytes) -> str:
    """One-shot transcription — returns the full text as a string."""
    results = list(transcribe(pcm16_bytes))
    if not results:
        return ""
    return " ".join(text for text, _ in results)
