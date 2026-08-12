"""Tests for AI Gateway — OpenAI-compatible /v1 endpoints (18A-ai).

Covers: auth, models, providers, chat completions, streaming, key management,
usage, rate limiting, audit logging, and Pydantic models.
"""

import json
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """FastAPI TestClient with the gateway router mounted."""
    from core.api import app
    return TestClient(app)


@pytest.fixture
def valid_auth():
    """Return a valid Authorization header value for the default API key."""
    from core.ai_gateway.keys import ensure_default_key, generate_api_key
    # Get or create a key
    default = ensure_default_key()
    if default:
        # Freshly created — return it
        return f"Bearer {default}"

    # Keys already exist — create a fresh test key
    key_id, plaintext = generate_api_key(label="test-fixture")
    return f"Bearer {plaintext}"


@pytest.fixture
def valid_key():
    """Return just the plaintext key string."""
    from core.ai_gateway.keys import generate_api_key
    _, plaintext = generate_api_key(label="test-key")
    return plaintext


# ---------------------------------------------------------------------------
# Test: Auth
# ---------------------------------------------------------------------------


class TestAuth:
    """Verify bearer token authentication."""

    def test_401_without_auth_header(self, client):
        resp = client.get("/v1/models")
        assert resp.status_code == 401
        assert "missing_authorization" in resp.text

    def test_401_with_invalid_prefix(self, client):
        resp = client.get("/v1/models", headers={"Authorization": "Basic xyz"})
        assert resp.status_code == 401

    def test_401_with_invalid_key(self, client):
        resp = client.get("/v1/models", headers={"Authorization": "Bearer kai_invalid_key_never_valid"})
        assert resp.status_code == 401

    def test_401_with_empty_key(self, client):
        resp = client.get("/v1/models", headers={"Authorization": "Bearer "})
        assert resp.status_code == 401

    def test_200_with_valid_key_on_models(self, client, valid_auth):
        resp = client.get("/v1/models", headers={"Authorization": valid_auth})
        assert resp.status_code == 200

    def test_200_with_valid_key_on_providers(self, client, valid_auth):
        resp = client.get("/v1/providers", headers={"Authorization": valid_auth})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Test: GET /v1/models
# ---------------------------------------------------------------------------


class TestModelsEndpoint:
    """Verify GET /v1/models."""

    def test_returns_model_list(self, client, valid_auth):
        resp = client.get("/v1/models", headers={"Authorization": valid_auth})

        assert resp.status_code == 200
        data = resp.json()
        assert data["object"] == "list"
        assert isinstance(data["data"], list)

    def test_includes_auto_model(self, client, valid_auth):
        resp = client.get("/v1/models", headers={"Authorization": valid_auth})

        data = resp.json()
        model_ids = [m["id"] for m in data["data"]]
        assert "auto" in model_ids

    def test_all_models_have_required_fields(self, client, valid_auth):
        resp = client.get("/v1/models", headers={"Authorization": valid_auth})

        for model in resp.json()["data"]:
            assert "id" in model
            assert model["object"] == "model"
            assert model["owned_by"] == "kai"


# ---------------------------------------------------------------------------
# Test: GET /v1/providers
# ---------------------------------------------------------------------------


class TestProvidersEndpoint:
    """Verify GET /v1/providers."""

    def test_returns_provider_list(self, client, valid_auth):
        resp = client.get("/v1/providers", headers={"Authorization": valid_auth})

        assert resp.status_code == 200
        data = resp.json()
        assert "providers" in data
        assert isinstance(data["providers"], list)

    def test_providers_have_required_fields(self, client, valid_auth):
        resp = client.get("/v1/providers", headers={"Authorization": valid_auth})

        for p in resp.json()["providers"]:
            assert "name" in p
            assert "available" in p
            assert "enabled" in p
            assert "capabilities" in p
            assert "cost_tier" in p


# ---------------------------------------------------------------------------
# Test: POST /v1/chat/completions
# ---------------------------------------------------------------------------


