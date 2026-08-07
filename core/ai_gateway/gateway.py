"""FastAPI router for the AI Gateway — OpenAI-compatible /v1 endpoints.

Mounted on the existing FastAPI app in core/api.py via include_router().
External consumers authenticate with bearer API keys (core.ai_gateway.keys),
not the dashboard JWT/bridge-token auth used by other routes.
"""

import json
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from core.ai.ai_router import classify_task, delegate, AllProvidersFailed
from core.ai_provider import list_providers as get_all_providers
from core.rate_limiter import RateLimiter

from core.ai_gateway.keys import validate_api_key, ensure_default_key
from core.ai_gateway.audit import log_request
from core.ai_gateway.models import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionChoice,
    Message,
    Usage,
    ModelInfo,
    ModelList,
    ProviderInfo,
    ProviderList,
)

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/v1", tags=["ai-gateway"])

# ---------------------------------------------------------------------------
# Rate limiter (separate instance from the auth one — gateway consumers are
# independent of dashboard/login rate limits)
# ---------------------------------------------------------------------------

_gateway_limiter = RateLimiter()


def _check_rate_limit(key_id: str, is_stream: bool = False) -> None:
    """Check per-key rate limit.  Raises 429 if exceeded."""
    limit_name = "gateway_stream" if is_stream else "gateway_default"
    allowed, retry = _gateway_limiter.check(key_id, limit_name)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limit_exceeded",
                "message": f"Too many requests. Retry after {retry:.1f}s",
                "retry_after": round(retry, 1),
            },
            headers={"Retry-After": str(int(retry) + 1)},
        )


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


async def get_api_key(request: Request) -> dict:
    """Extract and validate the bearer API key from the Authorization header.

    Returns the key record dict on success, raises 401 on failure.
    """
    auth = request.headers.get("Authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail={"error": "missing_authorization",
                    "message": "Authorization header must be: Bearer <api_key>"},
        )

    plaintext = auth[7:].strip()  # len("Bearer ") == 7
    key_record = validate_api_key(plaintext)
    if key_record is None:
        raise HTTPException(
            status_code=401,
            detail={"error": "invalid_api_key", "message": "Unknown or revoked API key"},
        )

    return key_record


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_prompt(messages: list[Message]) -> str:
    """Join a list of chat messages into a single prompt string.

    System messages are prefixed with a role tag; user messages are passed
    through directly.  This is a pragmatic simplification — the gateway is
    text_task only (no tool use), so a structured prompt works fine.
    """
    parts: list[str] = []
    for msg in messages:
        role = msg.role.lower()
        if role == "system":
            parts.append(f"[System]: {msg.content}")
        elif role == "assistant":
            parts.append(f"[Assistant]: {msg.content}")
        else:
            parts.append(msg.content)
    return "\n\n".join(parts)


def _extract_task_type(messages: list[Message], explicit: Optional[str]) -> str:
    """Resolve task_type: explicit hint wins, otherwise classify from content."""
    if explicit:
        return explicit
    prompt = _build_prompt(messages)
    return classify_task(prompt)


def _resolve_model(requested: str) -> Optional[str]:
    """Resolve a model name to a provider key.

    - "auto" → None (let delegate() classify + route)
    - registered provider key → that key
    - unknown → None (will error downstream)
    """
    if not requested or requested == "auto":
        return None

    providers = get_all_providers()
    if requested in providers:
        return requested

    # Case-insensitive fallback
    lower = requested.lower()
    for name in providers:
        if name.lower() == lower:
            return name

    return None


