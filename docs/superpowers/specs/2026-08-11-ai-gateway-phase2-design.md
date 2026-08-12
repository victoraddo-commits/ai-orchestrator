# AI Gateway Phase 2 — Direct Provider Routing

**Date**: 2026-08-11
**Phase**: 18A-ai Phase 2
**Status**: approved

## Overview

Add a `provider` parameter to `delegate()` in `core/ai/ai_router.py` so callers
can route to a specific provider instead of always auto-classifying and rotating.
Wire the AI Gateway's `/v1/chat/completions` to pass the consumer's `model`
choice through to `delegate()`, fixing the current behavior where model selection
is silently ignored.

## Motivation

Phase 1 built the gateway shell — FastAPI routes, API key auth, rate limiting,
and audit logging. But `/v1/chat/completions` calls `delegate()` which always
auto-routes via `classify_task()` + `_candidates_for()` + rotation, ignoring the
consumer's `model` parameter entirely. A consumer requesting
`model: "deepseek_native_flash"` might get `gemini` or `qwen` — the model field
is decorative.

Phase 2 makes model selection functional. Consumers who want specific behavior
(e.g. "use the free local model for cost reasons") can now get it.

## Scope

### In scope
- `provider` kwarg on `delegate()` — skips classification/rotation, tries only
  that provider
- Gateway passes resolved provider to `delegate()`
- Error handling: 400 for unknown models, 502 for provider failures
- Tests for both `delegate()` and the HTTP gateway

### Out of scope (deferred to later phases)
- Real per-token SSE streaming (Phase 3)
- Token usage in responses (Phase 3)
- Cost tracking in responses (Phase 4)
- `/v1/models` enrichment with model capabilities (Phase 4)

## Design

### 1. `delegate()` — new `provider` parameter

```python
def delegate(description, task_type=None, timeout=60, project_path=None,
             capability="text_task", return_attempts=False,
             requires_file_access=False, provider=None):
```

When `provider` is set to a registered provider key:
1. Look up the provider via `ai_provider.get_provider(provider)`
2. If not found, raise `AllProvidersFailed` with `"unknown_provider"` error
3. Run the standard health/availability/enabled/circuit-breaker checks
4. Call `run_text_task` (or `run_coding_task`) directly
5. If it fails, raise `AllProvidersFailed` immediately — no fallback chain
6. If it succeeds, return the result

When `provider` is `None` (default): behavior is **unchanged**.

### 2. Gateway — pass model choice to `delegate()`

In `chat_completions()` and `chat_completions_stream()`:

- `model: "auto"` or omitted → `delegate()` with `provider=None` (auto-route)
- `model: "local"` → `delegate(provider="local")` (direct route)
- `model: "nonexistent"` → HTTP 400 before calling `delegate()`

The gateway already resolves models via `_resolve_model()`. The change is
passing the resolved value into `delegate()`.

### 3. Error responses

| Scenario | HTTP | Response |
|----------|------|----------|
| Unknown model | 400 | `{"error": "unknown_model", "message": "..."}` |
| Provider unavailable | 502 | `{"error": "provider_failed", "provider": "...", "message": "..."}` |
| All providers failed (auto) | 502 | `{"error": "all_providers_failed", "message": "..."}` |

## Files changed

| File | Change |
|------|--------|
| `core/ai/ai_router.py` | Add `provider` parameter to `delegate()` |
| `core/ai_gateway/gateway.py` | Pass resolved provider to `delegate()` |
| `tests/test_ai_router.py` | Tests for provider override |
| `tests/test_ai_gateway.py` | HTTP tests for direct routing |

## Backward compatibility

Fully backward compatible. The `provider` parameter defaults to `None`, which
preserves the exact existing behavior. All internal callers of `delegate()`
(orchestrator cycle, build manager, kai commands, etc.) are unchanged.

## Testing

- `test_delegate_provider_override` — provider="local" routes to local only
- `test_delegate_provider_override_nonexistent` — unknown provider raises
- `test_delegate_provider_override_unavailable` — unavailable provider raises
- `test_delegate_default_behavior_unchanged` — provider=None auto-routes
- `test_gateway_direct_model_route` — POST with model="local" uses local
- `test_gateway_unknown_model_400` — POST with model="fake" returns 400
- `test_gateway_auto_route_unchanged` — POST with model="auto" auto-routes