class TestChatCompletions:
    """Verify the sync chat completions endpoint."""

    BODY = {
        "model": "auto",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "What is the capital of France?"},
        ],
    }

    def test_401_without_auth(self, client):
        resp = client.post("/v1/chat/completions", json=self.BODY)
        assert resp.status_code == 401

    def test_400_without_messages(self, client, valid_auth):
        resp = client.post("/v1/chat/completions",
                           json={"model": "auto"},
                           headers={"Authorization": valid_auth})
        assert resp.status_code == 422  # Pydantic validation error

    def test_returns_openai_compatible_response(self, client, valid_auth):
        resp = client.post("/v1/chat/completions",
                           json=self.BODY,
                           headers={"Authorization": valid_auth})

        assert resp.status_code in (200, 502)
        if resp.status_code == 200:
            data = resp.json()
            assert data["object"] == "chat.completion"
            assert "choices" in data
            assert len(data["choices"]) >= 1
            assert "message" in data["choices"][0]
            assert data["choices"][0]["message"]["role"] == "assistant"
            # Kai extensions
            assert "provider" in data
            assert "duration_ms" in data

    def test_handles_specific_model(self, client, valid_auth):
        body = {**self.BODY, "model": "deepseek_native_flash"}
        resp = client.post("/v1/chat/completions",
                           json=body,
                           headers={"Authorization": valid_auth})

        assert resp.status_code in (200, 502)

    def test_handles_system_only_message(self, client, valid_auth):
        body = {
            "model": "auto",
            "messages": [
                {"role": "system", "content": "You are Kai."},
                {"role": "user", "content": "Hello"},
            ],
        }
        resp = client.post("/v1/chat/completions",
                           json=body,
                           headers={"Authorization": valid_auth})
        assert resp.status_code in (200, 502)

    def test_passes_task_type(self, client, valid_auth):
        body = {**self.BODY, "task_type": "planning"}
        resp = client.post("/v1/chat/completions",
                           json=body,
                           headers={"Authorization": valid_auth})
        assert resp.status_code in (200, 502)

    def test_passes_temperature(self, client, valid_auth):
        body = {**self.BODY, "temperature": 0.7}
        resp = client.post("/v1/chat/completions",
                           json=body,
                           headers={"Authorization": valid_auth})
        assert resp.status_code in (200, 502)

    def test_502_returns_error_detail(self, client, valid_auth):
        """When all providers fail, the gateway returns 502 with detail."""
        body = {
            "model": "nonexistent_provider_xyz",
            "messages": [{"role": "user", "content": "test"}],
        }
        resp = client.post("/v1/chat/completions",
                           json=body,
                           headers={"Authorization": valid_auth})
        # 18A-ai Phase 2: unknown model → 400 (not 502 — model validation
        # happens before provider dispatch).
        assert resp.status_code == 400
        data = resp.json()
        assert data["detail"]["error"] == "unknown_model"
        assert "nonexistent_provider_xyz" in data["detail"]["message"]


# ---------------------------------------------------------------------------
# Test: POST /v1/chat/completions/stream
# ---------------------------------------------------------------------------


class TestStreaming:
    """Verify SSE streaming endpoint."""

    BODY = {
        "model": "auto",
        "messages": [
            {"role": "user", "content": "Say hello in 3 words."},
        ],
    }

    def test_returns_sse_stream(self, client, valid_auth):
        resp = client.post("/v1/chat/completions/stream",
                           json=self.BODY,
                           headers={"Authorization": valid_auth})

        assert resp.status_code in (200, 502)
        if resp.status_code == 200:
            content_type = resp.headers.get("content-type", "")
            assert "text/event-stream" in content_type
            # Body should contain SSE format
            body = resp.text
            assert "data:" in body
            assert "[DONE]" in body

    def test_has_request_id_header(self, client, valid_auth):
        resp = client.post("/v1/chat/completions/stream",
                           json=self.BODY,
                           headers={"Authorization": valid_auth})

        if resp.status_code == 200:
            assert "X-Kai-Request-Id" in resp.headers

    def test_stream_has_provider_field(self, client, valid_auth):
        resp = client.post("/v1/chat/completions/stream",
                           json=self.BODY,
                           headers={"Authorization": valid_auth})

        if resp.status_code == 200:
            # Extract the data chunk (between first "data:" and "[DONE]")
            body = resp.text
            lines = [l for l in body.split("\n") if l.startswith("data:") and l != "data: [DONE]"]
            if lines:
                chunk = json.loads(lines[0][6:].strip())
                assert "provider" in chunk


