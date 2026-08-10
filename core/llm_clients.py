"""Thin, plain text-completion clients for non-agentic providers.

Deliberately NOT a second coding engine: these are single request/response
chat-completion calls only, no tool use, no file access, no agent loop.
Only Claude (via core.coding_bridge / CloudCLI's Agent SDK) can write files
or run commands -- these exist for the text-in/text-out roles (review,
planning, docs, log analysis) the Phase 12J AI-team model assigns them.
"""

import os

import requests

import core.ai.provider_health as provider_health


# gemini-2.0-flash and gemini-2.0-flash-lite both return 429 RESOURCE_EXHAUSTED
# (limit: 0) on this account's free tier -- confirmed live. gemini-flash-lite-latest
# is the model this key actually has real generateContent quota for.
GEMINI_DEFAULT_MODEL = "gemini-flash-lite-latest"
GROQ_DEFAULT_MODEL = "llama-3.3-70b-versatile"
OPENAI_DEFAULT_MODEL = "gpt-4o-mini"
# openai/gpt-4o-mini confirmed working live via OpenRouter. The free-tier
# models available on this account (google/gemma-4-*:free) returned 429
# "temporarily rate-limited upstream" when tested live -- unreliable for a
# fallback role, so defaulting to the cheap, confirmed-working paid model
# instead of a free one that wasn't actually available at test time.
OPENROUTER_DEFAULT_MODEL = "openai/gpt-4o-mini"
# 13M: the plain-text openrouter provider rotates across these instead of
# being pinned to the single default above (which stays as the back-compat
# default for direct call_openrouter callers). All 5 confirmed live against
# the real OpenRouter chat-completions API (2026-07-28 design session).
# deepseek/deepseek-v4-flash is first per explicit user directive
# (2026-07-30): prioritize it over the rest of this set. Claude Sonnet 4.6
# deliberately does NOT join this rotation -- its role is the coding
# fallback (openrouter_claude_* in core.ai_provider), keeping the
# cheap/plain-text role cheap.
OPENROUTER_MODELS = [
    "deepseek/deepseek-v4-flash",
    "openai/gpt-4o-mini",
    "z-ai/glm-5",
    "openai/gpt-5",
    "deepseek/deepseek-v4-pro",
]
# MiniMax-Text-01/M1 isn't supported on this account's plan ("your current
# token plan not support model"), but MiniMax-M2 is, and is confirmed
# working live against real API calls with the account's loaded credits
# (2026-07-28) -- an earlier "exhausted" reading was this account's
# temporary pre-topup state, not a code or model-choice problem.
MINIMAX_DEFAULT_MODEL = "MiniMax-M2"
# deepseek/deepseek-v4-pro confirmed live via OpenRouter using the dedicated
# DEEPSEEK_OPENROUTER_API_KEY (separate from the shared OPENROUTER_API_KEY
# the existing 'openrouter' provider uses) -- routing through OpenRouter
# rather than calling DeepSeek's own API directly, reusing the infrastructure
# already proven for gpt-4o-mini (same endpoint, same response format).
DEEPSEEK_DEFAULT_MODEL = "deepseek/deepseek-v4-pro"
# 13V: Claude Sonnet through OpenRouter's text-completion API, on the shared
# OPENROUTER_API_KEY -- a Claude-family fallback for the Chief Architect
# chain that survives the CloudCLI/Anthropic subscription's own quota. Kept
# as its own provider key (not a model swap on "openrouter") so
# provider_health tracks its quota/errors separately from the gpt-4o-mini
# route and ai_router can order the two independently.
OPENROUTER_CLAUDE_MODEL = "anthropic/claude-sonnet-4.6"


class ProviderUnavailable(Exception):
    """Raised when a provider's API key isn't configured."""


# Provider-key → secrets-store lookup mapping.  When _require_key() is called
# with an env_var, we check whether a corresponding provider has a secret stored
# (provider_secrets.json).  If so, the stored key is used instead of the env var.
_ENV_TO_SECRETS_PROVIDER = {
    "DEEPSEEK_NATIVE_PRO_API_KEY": "deepseek",
    "DEEPSEEK_NATIVE_FLASH_API_KEY": "deepseek",
    "OPENROUTER_API_KEY": "openrouter",
    "DEEPSEEK_OPENROUTER_API_KEY": "deepseek",
    "GEMINI_API_KEY": "gemini",
    "GEMINIX_API_KEY": "geminix",
    "GROQ_API_KEY": "groq",
    "MINIMAX_API_KEY": "minimax",
}


