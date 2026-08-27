"""Piper TTS client — local CPU synthesis for acknowledgements.

Piper is a fast, local neural-TTS that runs on CPU. We use it for all
acknowledgement phrases ("I'm listening", "Working on it", etc.) because
it streams near-instantaneously with no cold-start.

Substantive answers use ElevenLabs via elevenlabs_client.py instead.

The piper-tts Python package must be installed.
A .onnx model is required; we look in PIPER_MODEL_DIR or use a direct path.

Setup:
  # Download a voice model (~61MB)
  mkdir -p ~/.local/share/piper/voices
  wget -q "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx" \
    -O ~/.local/share/piper/voices/en_US-lessac-medium.onnx
  wget -q "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json" \
    -O ~/.local/share/piper/voices/en_US-lessac-medium.onnx.json

  # Or use the download helper
  PIPER_MODEL_DIR=~/.local/share/piper/voices python -c \
    "from core.voice_gateway.piper_client import ensure_model; ensure_model()"
"""

from __future__ import annotations

import asyncio
import os
import wave
from pathlib import Path
from typing import AsyncIterator, Iterable

from piper.config import SynthesisConfig
from piper.voice import AudioChunk, PiperVoice

VOICE_MODEL = os.environ.get("PIPER_VOICE_MODEL", "en_US-lessac-medium.onnx")
VOICE_CONFIG = os.environ.get("PIPER_VOICE_CONFIG", "en_US-lessac-medium.onnx.json")
PIPER_MODEL_DIR = Path(os.environ.get("PIPER_MODEL_DIR", "/root/.local/share/piper/voices"))
PIPER_HTTP_PORT = int(os.environ.get("PIPER_HTTP_PORT", "5180"))

# Acknowledgement phrases — pre-determined short texts routed to Piper
ACKNOWLEDGEMENT_PHRASES = {
    "okay", "ok", "sure", "thanks", "thank you", "got it", "yes", "no",
    "one second", "just a moment", "hold on", "working on it", "processing",
    "i'm listening", "go ahead", "what", "who", "where", "when", "why",
    "how", "hmm", "huh", "nice", "cool", "great",
}


def is_acknowledgement(text: str) -> bool:
    """Return True if text is short and likely an acknowledgement."""
    if not text:
        return True
    t = text.strip().lower()
    words = t.split()
    if len(words) <= 2 and len(t) < 30:
        return True
    if t in ACKNOWLEDGEMENT_PHRASES:
        return True
    return False


def _build_voice() -> PiperVoice:
    """Load and return a PiperVoice instance for the configured model."""
    model_path = PIPER_MODEL_DIR / VOICE_MODEL
    if not model_path.exists():
        raise FileNotFoundError(
            f"Piper voice model not found at {model_path}. "
            f"Set PIPER_MODEL_DIR or run the download helper in this module."
        )
    return PiperVoice.load(str(model_path))


def synthesize(text: str) -> Iterable[AudioChunk]:
    """Synchronous synthesis — yields AudioChunk objects.

    Each AudioChunk has .audio_int16_array (numpy.ndarray) and .audio_int16_bytes.
    We yield raw PCM16 bytes, one chunk per sentence fragment.
    """
    voice = _build_voice()
    syn_config = SynthesisConfig()
    for chunk in voice.synthesize(text, syn_config):
        yield chunk


async def synthesize_stream(text: str) -> AsyncIterator[bytes]:
    """Async generator — yields raw PCM16 16kHz mono audio bytes.

    Loads the model, runs synthesis synchronously in a thread pool since
    Piper's synthesize() is CPU-bound, and yields chunks as they complete.
    """
    def _gen() -> Iterable[AudioChunk]:
        voice = _build_voice()
        syn_config = SynthesisConfig()
        return voice.synthesize(text, syn_config)

    loop = asyncio.get_running_loop()
    # Run CPU-bound synthesis in a thread so we don't block the event loop
    chunks: Iterable[AudioChunk] = await loop.run_in_executor(None, _gen)
    for chunk in chunks:
        yield chunk.audio_int16_bytes


async def synthesize_to_wav(text: str, output_path: Path) -> Path:
    """Synthesize text to a 16kHz mono WAV file."""
    voice = _build_voice()
    syn_config = SynthesisConfig()

    loop = asyncio.get_running_loop()

    def _run() -> None:
        with wave.open(str(output_path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)  # 16-bit
            wav_file.setframerate(16000)
            for chunk in voice.synthesize(text, syn_config):
                wav_file.writeframes(chunk.audio_int16_bytes)

    await loop.run_in_executor(None, _run)
    return output_path


# ---------------------------------------------------------------------------
# Model download helper
# ---------------------------------------------------------------------------

def ensure_model(voice: str = "en_US-lessac-medium") -> None:
    """Download a Piper voice model if not already present in PIPER_MODEL_DIR."""
    model_dir = PIPER_MODEL_DIR
    model_path = model_dir / f"{voice}.onnx"
    if model_path.exists():
        return

    model_dir.mkdir(parents=True, exist_ok=True)

    try:
        from piper.download_voices import download_voice
        def progress_callback(progress: "piper.download_voices.PiperDownloadProgress") -> None:
            print(f"  Downloading {voice}: {progress.progress:.0%}")
        download_voice(voice, model_dir, progress_callback=progress_callback)
    except Exception as exc:
        print(f"piper.download_voices failed: {exc}")
        print(f"  Manually download from:")
        print(f"  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx")
        print(f"  → {model_path}")