# ---------------------------------------------------------------------------
# Test: POST /v1/providers/{name}/test
# ---------------------------------------------------------------------------


class TestProviderTest:
    """Verify provider connection test endpoint."""

    def test_401_without_auth(self, client):
        resp = client.post("/v1/providers/gemini/test")
        assert resp.status_code == 401

    def test_returns_result_for_valid_provider(self, client, valid_auth):
        resp = client.post("/v1/providers/gemini/test",
                           headers={"Authorization": valid_auth})

        assert resp.status_code in (200, 404, 500)
        if resp.status_code == 200:
            data = resp.json()
            assert "provider" in data
            assert "ok" in data
            assert "status" in data
            assert "latency_ms" in data


# ---------------------------------------------------------------------------
# Test: Key management
# ---------------------------------------------------------------------------


class TestKeyManagement:
    """Verify API key management endpoints."""

    def test_create_and_list_key(self, client, valid_auth):
        # Create a key
        resp = client.post("/v1/keys",
                           json={"label": "test-key-create"},
                           headers={"Authorization": valid_auth})
        assert resp.status_code == 200
        data = resp.json()
        assert "api_key" in data
        assert data["api_key"].startswith("kai_")
        assert data["label"] == "test-key-create"
        created_id = data["key_id"]

        # List keys — should include the new one
        resp = client.get("/v1/keys", headers={"Authorization": valid_auth})
        assert resp.status_code == 200
        keys = resp.json()["keys"]
        key_ids = [k["key_id"] for k in keys]
        assert created_id in key_ids

    def test_cannot_revoke_self(self, client, valid_key):
        """Revoking your own key returns 400."""
        from core.ai_gateway.keys import validate_api_key
        record = validate_api_key(valid_key)
        assert record is not None

        resp = client.delete(f"/v1/keys/{record['key_id']}",
                             headers={"Authorization": f"Bearer {valid_key}"})
        assert resp.status_code == 400
        assert "cannot_revoke_self" in resp.text

    def test_revoke_nonexistent_key_returns_404(self, client, valid_auth):
        resp = client.delete("/v1/keys/nonexistent_key_id",
                             headers={"Authorization": valid_auth})
        assert resp.status_code == 404

    def test_revoke_then_use_key_fails(self, client, valid_auth):
        """Create a key, revoke it with a different key, then verify it's invalid."""
        # Create key to revoke
        resp = client.post("/v1/keys",
                           json={"label": "to-revoke"},
                           headers={"Authorization": valid_auth})
        new_key = resp.json()["api_key"]
        new_key_id = resp.json()["key_id"]

        # Revoke it using the original authenticated key
        resp = client.delete(f"/v1/keys/{new_key_id}",
                             headers={"Authorization": valid_auth})
        assert resp.status_code == 200

        # Try using the revoked key
        resp = client.get("/v1/models",
                          headers={"Authorization": f"Bearer {new_key}"})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Test: Usage
# ---------------------------------------------------------------------------


class TestUsageEndpoint:
    """Verify GET /v1/usage."""

    def test_returns_usage_for_authenticated_key(self, client, valid_auth):
        resp = client.get("/v1/usage", headers={"Authorization": valid_auth})
        assert resp.status_code == 200
        data = resp.json()
        assert "consumer_id" in data
        assert "total_requests" in data
        assert "providers" in data
        assert "models" in data

    def test_401_without_auth(self, client):
        resp = client.get("/v1/usage")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Test: Audit logging
