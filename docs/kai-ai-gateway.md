# Kai AI Gateway — API Reference

**Part of**: Phase 18A-ai — OpenAI-compatible Gateway for External Consumers
**Base URL**: `http://<kai-server>:8000/v1`
**Last updated**: 2026-08-10

## Overview

The Kai AI Gateway provides an **OpenAI-compatible HTTP API** for external
applications and services to access Kai's AI provider fleet. Any tool that can
talk to the OpenAI API can target Kai instead — same request/response shapes,
same streaming format.

Key properties:
- **Bearer token auth** — long-lived API keys (`kai_...`), managed separately from dashboard JWT sessions
- **Auto-routing** — pass `"model": "auto"` to let Kai pick the best provider for your task
- **Provider pinning** — pass a provider name as `model` to route directly
- **Per-key rate limiting** — 60 req/min default, 10 req/min for streaming
- **Audit logging** — every request logged with trace_id, consumer, provider, latency, cost

## Authentication

All endpoints require a bearer token in the `Authorization` header:

```
Authorization: Bearer kai_<your_api_key>
```

**Managing keys**:
- `POST /v1/keys` — Create a new API key (requires existing key)
- `GET /v1/keys` — List all key IDs
- `DELETE /v1/keys/{key_id}` — Revoke a key

The plaintext key is returned **only once** at creation. Save it immediately.
Keys use SHA-256 hashing with constant-time comparison (hmac.compare_digest).

## Endpoints

### POST /v1/chat/completions

OpenAI-compatible chat completion.

**Request**:
```json
{
  "model": "auto",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "What is the capital of France?"}
  ],
  "temperature": 0.7,
  "max_tokens": 100
}
```

**Kai extensions** (optional, silently ignored by standard OpenAI clients):
| Field | Type | Description |
|-------|------|-------------|
| `task_type` | string | Explicit role hint: `coding`, `planning`, `review`, `classification`, `documentation`, `log_analysis` |
| `timeout` | integer | Per-request timeout override in seconds (1-600) |

**Response** (200):
```json
{
  "id": "kai-a1b2c3d4e5f6",
  "object": "chat.completion",
  "created": 1755379200,
  "model": "auto",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "The capital of France is Paris."},
    "finish_reason": "stop"
  }],
  "usage": null,
  "provider": "deepseek_native_flash",
  "duration_ms": 245
}
```

Kai extensions: `provider` (which provider served the request) and `duration_ms` (wall-clock latency).

### POST /v1/chat/completions/stream

SSE streaming (simulated — full response delivered as one delta chunk followed by `[DONE]`).
Real per-token streaming will be available once individual provider clients support it.

**Response** (200):
```
data: {"id":"kai-...","object":"chat.completion.chunk","model":"auto","provider":"gemini","choices":[{"index":0,"delta":{"role":"assistant","content":"..."},"finish_reason":"stop"}]}

data: [DONE]
```

Response headers include `X-Kai-Request-Id` for tracing.

### GET /v1/models

List available models (text_task providers + `"auto"`).

**Response** (200):
```json
{
  "object": "list",
  "data": [
    {"id": "qwen4_text", "object": "model", "created": 1755379200, "owned_by": "kai"},
    {"id": "deepseek_native_flash", "object": "model", "created": 1755379200, "owned_by": "kai"},
    {"id": "gemini", "object": "model", "created": 1755379200, "owned_by": "kai"},
    {"id": "auto", "object": "model", "created": 1755379200, "owned_by": "kai"}
  ]
}
```

### GET /v1/providers

Provider health/status for all registered providers.

**Response** (200):
```json
{
  "providers": [
    {
      "name": "deepseek_native_flash",
      "available": true,
      "enabled": true,
      "capabilities": ["text_task"],
      "cost_tier": "free",
      "health": "ok"
    }
  ]
}
```

### POST /v1/providers/{name}/test

Test a provider's API connection using stored credentials.