def _require_key(env_var, provider_key=None):
    # 1. Secrets store (production path — keys never in env/process memory)
    pk = provider_key or _ENV_TO_SECRETS_PROVIDER.get(env_var)
    if pk:
        try:
            from core.ai.secrets import get_api_key
            stored = get_api_key(pk)
            if stored:
                return stored
        except ImportError:
            pass

    # 2. Environment variable (legacy fallback)
    key = os.getenv(env_var)
    if not key:
        raise ProviderUnavailable(f"{env_var} is not set and no secret stored for {pk or 'unknown'}")
    return key


def _post_json(provider_key, url, **kwargs):
    # Redact broadly on any request failure: never let the key value end up
    # in a raised exception message (e.g. via a requests error that echoes
    # request internals) -- re-raise with just the exception type, not the
    # original exception object.
    detail = None
    try:
        response = requests.post(url, **kwargs)

        if response.status_code >= 400:
            # Diagnostic signal is useful even on failure -- capture it before
            # raising, using whatever detail the provider's own error body
            # gives (never the request itself, so this can't leak the key).
            try:
                detail = response.json()
            except ValueError:
                detail = response.text[:300]
            detail = str(detail)[:300]

            # 402 (e.g. OpenRouter "requires more credits") is functionally
            # the same as 429 for routing purposes -- this candidate has no
            # headroom right now, so treat both as quota_exceeded (skippable
            # by ai_router.delegate()) rather than the ambiguous "error"
            # status, which delegate() deliberately keeps retrying every call.
            if response.status_code in (429, 402):
                provider_health.capture_quota_exceeded(provider_key, detail=detail)
            else:
                provider_health.capture_provider_error(provider_key, detail=detail)

        response.raise_for_status()

        provider_health.capture_from_response_headers(provider_key, response.headers)

        return response.json()
    except requests.RequestException as error:
        message = f"{provider_key} request failed: {type(error).__name__}"
        if detail:
            message += f" ({detail})"
        raise RuntimeError(message) from None


def call_gemini(prompt, model=GEMINI_DEFAULT_MODEL, timeout=60):
    key = _require_key("GEMINI_API_KEY")

    data = _post_json(
        "gemini",
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": key},
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=timeout,
    )
    return data["candidates"][0]["content"]["parts"][0]["text"]


# 2026-08-02: second Gemini account ("GeminiX", operator directive) -- same
# API/model/request shape as call_gemini above, different key and a distinct
# "geminix" provider_key so provider_health tracks its quota separately
# (different account, can be healthy/exhausted independently of "gemini").
def call_geminix(prompt, model=GEMINI_DEFAULT_MODEL, timeout=60):
    key = _require_key("GEMINIX_API_KEY")

    data = _post_json(
        "geminix",
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": key},
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=timeout,
    )
    return data["candidates"][0]["content"]["parts"][0]["text"]


def call_groq(prompt, model=GROQ_DEFAULT_MODEL, timeout=60):
    key = _require_key("GROQ_API_KEY")

    data = _post_json(
        "groq",
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model, "messages": [{"role": "user", "content": prompt}]},
        timeout=timeout,
    )
    return data["choices"][0]["message"]["content"]


# RunPod pods decommissioned.
# 2026-08-07: call_openai, call_qwen4_text, call_qwen4_pod_b_text removed.

def call_openrouter(prompt, model=OPENROUTER_DEFAULT_MODEL, timeout=60):
    key = _require_key("OPENROUTER_API_KEY")

    data = _post_json(
        "openrouter",
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model, "messages": [{"role": "user", "content": prompt}]},
        timeout=timeout,
    )
    return data["choices"][0]["message"]["content"]


def call_openrouter_claude(prompt, model=OPENROUTER_CLAUDE_MODEL, timeout=60):
    key = _require_key("OPENROUTER_API_KEY")

    data = _post_json(
        "openrouter_claude",
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model, "messages": [{"role": "user", "content": prompt}]},
        timeout=timeout,
    )
    return data["choices"][0]["message"]["content"]


def call_deepseek(prompt, model=DEEPSEEK_DEFAULT_MODEL, timeout=60):
    key = _require_key("DEEPSEEK_OPENROUTER_API_KEY")

    data = _post_json(
        "deepseek",
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model, "messages": [{"role": "user", "content": prompt}]},
        timeout=timeout,
    )
    return data["choices"][0]["message"]["content"]


