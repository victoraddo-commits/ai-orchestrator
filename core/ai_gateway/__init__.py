"""Phase 1 AI Gateway — unified OpenAI-compatible endpoint for external consumers.

Sits alongside ai_router.delegate() (which the orchestrator cycle continues to
use directly).  The gateway provides HTTP access with API-key auth, per-key
rate limiting, request/response audit logging, and cost tracking.

Routes
------
POST /v1/chat/completions          OpenAI-compatible chat (sync)
POST /v1/chat/completions/stream   SSE streaming (simulated initially)
GET  /v1/models                    List available models
GET  /v1/providers                 Provider health/status
"""

from core.ai_gateway.gateway import router

__all__ = ["router"]