**Response** (200):
```json
{
  "provider": "gemini",
  "ok": true,
  "status": "ok",
  "latency_ms": 312,
  "detail": "Provider healthy",
  "models": ["gemini-pro", "gemini-flash"]
}
```

### GET /v1/usage

Return usage stats for the authenticated consumer key.

**Response** (200):
```json
{
  "consumer_id": "kai_l7w0pQ93X34j",
  "total_requests": 42,
  "total_cost": 0.023,
  "providers": {"gemini": 20, "deepseek_native_flash": 22},
  "models": {"auto": 30, "gemini": 12}
}
```

## Rate Limits

| Limit name | Requests | Window |
|-----------|----------|--------|
| `gateway_default` | 60 | 60 seconds |
| `gateway_stream` | 10 | 60 seconds |

When exceeded, returns `429 Too Many Requests` with `Retry-After` header and:
```json
{
  "detail": {
    "error": "rate_limit_exceeded",
    "message": "Too many requests. Retry after 1.0s",
    "retry_after": 1.0
  }
}
```

## Error Responses

| Status | Error | Meaning |
|--------|-------|---------|
| 401 | `missing_authorization` | No `Authorization` header |
| 401 | `invalid_api_key` | Unknown or revoked key |
| 429 | `rate_limit_exceeded` | Per-key rate limit hit |
| 502 | `all_providers_failed` | No available provider could serve the request |

## Model → Provider Resolution

| `model` value | Behavior |
|---------------|----------|
| `"auto"` | Classify task type from messages → delegate to best provider via ai_router |
| Registered provider name (e.g. `"gemini"`) | Route directly to that provider |
| Unknown | Falls back to auto-routing |

## Architecture

```
External Consumer (any OpenAI-compatible client)
        │
        ▼
POST /v1/chat/completions
Authorization: Bearer kai_xxxx
        │
        ▼
┌──────────────────────────────────┐
│         Kai AI Gateway            │
│  ┌────────────────────────────┐  │
│  │ Bearer token auth (keys.py) │  │
│  └──────────┬─────────────────┘  │
│             ▼                    │
│  ┌────────────────────────────┐  │
│  │ Rate limiter (per-key)      │  │
│  └──────────┬─────────────────┘  │
│             ▼                    │
│  ┌────────────────────────────┐  │
│  │ Task classification         │  │
│  │ └→ classify_task(prompt)   │  │
│  └──────────┬─────────────────┘  │
│             ▼                    │
│  ┌────────────────────────────┐  │
│  │ Provider routing            │  │
│  │ └→ delegate(prompt,         │  │
│  │     task_type, capability)  │  │
│  └──────────┬─────────────────┘  │
│             ▼                    │
│  ┌────────────────────────────┐  │
│  │ Response shape              │  │
│  │ └→ OpenAI-compatible JSON   │  │
│  └──────────┬─────────────────┘  │
│             ▼                    │
│  ┌────────────────────────────┐  │
│  │ Audit log (audit.py)        │  │
│  └────────────────────────────┘  │
└──────────────────────────────────┘
```

## Quick Start

```bash
# Get your API key (from the Kai server)
curl -X POST http://localhost:8000/v1/keys \
  -H "Authorization: Bearer kai_<existing_key>" \
  -H "Content-Type: application/json" \
  -d '{"label": "my-app"}'

# List available models
curl http://localhost:8000/v1/models \
  -H "Authorization: Bearer kai_<your_key>"

# Chat
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer kai_<your_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "Hello Kai!"}]
  }'

# Check your usage
curl http://localhost:8000/v1/usage \
  -H "Authorization: Bearer kai_<your_key>"
```

## Security

- **API keys**: `kai_` prefix + 32 random bytes base64url, stored as SHA-256 hashes
- **No key reuse**: Each consumer gets their own key — revoke one without affecting others
- **Audit trail**: Every request logged with consumer, provider, latency, status code
- **Rate limiting**: Per-key token bucket prevents abuse
- **Storage**: Keys and audit log use atomic writes (`tmp + os.replace`), 0600 permissions
- **No exposure**: Plaintext keys never returned after creation; not stored in log files