# Direct DeepSeek platform API (api.deepseek.com), not proxied through
# OpenRouter or OpenCode CLI's agentic tool-use loop -- a single plain
# chat-completion request/response, same shape as every other plain
# text-completion client in this module. Added 2026-08-01 (operator-provided
# dedicated keys) specifically because the OpenCode-CLI-mediated
# "opencode_deepseek" coding-agent route was too slow/timeout-prone for
# quick delegated Q&A/review calls that don't need real tool use -- this
# trades away live file exploration for a fast, reliable plain response.
# Model IDs confirmed live via GET https://api.deepseek.com/v1/models on
# both keys: "deepseek-v4-pro" and "deepseek-v4-flash".
DEEPSEEK_NATIVE_PRO_MODEL = "deepseek-v4-pro"
DEEPSEEK_NATIVE_FLASH_MODEL = "deepseek-v4-flash"


def call_deepseek_native_pro(prompt, model=DEEPSEEK_NATIVE_PRO_MODEL, timeout=60, max_tokens=4096):
    key = _require_key("DEEPSEEK_NATIVE_PRO_API_KEY")

    data = _post_json(
        "deepseek_native_pro",
        "https://api.deepseek.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens},
        timeout=timeout,
    )
    return data["choices"][0]["message"]["content"]


def call_deepseek_native_flash(prompt, model=DEEPSEEK_NATIVE_FLASH_MODEL, timeout=60, max_tokens=2048):
    key = _require_key("DEEPSEEK_NATIVE_FLASH_API_KEY")

    data = _post_json(
        "deepseek_native_flash",
        "https://api.deepseek.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens},
        timeout=timeout,
    )
    return data["choices"][0]["message"]["content"]


def call_minimax(prompt, model=MINIMAX_DEFAULT_MODEL, timeout=60):
    key = _require_key("MINIMAX_API_KEY")

    data = _post_json(
        "minimax",
        "https://api.minimax.io/v1/text/chatcompletion_v2",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model, "messages": [{"role": "user", "content": prompt}]},
        timeout=timeout,
    )

    if not data.get("choices"):
        status = (data.get("base_resp") or {}).get("status_msg", "unknown error")
        raise RuntimeError(f"minimax request failed: {status}")

    return data["choices"][0]["message"]["content"]


# 2026-08-03: OmniRoute (localhost:20128, the operator's self-hosted AI
# gateway, v16.2.12) as Kai's always-on fallback -- the operator directive
# was "i want kai to add omniroute as a provider so it has access to them,
# that will be kai's always on fallback". It aggregates multiple upstream
# providers (OpenRouter, native DeepSeek, minimax, north, etc.) behind one
# OpenAI-compatible /v1 endpoint, and its own gateway account has the API
# key independent of any single upstream's quota. Reuses the same
# ANTHROPIC_AUTH_TOKEN / OPENAI_API_KEY credential the Claude Code session
# itself uses to reach the gateway (confirmed live: auto/best-fast routed to
# minimax-m2.5 and auto/pro-coding -> north-mini-code-free, HTTP 200).
OMNIROUTE_BASE_URL = os.getenv("OMNIROUTE_BASE_URL", "http://localhost:20128/v1").rstrip("/")
# auto/ routes let the gateway pick the best upstream per request, so a
# single model slot never bakes in one upstream's outage/credit state.
OMNIROUTE_CODING_MODEL = os.getenv("OMNIROUTE_CODING_MODEL", "auto/best-coding")
OMNIROUTE_TEXT_MODEL = os.getenv("OMNIROUTE_TEXT_MODEL", "auto/best-fast")


def _omniroute_key():
    # The gateway authenticates whichever key the operator configured it
    # with; this session's own ANTHROPIC_AUTH_TOKEN already works against
    # localhost:20128 (verified live above).
    return os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("OPENAI_API_KEY")


def call_omniroute(prompt, model=None, timeout=60):
    key = _omniroute_key()
    if not key:
        raise ProviderUnavailable("OmniRoute needs ANTHROPIC_AUTH_TOKEN or OPENAI_API_KEY")

    data = _post_json(
        "omniroute",
        f"{OMNIROUTE_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model or OMNIROUTE_TEXT_MODEL, "messages": [{"role": "user", "content": prompt}]},
        timeout=timeout,
    )

    if not data.get("choices"):
        detail = (data.get("error") or {}).get("message", "unknown error")
        raise RuntimeError(f"omniroute request failed: {detail}")

    return data["choices"][0]["message"]["content"]


# 2026-08-06: dedicated DeepSeek V4 Flash route through OmniRoute.
# Operator-provided API key, already verified live against api.deepseek.com.
# This gives Kai a named provider for ds/deepseek-v4-flash that routes
# through the self-hosted omniroute gateway instead of calling DeepSeek
# directly — omniroute handles failover, logging, and cost aggregation.
OMNIROUTE_DEEPSEEK_FLASH_MODEL = "ds/deepseek-v4-flash"


