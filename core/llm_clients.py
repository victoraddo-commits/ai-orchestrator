"""Thin, plain text-completion clients for non-agentic providers.

Deliberately NOT a second coding engine: these are single request/response
chat-completion calls only, no tool use, no file access, no agent loop.
Only Claude (via core.coding_bridge / CloudCLI's Agent SDK) can write files
or run commands -- these three exist for the text-in/text-out roles (review,
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
    try:
        response = requests.post(url, **kwargs)

        if response.status_code == 429:
            # Quota/rate-limit signal is useful even on failure -- capture it
            # before raising, using whatever detail the provider's own error
            # body gives (never the request itself, so this can't leak the key).
            try:
                detail = response.json()
            except ValueError:
                detail = response.text[:300]
            provider_health.capture_quota_exceeded(provider_key, detail=str(detail)[:300])

        response.raise_for_status()

        provider_health.capture_from_response_headers(provider_key, response.headers)

        return response.json()
    except requests.RequestException as error:
        raise RuntimeError(f"{provider_key} request failed: {type(error).__name__}") from None


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


def call_openai(prompt, model=OPENAI_DEFAULT_MODEL, timeout=60):
    key = _require_key("OPENAI_API_KEY")

    data = _post_json(
        "openai",
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model, "messages": [{"role": "user", "content": prompt}]},
        timeout=timeout,
    )
    return data["choices"][0]["message"]["content"]
