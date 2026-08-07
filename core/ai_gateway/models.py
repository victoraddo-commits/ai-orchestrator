"""OpenAI-compatible request/response models for the AI Gateway.

These Pydantic models mirror the OpenAI /v1/chat/completions contract so any
OpenAI-compatible client can target the gateway with zero changes.  Kai-specific
extensions (provider, duration_ms, task_type) are appended to the response
without breaking standard-compliant parsers.
"""

import time
import uuid
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class Message(BaseModel):
    """A single chat message."""
    role: str = Field(..., description="One of: system, user, assistant")
    content: str = Field(..., description="The message content")


class ChatCompletionRequest(BaseModel):
    """OpenAI-compatible chat completion request.

    The ``model`` field doubles as a provider key: pass ``"auto"`` to let
    the router auto-detect task_type and pick the best provider, or pass a
    registered provider key (e.g. ``"deepseek_native_flash"``) to route directly.
    """
    model: str = Field(
        default="auto",
        description="Provider key or 'auto' for automatic routing",
    )
    messages: list[Message] = Field(..., min_length=1)
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(default=None, ge=1)
    stream: bool = Field(default=False)
    # Kai-specific extensions — silently accepted, ignored by standard clients
    task_type: Optional[str] = Field(
        default=None,
        description="Explicit role hint (coding/planning/review/…). Auto-detected from messages when omitted.",
    )
    timeout: Optional[int] = Field(
        default=None, ge=1, le=600,
        description="Per-request timeout override, seconds.",
    )


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class Usage(BaseModel):
    """Token usage.  prompt/completion/total are null when the provider
    does not report token counts."""
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None


class ChatCompletionChoice(BaseModel):
    """A single completion choice."""
    index: int = 0
    message: Message
    finish_reason: str = "stop"


class ChatCompletionResponse(BaseModel):
    """OpenAI-compatible chat completion response."""
    id: str = Field(default_factory=lambda: f"kai-{uuid.uuid4().hex[:12]}")
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: list[ChatCompletionChoice]
    usage: Optional[Usage] = None
    # Kai extensions — safe for standard clients to ignore
    provider: str = Field(description="Which provider actually served the request")
    duration_ms: int = Field(description="Wall-clock latency in milliseconds")


# ---------------------------------------------------------------------------
# Model / provider listing models
# ---------------------------------------------------------------------------

class ModelInfo(BaseModel):
    """One available model."""
    id: str
    object: str = "model"
    created: int = Field(default_factory=lambda: int(time.time()))
    owned_by: str = "kai"


class ModelList(BaseModel):
    """Response from GET /v1/models."""
    object: str = "list"
    data: list[ModelInfo]


class ProviderInfo(BaseModel):
    """One provider's status snapshot."""
    name: str
    available: bool
    enabled: bool
    capabilities: list[str]
    cost_tier: str
    health: str


class ProviderList(BaseModel):
    """Response from GET /v1/providers."""
    providers: list[ProviderInfo]