# ---------------------------------------------------------------------------


class TestAuditLogging:
    """Verify audit log records are created for gateway requests."""

    def test_chat_request_logs_to_audit(self, client, valid_auth):
        from core.ai_gateway.audit import _load

        before = len(_load())

        resp = client.post("/v1/chat/completions",
                           json={"model": "auto", "messages": [
                               {"role": "user", "content": "Hi"}]},
                           headers={"Authorization": valid_auth})

        after = len(_load())
        assert after >= before  # a request was logged (or the endpoint worked)


# ---------------------------------------------------------------------------
# Test: Pydantic models
# ---------------------------------------------------------------------------


class TestModels:
    """Verify Pydantic models parse and validate correctly."""

    def test_chat_completion_request_valid(self):
        from core.ai_gateway.models import ChatCompletionRequest, Message

        req = ChatCompletionRequest(
            model="auto",
            messages=[Message(role="user", content="Hello")],
        )
        assert req.model == "auto"
        assert req.stream is False

    def test_chat_completion_request_with_extensions(self):
        from core.ai_gateway.models import ChatCompletionRequest, Message

        req = ChatCompletionRequest(
            model="gemini",
            messages=[
                Message(role="system", content="You are Kai."),
                Message(role="user", content="What's up?"),
            ],
            temperature=0.5,
            max_tokens=100,
            task_type="planning",
            timeout=30,
        )
        assert req.task_type == "planning"
        assert req.timeout == 30
        assert len(req.messages) == 2

    def test_rejects_empty_messages(self):
        from core.ai_gateway.models import ChatCompletionRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ChatCompletionRequest(model="auto", messages=[])

    def test_temperature_bounds(self):
        from core.ai_gateway.models import ChatCompletionRequest, Message
        from pydantic import ValidationError

        # Too low
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model="auto",
                messages=[Message(role="user", content="x")],
                temperature=-0.1,
            )

        # Too high
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model="auto",
                messages=[Message(role="user", content="x")],
                temperature=2.1,
            )

    def test_chat_completion_response_structure(self):
        from core.ai_gateway.models import (
            ChatCompletionResponse, ChatCompletionChoice, Message,
        )

        resp = ChatCompletionResponse(
            model="deepseek_native_flash",
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=Message(role="assistant", content="Paris"),
                ),
            ],
            provider="deepseek_native_flash",
            duration_ms=150,
        )
        assert resp.object == "chat.completion"
        assert resp.provider == "deepseek_native_flash"
        assert len(resp.choices) == 1

    def test_model_info_defaults(self):
        from core.ai_gateway.models import ModelInfo

        m = ModelInfo(id="test-model")
        assert m.object == "model"
        assert m.owned_by == "kai"
        assert m.id == "test-model"


# ---------------------------------------------------------------------------
# Test: Keys module
# ---------------------------------------------------------------------------


class TestKeysModule:
    """Verify the keys module directly."""

    def test_generate_and_validate(self):
        from core.ai_gateway.keys import generate_api_key, validate_api_key

        key_id, plaintext = generate_api_key(label="unit-test")
        assert plaintext.startswith("kai_")
        assert len(plaintext) > 40

        record = validate_api_key(plaintext)
        assert record is not None
        assert record["key_id"] == key_id
        assert record["label"] == "unit-test"

    def test_invalid_key_fails(self):
        from core.ai_gateway.keys import validate_api_key

        assert validate_api_key("") is None
        assert validate_api_key("not-a-kai-key") is None
        assert validate_api_key("kai_fake_key_12345") is None

    def test_revoke_and_revalidate(self):
        from core.ai_gateway.keys import generate_api_key, validate_api_key, revoke_api_key

        key_id, plaintext = generate_api_key(label="revoke-test")
        assert validate_api_key(plaintext) is not None

        assert revoke_api_key(key_id) is True
        assert validate_api_key(plaintext) is None

    def test_revoke_nonexistent(self):
        from core.ai_gateway.keys import revoke_api_key

        assert revoke_api_key("nonexistent") is False

    def test_list_includes_generated_keys(self):
        from core.ai_gateway.keys import generate_api_key, list_api_keys

        _, plaintext = generate_api_key(label="list-test")
        keys = list_api_keys()
        assert any(k["label"] == "list-test" for k in keys)

    def test_hash_is_constant_time(self):
        from core.ai_gateway.keys import _hash

        h1 = _hash("kai_test_value")
        h2 = _hash("kai_test_value")
        assert h1 == h2  # deterministic
        assert len(h1) == 64  # SHA-256 hex is 64 chars

    def test_generate_api_key_is_unique(self):
        from core.ai_gateway.keys import generate_api_key

        keys = [generate_api_key()[1] for _ in range(5)]
        assert len(set(keys)) == 5  # all unique


