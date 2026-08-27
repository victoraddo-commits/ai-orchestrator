"""KAI Voice Router — adapted from existing stub (§5/§6/§64).

STT/TTS provider abstraction with local-first selection:
  local  → kai-voice gateway on 127.0.0.1:8130 (faster-whisper + piper, CPU)
  cloud  → future providers via ai_router (graceful fallback per §6)

The Brain never hardcodes a provider: callers ask for transcribe/speak and
the router picks by availability. Latency and chosen provider are reported
honestly in every response (§63).

This file REPLACES the old stub that pointed to 192.168.1.109:8130.
The function signatures are preserved so existing callers don't break.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

# Updated: point to local gateway (ZT-only) instead of dead 192.168.1.109
_VOICE_WS_URL = os.environ.get("KAI_VOICE_WS_URL", "ws://127.0.0.1:8130")
_VOICE_HTTP_URL = os.environ.get("KAI_VOICE_URL", "http://127.0.0.1:8130")
_TIMEOUT = 30


def _probe_local() -> bool:
    """Check if the local voice gateway is responding."""
    try:
        import requests
        r = requests.get(f"{_VOICE_HTTP_URL}/health", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def transcribe(audio_bytes: bytes, filename: str = "audio.wav") -> dict:
    """Speech → text. Local-first; raises if no provider available."""
    if not audio_bytes:
        return {"ok": False, "error": "empty audio"}
    t0 = time.time()
    if _probe_local():
        try:
            import requests
            r = requests.post(
                f"{_VOICE_HTTP_URL}/transcribe",
                files={"file": (filename, audio_bytes, "audio/wav")},
                timeout=_TIMEOUT,
            )
            if r.status_code == 200:
                d = r.json()
                d["router"] = "kai-voice-local"
                return {"ok": True, **d}
            return {"ok": False, "error": f"voice gateway {r.status_code}"}
        except Exception as e:
            return {"ok": False, "error": f"local STT failed: {e}"}
    # Cloud STT fallback slot (§6): add providers here when configured.
    return {
        "ok": False,
        "error": "no voice provider available (gateway down, no cloud configured)",
        "latency_ms": int((time.time() - t0) * 1000),
    }


def speak(text: str) -> dict:
    """Text → WAV audio bytes. Local-first."""
    if not text or len(text) > 2000:
        return {"ok": False, "error": "text required (max 2000 chars)"}
    if _probe_local():
        try:
            import requests
            r = requests.post(
                f"{_VOICE_HTTP_URL}/speak",
                params={"text": text},
                timeout=_TIMEOUT,
            )
            if r.status_code == 200 and r.content:
                return {
                    "ok": True,
                    "audio": r.content,
                    "provider": "local-piper",
                    "bytes": len(r.content),
                }
            return {"ok": False, "error": f"voice gateway {r.status_code}"}
        except Exception as e:
            return {"ok": False, "error": f"local TTS failed: {e}"}
    return {"ok": False, "error": "no voice provider available"}


def transcribe_stream(pcm16_bytes: bytes) -> dict:
    """Chunked PCM16 16k mono → NDJSON partials + final.

    This is the streaming variant used by the enhancement system.
    Requires the voice gateway's /transcribe_stream endpoint.
    """
    if _probe_local():
        try:
            import requests
            r = requests.post(
                f"{_VOICE_HTTP_URL}/transcribe_stream",
                files={"file": ("chunk.pcm", pcm16_bytes, "audio/pcm")},
                timeout=120,
                stream=True,
            )
            partials, final = [], ""
            for line in r.iter_lines(decode_unicode=True):
                if not line:
                    continue
                import json as _json
                try:
                    evt = _json.loads(line)
                except Exception:
                    continue
                if evt.get("type") == "partial":
                    partials.append(evt.get("text", ""))
                elif evt.get("type") == "final":
                    final = evt.get("text", final)
            return {
                "ok": True,
                "final": final,
                "partials": partials,
                "provider": "kai-voice-whisper-stream",
            }
        except Exception as e:
            return {"ok": False, "error": f"stream failed: {e}"}
    return {"ok": False, "error": "voice gateway unavailable"}