def call_omniroute_deepseek_flash(prompt, timeout=60):
    """DeepSeek V4 Flash via OmniRoute gateway.

    Uses the operator-provided DeepSeek native key, routed through
    omniroute's ds/deepseek-v4-flash model slot so Kai gets gateway-level
    failover, call logging, and cost aggregation without a separate
    direct-to-DeepSeek code path.
    """
    key = _omniroute_key()
    if not key:
        raise ProviderUnavailable(
            "OmniRoute DeepSeek Flash needs ANTHROPIC_AUTH_TOKEN or OPENAI_API_KEY"
        )

    data = _post_json(
        "omniroute_deepseek_flash",
        f"{OMNIROUTE_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": OMNIROUTE_DEEPSEEK_FLASH_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        },
        timeout=timeout,
    )

    if not data.get("choices"):
        detail = (data.get("error") or {}).get("message", "unknown error")
        raise RuntimeError(f"omniroute deepseek flash request failed: {detail}")

    return data["choices"][0]["message"]["content"]


# GPU.ai — Gemma 4 31B IT (vision-capable, OpenAI-compatible).
# https://gpu.ai hosts Google's Gemma 4 31B IT with full multimodal support:
# image understanding, OCR, document analysis, table extraction, UI screenshots,
# and all the visual capabilities listed in Gemma 4's capability registry.
# GPU.ai exposes a standard OpenAI-compatible /v1 endpoint, so this is a thin
# wrapper around the same chat/completions call shape as call_groq/call_openrouter.
#
# Unlike the other text-only clients in this module, this function accepts
# OpenAI-compatible message arrays (text + image_url blocks) so the legal
# module can send scanned documents, photos of legislation pages, etc.
GPUAI_GEMMA_MODEL = "gpuai/gemma-4-31b-it"
GPUAI_BASE_URL = "https://api.gpu.ai/v1"


def call_gpuai_gemma(prompt, model=GPUAI_GEMMA_MODEL, timeout=120, max_tokens=4096):
    """Call GPU.ai's Gemma 4 31B IT (vision-capable, OpenAI-compatible).

    `prompt` can be:
      - str: sent as {"role": "user", "content": prompt}
      - list[dict]: sent directly as the messages array (allows image_url blocks,
        multi-turn, system messages, etc.)

    Returns the response text. Raises ProviderUnavailable if no key is stored.
    """
    from core.ai.secrets import get_api_key
    key = get_api_key("gpuai")
    if not key:
        raise ProviderUnavailable("GPU.ai Gemma needs a stored secret (provider: 'gpuai')")

    if isinstance(prompt, list):
        messages = prompt
    else:
        messages = [{"role": "user", "content": prompt}]

    data = _post_json(
        "gpuai_gemma",
        f"{GPUAI_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model, "messages": messages, "max_tokens": max_tokens},
        timeout=timeout,
    )

    if not data.get("choices"):
        detail = (data.get("error") or {}).get("message", "unknown error")
        raise RuntimeError(f"gpuai_gemma request failed: {detail}")

    return data["choices"][0]["message"]["content"]


# GPU.ai Minimax M3 — serverless, OpenAI-compatible.
# Replaces opencode_minimax (removed 2026-08-10). Same GPU.ai account key
# as gpuai_gemma, different model. https://api.gpu.ai/v1/chat/completions
GPUAI_MINIMAX_MODEL = "gpuai/minimax-m3"


def call_gpuai_minimax(prompt, model=GPUAI_MINIMAX_MODEL, timeout=60, max_tokens=4096):
    """Call GPU.ai's Minimax M3 (OpenAI-compatible chat API).

    Uses the same GPU.ai account key as call_gpuai_gemma — stored secrets,
    not env vars. Returns the response text. Raises ProviderUnavailable if
    no key is stored.
    """
    from core.ai.secrets import get_api_key
    key = get_api_key("gpuai")
    if not key:
        raise ProviderUnavailable(
            "GPU.ai Minimax M3 needs a stored secret (provider: 'gpuai')"
        )

    data = _post_json(
        "gpuai_minimax",
        f"{GPUAI_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens},
        timeout=timeout,
    )

    if not data.get("choices"):
        detail = (data.get("error") or {}).get("message", "unknown error")
        raise RuntimeError(f"gpuai_minimax request failed: {detail}")

    return data["choices"][0]["message"]["content"]