def _model_response_shape(provider: str, delegate_result: dict, requested_model: str) -> ChatCompletionResponse:
    """Map a delegate() result into an OpenAI-compatible response."""
    response_text = delegate_result.get("response", "")
    duration_ms = delegate_result.get("duration_ms", 0)

    return ChatCompletionResponse(
        id=f"kai-{uuid.uuid4().hex[:12]}",
        model=requested_model,
        choices=[
            ChatCompletionChoice(
                index=0,
                message=Message(role="assistant", content=response_text),
                finish_reason="stop",
            )
        ],
        usage=None,   # text_task responses don't carry token counts yet
        provider=provider,
        duration_ms=duration_ms,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/models", response_model=ModelList)
async def list_models():
    """Return all available text_task providers as OpenAI-compatible models."""
    data: list[ModelInfo] = []
    for name, info in get_all_providers().items():
        if not info.get("available") or not info.get("enabled"):
            continue
        if "text_task" not in info.get("capabilities", []):
            continue
        data.append(ModelInfo(id=name, owned_by="kai"))

    # "auto" is always available
    data.append(ModelInfo(id="auto", owned_by="kai"))

    return ModelList(data=data)


@router.get("/providers", response_model=ProviderList)
async def api_list_providers():
    """Return provider health/status for all registered providers."""
    from core.ai.ai_router import get_provider_dashboard

    dashboard = get_provider_dashboard()
    providers: list[ProviderInfo] = []
    for name, d in dashboard.items():
        providers.append(ProviderInfo(
            name=name,
            available=d.get("status") == "connected",
            enabled=True,  # dashboard filters disabled already
            capabilities=["text_task"] if d.get("last_task_type") else ["text_task", "coding_agent"],
            cost_tier=d.get("cost_tier", "unknown"),
            health=d.get("health", "unknown"),
        ))
    return ProviderList(providers=providers)


@router.post("/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(
    body: ChatCompletionRequest,
    request: Request,
    api_key: dict = Depends(get_api_key),
):
    """OpenAI-compatible chat completions.

    ``model``: provider key (e.g. "qwen4_text") or "auto" for auto-routing.
    """
    # Rate limit
    _check_rate_limit(api_key["key_id"], is_stream=False)

    # Resolve model → provider
    task_type = _extract_task_type(body.messages, body.task_type)
    prompt = _build_prompt(body.messages)
    provider = _resolve_model(body.model)
    timeout = body.timeout or 60

    start = time.time()
    try:
        if provider:
            # Direct route to a specific provider
            result = delegate(
                prompt,
                task_type=task_type,
                timeout=timeout,
                capability="text_task",
            )
            actual_provider = result["provider"]
        else:
            # Auto-route: classify + delegate
            result = delegate(
                prompt,
                task_type=task_type,
                timeout=timeout,
                capability="text_task",
            )
            actual_provider = result["provider"]
    except AllProvidersFailed as exc:
        duration_ms = int((time.time() - start) * 1000)
        log_request(
            consumer=api_key["key_id"],
            model=body.model or "auto",
            provider="(none)",
            duration_ms=duration_ms,
            status_code=502,
            error=str(exc)[:500],
        )
        raise HTTPException(
            status_code=502,
            detail={"error": "all_providers_failed",
                    "message": "No available provider could serve this request"},
        )

    duration_ms = int((time.time() - start) * 1000)

    # Audit log
    log_request(
        consumer=api_key["key_id"],
        model=body.model or "auto",
        provider=actual_provider,
        duration_ms=duration_ms,
        status_code=200,
    )

    return _model_response_shape(actual_provider, result, body.model or "auto")


@router.post("/chat/completions/stream")
async def chat_completions_stream(
    body: ChatCompletionRequest,
    request: Request,
    api_key: dict = Depends(get_api_key),
):
    """Streaming chat completions (SSE).

    Currently simulates streaming — the full response is delivered as a single
    SSE chunk.  Real per-token streaming will be wired once individual
    provider clients (llm_clients) support streaming responses.
    """
    # Rate limit (stricter for streaming)
    api_key["key_id"]  # trigger validation
    _check_rate_limit(api_key["key_id"], is_stream=True)

    task_type = _extract_task_type(body.messages, body.task_type)
    prompt = _build_prompt(body.messages)
    provider = _resolve_model(body.model)
    timeout = body.timeout or 60
    model_id = body.model or "auto"
    request_id = f"kai-{uuid.uuid4().hex[:12]}"

    # Run the delegate call once — streaming simulates chunking from this
    start = time.time()
    try:
        result = delegate(
            prompt,
            task_type=task_type,
            timeout=timeout,
            capability="text_task",
        )
        actual_provider = result["provider"]
        response_text = result.get("response", "")
    except AllProvidersFailed as exc:
        duration_ms = int((time.time() - start) * 1000)
        log_request(
            consumer=api_key["key_id"],
            model=model_id,
            provider="(none)",
            duration_ms=duration_ms,
            status_code=502,
            error=str(exc)[:500],
        )

        async def error_stream():
            import json
            error_data = json.dumps({"error": "all_providers_failed"})
            yield f"data: {error_data}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            error_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Kai-Request-Id": request_id,
            },
        )

    duration_ms = int((time.time() - start) * 1000)

    log_request(
        consumer=api_key["key_id"],
        model=model_id,
        provider=actual_provider,
        duration_ms=duration_ms,
        status_code=200,
    )

    # Simulated streaming: yield the full response as one delta chunk,
    # then [DONE].  Real streaming will split this into per-token deltas.
    async def generate():
        chunk = {
            "id": request_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model_id,
            "provider": actual_provider,
            "choices": [{
                "index": 0,
                "delta": {"role": "assistant", "content": response_text},
                "finish_reason": "stop",
            }],
        }
        yield f"data: {json.dumps(chunk)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Kai-Request-Id": request_id,
        },
    )


# ---------------------------------------------------------------------------
# Provider connection test (Command Center / admin)
# ---------------------------------------------------------------------------

from pydantic import BaseModel as _BaseModel


class ProviderTestResult(_BaseModel):
    provider: str
    ok: bool
    status: str
    latency_ms: int
    detail: str
    models: list[str] = []


@router.post("/providers/{provider_name}/test", response_model=ProviderTestResult)
async def test_provider_connection(
    provider_name: str,
    api_key: dict = Depends(get_api_key),
):
    """Test a provider's API connection using stored credentials.

    Makes a lightweight GET /v1/models request to verify the stored API key
    is valid.  Requires a valid gateway API key (bearer token auth).
    """
    try:
        from core.ai.secrets import check_health
    except ImportError:
        raise HTTPException(500, detail="Secrets management module not available")

    result = check_health(provider_name)
    return ProviderTestResult(
        provider=provider_name,
        ok=result["ok"],
        status=result["status"],
        latency_ms=result["latency_ms"],
        detail=result["detail"],
        models=result["models"],
    )
