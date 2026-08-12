"""AI-4: Serverless-compatible AI completion handler.

Self-contained handler suitable for Vercel Functions, Netlify Functions,
AWS Lambda, or any HTTP framework. The handler accepts a standard request
dict and returns an OpenAI-compatible response dict.

Dependencies: core.ai.ai_router (delegate), core.llm_clients (provider clients).
Cold-start target: <500ms for deepseek_native_flash.
"""

import json
import time
from typing import Optional


# Maximum completion tokens for serverless calls (keep responses tight to
# stay under Vercel's 10s function timeout on the free tier).
MAX_TOKENS_DEFAULT = 1024
VERCEL_TIMEOUT_SAFETY = 8.0  # seconds — leave 2s margin below 10s limit


def handle_completion(
    prompt: str,
    model: str = "auto",
    max_tokens: int = MAX_TOKENS_DEFAULT,
    task_type: Optional[str] = None,
    temperature: float = 0.7,
) -> dict:
    """Handle a serverless completion request.

    Args:
        prompt: User prompt text.
        model: Provider key (e.g. "deepseek_native_flash") or "auto" for routing.
        max_tokens: Maximum completion tokens.
        task_type: Override task classification, or None for auto-detect.
        temperature: Sampling temperature.

    Returns:
        OpenAI-compatible dict with ``choices``, ``model``, ``usage``.
    """
    import concurrent.futures

    from core.ai.ai_router import classify_task, delegate

    detected_type = task_type or classify_task(prompt)
    provider_used = None
    response_text = ""
    usage = {}
    attempts = []

    start = time.monotonic()

    try:
        # Validate provider if direct routing requested
        if model != "auto":
            from core.ai_provider import get_provider

            provider_info = get_provider(model)
            if provider_info is None or "text_task" not in provider_info.get("capabilities", []):
                return _error_response(
                    f"Provider '{model}' not found or doesn't support text tasks",
                    status=400,
                )

        # Route via ai_router.delegate() for both paths
        kwargs = {
            "prompt": prompt,
            "task_type": detected_type,
            "capability": "text_task",
            "return_attempts": True,
        }
        if model != "auto":
            kwargs["provider"] = model

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(delegate, **kwargs)
            try:
                result = future.result(timeout=VERCEL_TIMEOUT_SAFETY + 5)
                response_text = result["response"]
                provider_used = result["provider"]
                attempts = result.get("attempts", [])
            except concurrent.futures.TimeoutError:
                return _error_response("Request timed out", status=504)

    except Exception as e:
        return _error_response(f"Provider error: {str(e)}", status=500)

    elapsed = round(time.monotonic() - start, 3)
    # Rough token estimate: ~4 chars per token
    prompt_tokens = len(prompt) // 4
    completion_tokens = len(response_text) // 4

    usage = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }

    return {
        "id": f"serverless-{int(start)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": provider_used or "auto",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": response_text},
                "finish_reason": "stop",
            }
        ],
        "usage": usage,
        "_meta": {
            "provider": provider_used,
            "task_type": detected_type,
            "elapsed_s": elapsed,
            "attempts": attempts,
        },
    }


def handle_health() -> dict:
    """Health check endpoint — returns provider availability."""
    from core.ai_provider import list_providers

    providers = {}
    for name, p in sorted(list_providers().items()):
        if "text_task" in p.get("capabilities", []):
            try:
                available = p.get("enabled", True) and p.get("available", True)
            except Exception:
                available = False
            providers[name] = {
                "available": available,
                "kind": p.get("kind", "cloud"),
                "cost_tier": p.get("cost_tier", "unknown"),
            }

    return {
        "status": "healthy",
        "providers": providers,
    }


def _error_response(message: str, status: int = 500) -> dict:
    return {
        "id": f"error-{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "error",
        "choices": [],
        "error": {"message": message, "code": status},
    }