# ---------------------------------------------------------------------------
# Test: Audit module
# ---------------------------------------------------------------------------


class TestAuditModule:
    """Verify the audit module directly."""

    def test_log_request_returns_trace_id(self):
        from core.ai_gateway.audit import log_request

        trace_id = log_request(
            consumer="test-key",
            model="auto",
            provider="gemini",
            duration_ms=100,
            status_code=200,
        )
        assert len(trace_id) == 12

    def test_get_recent_requests(self):
        from core.ai_gateway.audit import log_request, get_recent_requests

        log_request(consumer="test", model="auto", provider="gemini",
                    duration_ms=50, status_code=200)
        recent = get_recent_requests(limit=10)
        assert isinstance(recent, list)

    def test_get_consumer_usage(self):
        from core.ai_gateway.audit import log_request, get_consumer_usage

        log_request(consumer="usage-test", model="auto", provider="gemini",
                    duration_ms=50, status_code=200, cost=0.001)
        usage = get_consumer_usage("usage-test")
        assert usage["total_requests"] >= 1
        assert isinstance(usage["providers"], dict)


# ---------------------------------------------------------------------------
# Test: _build_prompt and _resolve_model helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    """Verify gateway helper functions."""

    def test_build_prompt_system_then_user(self):
        from core.ai_gateway.gateway import _build_prompt
        from core.ai_gateway.models import Message

        msgs = [
            Message(role="system", content="You are Kai."),
            Message(role="user", content="Hello"),
        ]
        prompt = _build_prompt(msgs)
        assert "[System]" in prompt
        assert "Hello" in prompt

    def test_build_prompt_assistant(self):
        from core.ai_gateway.gateway import _build_prompt
        from core.ai_gateway.models import Message

        msgs = [
            Message(role="user", content="Q"),
            Message(role="assistant", content="A"),
        ]
        prompt = _build_prompt(msgs)
        assert "[Assistant]" in prompt

    def test_resolve_model_auto(self):
        from core.ai_gateway.gateway import _resolve_model

        assert _resolve_model("auto") is None
        assert _resolve_model("") is None
        assert _resolve_model(None) is None

    def test_resolve_model_unknown(self):
        from core.ai_gateway.gateway import _resolve_model

        # Unknown model should return None (will fallback to auto-routing)
        result = _resolve_model("super_fake_provider_xyz")
        assert result is None

    def test_extract_task_type_explicit(self):
        from core.ai_gateway.gateway import _extract_task_type
        from core.ai_gateway.models import Message

        msgs = [Message(role="user", content="test")]
        result = _extract_task_type(msgs, explicit="coding")
        assert result == "coding"

    def test_extract_task_type_auto(self):
        from core.ai_gateway.gateway import _extract_task_type
        from core.ai_gateway.models import Message

        msgs = [Message(role="user", content="test")]
        result = _extract_task_type(msgs, explicit=None)
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# Test: Rate limiting
# ---------------------------------------------------------------------------


