"""Thin, plain text-completion clients for non-agentic providers.

Deliberately NOT a second coding engine: these are single request/response
chat-completion calls only, no tool use, no file access, no agent loop.
Only Claude (via core.coding_bridge / CloudCLI's Agent SDK) can write files
or run commands -- these exist for the text-in/text-out roles (review,
planning, docs, log analysis) the Phase 12J AI-team model assigns them.
"""

import threading

import requests

import core.ai.provider_health as provider_health


# ── Per-call token usage capture (cost tracker feed) ─────────────────────────
# Every provider response carries a token-usage block, but each API shapes it
# differently: OpenAI-compatible /v1 uses "usage", Gemini uses "usageMetadata",
# and ollama's native /api/generate reports eval counts. The 13W contract keeps
# client return values as plain text strings, so instead of changing every
# return shape the capture happens here in one place -- _post_json stashes the
# usage dict for the current call on a thread-local (delegate() runs each
# provider attempt on its own thread), and ai_router.record_usage picks it up
# via pop_last_usage() right after run_fn returns. Ollama clients stash their
# own counts since they bypass _post_json. Nothing here fabricates numbers:
# absent usage data simply leaves the thread-local empty.
_last_call_usage = threading.local()


def pop_last_usage():
    """Return this thread's last captured usage dict, or None; clears it."""
    value = getattr(_last_call_usage, "value", None)
    _last_call_usage.value = None
    if isinstance(value, dict) and (value.get("prompt_tokens") or value.get("completion_tokens")):
        return value
    return None


class ProviderUnavailable(Exception):
    """Raised when a provider's API key isn't configured."""


# ── Local ollama (qwen2.5:7b) on Proxmox B ────────────────────────────
# Deployed 2026-08-11 after benchmark Phases 1-11: qwen2.5:7b beat
# llama3.2:3b and llama3.1:8b on speed, code quality, JSON output,
# reliability, hallucination resistance, and long-context extraction.
# The ollama API is reachable via LAN on Proxmox B (192.168.1.109).
# No API key needed — ollama runs unauthenticated on the local network.
OLLAMA_BASE_URL = "http://192.168.1.109:11434"
OLLAMA_MODEL = "qwen2.5:7b"

# ── Concurrency guard — prevents NVMe I/O saturation on Proxmox B ────
# Benchmark 2026-08-11 proved: >6 concurrent ollama workers → NVMe saturation
# → host crash. We cap at 4 (conservative) to leave headroom for the ZT agent,
# SSH, and NFS. Both qwen and llama share this pool since ollama serializes
# per-model anyway; the real limit is total I/O pressure on the Samsung 970 EVO.
OLLAMA_MAX_CONCURRENT = 4
_ollama_semaphore = threading.BoundedSemaphore(OLLAMA_MAX_CONCURRENT)


def check_ollama_available(timeout=5):
    """Lightweight availability check — true if ollama is responding."""
    try:
        r = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=timeout)
        return r.status_code == 200
    except requests.RequestException:
        return False


def call_ollama_qwen(prompt, model=OLLAMA_MODEL, timeout=120):
    """Call qwen2.5:7b via ollama on Proxmox B (LAN at 192.168.1.109:11434).

    Uses ollama's /api/generate endpoint (not OpenAI-compatible) with
    stream=false. Returns just the response text. Raises ProviderUnavailable
    if the ollama server can't be reached.
    """
    if not check_ollama_available():
        raise ProviderUnavailable(
            f"Ollama ({OLLAMA_BASE_URL}) is not reachable — is Proxmox B online?"
        )

    with _ollama_semaphore:
        try:
            response = requests.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={"model": model, "prompt": prompt, "stream": False},
                timeout=timeout,
            )
            response.raise_for_status()
            data = response.json()
            # ollama's native /api/generate reports token counts as
            # prompt_eval_count/eval_count (not an OpenAI usage block).
            _last_call_usage.value = {
                "prompt_tokens": int(data.get("prompt_eval_count") or 0),
                "completion_tokens": int(data.get("eval_count") or 0),
            }
            return data.get("response", "")
        except requests.RequestException as error:
            provider_health.capture_provider_error("ollama_qwen", detail=str(error)[:300])
            raise RuntimeError(f"ollama_qwen request failed: {type(error).__name__}") from None


OLLAMA_LLAMA_MODEL = "llama3.2:3b"


def call_ollama_llama(prompt, model=OLLAMA_LLAMA_MODEL, timeout=120):
    """Call llama3.2:3b via ollama on Proxmox B — faster but less accurate.

    Deployed 2026-08-11 alongside qwen2.5:7b. llama3.2:3b is smaller (2.0GB),
    faster inference, and better at format compliance (no markdown fences).
    Weaker at hallucination resistance and long-context extraction than qwen.
    Good for simple classification, quick lookups, and low-latency tasks.
    """
    if not check_ollama_available():
        raise ProviderUnavailable(
            f"Ollama ({OLLAMA_BASE_URL}) is not reachable — is Proxmox B online?"
        )

    with _ollama_semaphore:
        try:
            response = requests.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={"model": model, "prompt": prompt, "stream": False},
                timeout=timeout,
            )
            response.raise_for_status()
            data = response.json()
            _last_call_usage.value = {
                "prompt_tokens": int(data.get("prompt_eval_count") or 0),
                "completion_tokens": int(data.get("eval_count") or 0),
            }
            return data.get("response", "")
        except requests.RequestException as error:
            provider_health.capture_provider_error("ollama_llama", detail=str(error)[:300])
            raise RuntimeError(f"ollama_llama request failed: {type(error).__name__}") from None

# === OLLAMA LOCAL MODEL CONFIGURATION ===
# Added 2026-09-09: VM 104 Ollama server

OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:7b"

def call_ollama(prompt, max_tokens=2048, temperature=0.7, model=None, cognitive_role=None, timeout=300):
    """Call Ollama server on VM 104 with cognitive routing support.

    Args:
        prompt: Text prompt
        max_tokens: Maximum tokens to generate
        temperature: Sampling temperature
        model: Explicit model name (e.g., "qwen2.5:7b", "kai-brain:27b")
        cognitive_role: Route via cognitive router (e.g., "reasoning", "coding", "fast")
                       Takes precedence over model parameter

    Returns:
        Generated text string
    """
    import requests

    # Determine which model to use
    target_model = OLLAMA_MODEL  # Default: qwen2.5:7b

    if cognitive_role:
        # Use cognitive router
        try:
            from core.ai.cognitive_router import get_model_for_role
            target_model = get_model_for_role(cognitive_role)
        except (ImportError, Exception):
            # Fallback to default if cognitive router unavailable
            pass
    elif model:
        # Explicit model override
        target_model = model

    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": target_model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "num_predict": max_tokens,
                    "temperature": temperature,
                }
            },
            timeout=timeout
        )
        response.raise_for_status()
        return response.json()["response"]
    except Exception as e:
        raise RuntimeError(f"Ollama server unavailable: {e}")

# Compatibility aliases
local_brain = call_ollama
local_brain_fast = call_ollama
local_brain_coder = call_ollama
