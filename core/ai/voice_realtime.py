"""voice_realtime — low-latency voice routing class for Kai.

A standalone async router (NOT a registered ai_provider) that reuses the
existing provider functions to deliver fast text responses for the voice
pipeline.

Circuit breaker: 2 consecutive failures trips this breaker open.
Timeout: 3s per attempt.
Chain: groq → deepseek_native_flash → qwen4_text → gemini → local (ollama)
Free pool is excluded explicitly.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Optional

from core import ai_provider
from core.ai import circuit_breaker

# Voice-specific provider chain — free pool excluded
VOICE_CHAIN = [
    "groq",
    "deepseek_native_flash",
    "qwen4_text",
    "gemini",
    "local",
]

# 3s per-attempt timeout for voice turns
_ATTEMPT_TIMEOUT = 3.0

# 2-failure trip for voice (vs standard 3)
_VOICE_BREAKER_THRESHOLD = 2

# Acknowledgement phrases handled locally by Piper — never routed
_ACKNOWLEDGEMENTS = {
    "okay", "ok", "thanks", "thank you", "got it", "sure", "yes", "no",
    "one second", "just a moment", "hold on", "working on it", "processing",
    "i'm listening", "go ahead", "what", "who", "where", "when", "why",
    "how", "hmm", "huh",
}


def _init_breaker() -> None:
    """Set the voice_realtime breaker threshold at import time."""
    try:
        circuit_breaker.set_threshold("voice_realtime", _VOICE_BREAKER_THRESHOLD)
    except Exception:
        # Non-fatal — breaker uses defaults if this fails
        pass


_init_breaker()


def is_acknowledgement(text: str) -> bool:
    """Return True if text is short enough to be an acknowledgement."""
    if not text:
        return True
    t = text.strip().lower()
    if len(t) < 3:
        return True
    if t in _ACKNOWLEDGEMENTS:
        return True
    # Single short word
    if len(t.split()) == 1 and len(t) < 8:
        return True
    return False


async def delegate_voice_turn(
    text: str,
    session_id: str = "",
) -> tuple[str, str, int]:
    """Route a voice turn through the provider chain.

    Returns:
        (response_text, provider_name, elapsed_ms)

    Raises:
        RuntimeError: if no provider in the chain succeeds
    """
    elapsed_total = 0

    for provider_name in VOICE_CHAIN:
        # Check the breaker first
        if circuit_breaker.is_open("voice_realtime"):
            break

        # Skip disabled providers silently
        if not ai_provider.get_provider_enabled(provider_name):
            continue

        # Skip the free pool explicitly (it's for background work)
        if provider_name == "free_coding":
            continue

        provider = ai_provider.get_provider(provider_name)
        if not provider:
            continue

        run_text_task = provider.get("run_text_task")
        if not run_text_task:
            continue

        t0 = time.monotonic()
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(run_text_task, text),
                timeout=_ATTEMPT_TIMEOUT,
            )
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            elapsed_total += elapsed_ms

            # Record success
            circuit_breaker.record_success("voice_realtime")

            # Extract text from response — all our providers return dict
            if isinstance(response, dict):
                reply = response.get("text") or response.get("response") or ""
            elif isinstance(response, str):
                reply = response
            else:
                reply = str(response)

            if reply:
                return reply.strip(), provider_name, elapsed_total

        except asyncio.TimeoutError:
            circuit_breaker.record_failure("voice_realtime")
            elapsed_total += int(_ATTEMPT_TIMEOUT * 1000)
            continue
        except Exception:
            circuit_breaker.record_failure("voice_realtime")
            elapsed_total += int((time.monotonic() - t0) * 1000)
            continue

    # All providers failed
    raise RuntimeError(
        f"No voice provider succeeded for session {session_id!r}. "
        f"Chain attempted: {VOICE_CHAIN}"
    )