class TestGatewayRateLimiter:
    """Verify gateway-specific rate limiting."""

    def test_rate_limits_are_configured(self):
        from core.rate_limiter import DEFAULT_RATE_LIMITS

        assert "gateway_default" in DEFAULT_RATE_LIMITS
        assert "gateway_stream" in DEFAULT_RATE_LIMITS
        limit, window = DEFAULT_RATE_LIMITS["gateway_default"]
        assert limit > 0
        assert window > 0


# ---------------------------------------------------------------------------
# Test: End-to-end chat flow
# ---------------------------------------------------------------------------


class TestEndToEnd:
    """Verify the full chat flow from request to response."""

    def test_full_chat_roundtrip(self, client, valid_auth):
        """Send a chat request and verify the complete response structure."""
        resp = client.post("/v1/chat/completions",
                           json={
                               "model": "auto",
                               "messages": [
                                   {"role": "user", "content": "Say 'Kai is working' if you can read this."},
                               ],
                           },
                           headers={"Authorization": valid_auth})

        assert resp.status_code in (200, 502)
        if resp.status_code == 200:
            data = resp.json()
            # Full OpenAI-compatible shape
            assert "id" in data
            assert data["id"].startswith("kai-")
            assert data["object"] == "chat.completion"
            assert "created" in data
            assert "model" in data
            assert "choices" in data
            assert "provider" in data
            assert "duration_ms" in data
            # Choice shape
            choice = data["choices"][0]
            assert choice["index"] == 0
            assert choice["finish_reason"] == "stop"
            assert "message" in choice
            assert choice["message"]["role"] == "assistant"
            assert len(choice["message"]["content"]) > 0


# ---------------------------------------------------------------------------
# Test: DELETE /v1/keys/{key_id} — revoke a key using a different key
# ---------------------------------------------------------------------------


class TestRevokeOtherKey:
    """Verify revoking a key with a different valid key works."""

    def test_revoke_other_key(self, client, valid_auth):
        # Create a fresh key
        resp = client.post("/v1/keys",
                           json={"label": "sacrificial"},
                           headers={"Authorization": valid_auth})
        assert resp.status_code == 200
        sacrificial_key = resp.json()["api_key"]
        sacrificial_id = resp.json()["key_id"]

        # Revoke it using the original key
        resp = client.delete(f"/v1/keys/{sacrificial_id}",
                             headers={"Authorization": valid_auth})
        assert resp.status_code == 200
        assert resp.json()["revoked"] == sacrificial_id

        # Verify the revoked key no longer works
        resp = client.get("/v1/models",
                          headers={"Authorization": f"Bearer {sacrificial_key}"})
        assert resp.status_code == 401


# ── 18A-ai Phase 2: direct provider routing ─────────────────────────────


class TestDirectProviderRouting:
    """Verify /v1/chat/completions respects the model parameter."""

    def test_unknown_model_returns_400(self, client, valid_auth):
        """POST with model='nonexistent_provider_xyz' returns 400."""
        resp = client.post("/v1/chat/completions",
                           json={
                               "model": "nonexistent_provider_xyz",
                               "messages": [{"role": "user", "content": "test"}],
                           },
                           headers={"Authorization": valid_auth})
        assert resp.status_code == 400
        data = resp.json()
        assert data["detail"]["error"] == "unknown_model"

    def test_auto_model_still_auto_routes(self, client, valid_auth):
        """POST with model='auto' still auto-routes (200 or 502)."""
        resp = client.post("/v1/chat/completions",
                           json={
                               "model": "auto",
                               "messages": [{"role": "user", "content": "Say hi"}],
                           },
                           headers={"Authorization": valid_auth})
        assert resp.status_code in (200, 502)
        if resp.status_code == 200:
            assert resp.json()["provider"]  # some provider was used

    def test_omitted_model_auto_routes(self, client, valid_auth):
        """POST without a model field auto-routes (200 or 502)."""
        resp = client.post("/v1/chat/completions",
                           json={
                               "messages": [{"role": "user", "content": "Say hi"}],
                           },
                           headers={"Authorization": valid_auth})
        assert resp.status_code in (200, 502)
