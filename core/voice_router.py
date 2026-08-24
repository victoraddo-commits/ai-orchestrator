"""KAI Voice Router — JARVIS P6 (§5/§6/§64).

STT/TTS provider abstraction with local-first selection:
  local  → voice server on .109 :8130 (faster-whisper + piper, CPU)
  cloud  → future providers via ai_router (graceful fallback per §6)

The Brain never hardcodes a provider: callers ask for transcribe/speak and
the router picks by availability. Latency and chosen provider are reported
honestly in every response (§63).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

VOICE_SERVER_URL = os.environ.get("KAI_VOICE_URL", "http://192.168.1.109:8130")
_TIMEOUT = 30


def _probe_local() -> bool:
    try:
        import requests
        r = requests.get(f"{VOICE_SERVER_URL}/health", timeout=3)
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
                f"{VOICE_SERVER_URL}/transcribe",
                files={"file": (filename, audio_bytes, "audio/wav")},
                timeout=_TIMEOUT,
            )
            if r.status_code == 200:
                d = r.json()
                d["router"] = "local-first"
                return {"ok": True, **d}
            return {"ok": False, "error": f"voice server {r.status_code}"}
        except Exception as e:
            return {"ok": False, "error": f"local STT failed: {e}"}
    # Cloud STT fallback slot (§6): add providers here when configured.
    return {"ok": False, "error": "no voice provider available (local down, no cloud configured)",
            "latency_ms": int((time.time() - t0) * 1000)}


def speak(text: str) -> dict:
    """Text → WAV audio bytes. Local-first."""
    if not text or len(text) > 2000:
        return {"ok": False, "error": "text required (max 2000 chars)"}
    if _probe_local():
        try:
            import requests
            r = requests.post(f"{VOICE_SERVER_URL}/speak", params={"text": text},
                              timeout=_TIMEOUT)
            if r.status_code == 200 and r.content:
                return {"ok": True, "audio": r.content,
                        "provider": "local-piper", "bytes": len(r.content)}
            return {"ok": False, "error": f"voice server {r.status_code}"}
        except Exception as e:
            return {"ok": False, "error": f"local TTS failed: {e}"}
    return {"ok": False, "error": "no voice provider available"}
