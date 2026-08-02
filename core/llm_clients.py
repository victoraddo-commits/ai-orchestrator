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


def _require_key(env_var):
    key = os.getenv(env_var)
    if not key:
        raise ProviderUnavailable(f"{env_var} is not set")
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


QWEN3_CODER_BASE_URL = os.getenv("VLLM_QWEN3_CODER_BASE_URL", "").rstrip("/")
QWEN3_CODER_MODEL = os.getenv("VLLM_QWEN3_CODER_MODEL", "")


# 2026-08-02 operator directive: the "openai" provider slot now means the
# self-hosted Qwen3-Coder vLLM instance, not the real OpenAI API -- same
# "provider name keeps meaning something different underneath" convention
# already used for "opencode" (meant minimax, then deepseek-v4-pro). Uses
# VLLM_QWEN3_CODER_API_KEY/BASE_URL/MODEL (see .env, confirmed live via
# GET {base_url}/models 2026-08-02) instead of OPENAI_API_KEY/
# OPENAI_DEFAULT_MODEL/api.openai.com -- those stay defined above for
# whenever this gets pointed back at the real API.
def call_openai(prompt, model=None, timeout=60):
    key = _require_key("VLLM_QWEN3_CODER_API_KEY")
    model = model or QWEN3_CODER_MODEL

    data = _post_json(
        "openai",
        f"{QWEN3_CODER_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model, "messages": [{"role": "user", "content": prompt}]},
        timeout=timeout,
    )
    return data["choices"][0]["message"]["content"]


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


def call_deepseek_native_pro(prompt, model=DEEPSEEK_NATIVE_PRO_MODEL, timeout=60):
    key = _require_key("DEEPSEEK_NATIVE_PRO_API_KEY")

    data = _post_json(
        "deepseek_native_pro",
        "https://api.deepseek.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model, "messages": [{"role": "user", "content": prompt}]},
        timeout=timeout,
    )
    return data["choices"][0]["message"]["content"]


def call_deepseek_native_flash(prompt, model=DEEPSEEK_NATIVE_FLASH_MODEL, timeout=60):
    key = _require_key("DEEPSEEK_NATIVE_FLASH_API_KEY")

    data = _post_json(
        "deepseek_native_flash",
        "https://api.deepseek.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model, "messages": [{"role": "user", "content": prompt}]},
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
