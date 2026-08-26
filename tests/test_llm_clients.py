import pytest
import requests

import core.llm_clients as llm_clients


def _resp(status=200, json_body=None, headers=None):
    class FakeResp:
        status_code = status
        text = ""

        def __init__(self):
            self.headers = headers or {}

        def json(self):
            return json_body

        def raise_for_status(self):
            if status >= 400:
                raise requests.HTTPError(f"status {status}")

    return FakeResp()


def test_call_gemini_raises_when_key_missing(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(llm_clients.ProviderUnavailable):
        llm_clients.call_gemini("hello")


def test_call_gemini_extracts_text_from_response(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(
        llm_clients.requests, "post",
        lambda *a, **k: _resp(json_body={"candidates": [{"content": {"parts": [{"text": "hi there"}]}}]}),
    )

    assert llm_clients.call_gemini("hello") == "hi there"


def test_call_groq_raises_when_key_missing(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    with pytest.raises(llm_clients.ProviderUnavailable):
        llm_clients.call_groq("hello")


def test_call_groq_extracts_text_from_response(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr(
        llm_clients.requests, "post",
        lambda *a, **k: _resp(json_body={"choices": [{"message": {"content": "quick answer"}}]}),
    )

    assert llm_clients.call_groq("hello") == "quick answer"


def test_call_groq_captures_quota_from_response_headers(monkeypatch):
    import core.ai.provider_health as provider_health

    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr(
        llm_clients.requests, "post",
        lambda *a, **k: _resp(
            json_body={"choices": [{"message": {"content": "hi"}}]},
            headers={"x-ratelimit-remaining-tokens": "500", "x-ratelimit-limit-tokens": "1000"},
        ),
    )

    llm_clients.call_groq("hello")

    snapshot = provider_health.get_quota_snapshot("groq")
    assert snapshot["percent_remaining"] == 50.0


def test_call_openrouter_raises_when_key_missing(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with pytest.raises(llm_clients.ProviderUnavailable):
        llm_clients.call_openrouter("hello")


def test_call_openrouter_extracts_text_from_response(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(
        llm_clients.requests, "post",
        lambda *a, **k: _resp(json_body={"choices": [{"message": {"content": "openrouter says hi"}}]}),
    )

    assert llm_clients.call_openrouter("hello") == "openrouter says hi"


def test_call_minimax_raises_when_key_missing(monkeypatch):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)

    with pytest.raises(llm_clients.ProviderUnavailable):
        llm_clients.call_minimax("hello")


def test_call_minimax_extracts_text_from_response(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    monkeypatch.setattr(
        llm_clients.requests, "post",
        lambda *a, **k: _resp(json_body={"choices": [{"message": {"content": "minimax says hi"}}], "base_resp": {"status_code": 0}}),
    )

    assert llm_clients.call_minimax("hello") == "minimax says hi"


def test_call_minimax_raises_a_clear_error_on_business_level_failure(monkeypatch):
    # Minimax returns HTTP 200 even for business-logic failures like an
    # unsupported model or exhausted plan -- confirmed live -- so
    # raise_for_status() alone can't catch this; must check the body.
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    monkeypatch.setattr(
        llm_clients.requests, "post",
        lambda *a, **k: _resp(json_body={
            "choices": None,
            "base_resp": {"status_code": 2061, "status_msg": "your current token plan not support model, MiniMax-M2"},
        }),
    )

    with pytest.raises(RuntimeError, match="token plan"):
        llm_clients.call_minimax("hello")


def test_call_groq_never_leaks_key_in_raised_error(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "super-secret-value")

    def failing_post(*a, **k):
        raise requests.ConnectionError("timeout talking to groq")

    monkeypatch.setattr(llm_clients.requests, "post", failing_post)

    with pytest.raises(Exception) as excinfo:
        llm_clients.call_groq("hello")

    assert "super-secret-value" not in str(excinfo.value)


@pytest.mark.integration
@pytest.mark.external_api
def test_call_gemini_against_real_api():
    result = llm_clients.call_gemini("Reply with exactly the single word: pong")
    assert "pong" in result.lower()


@pytest.mark.integration
@pytest.mark.external_api
def test_call_groq_against_real_api():
    result = llm_clients.call_groq("Reply with exactly the single word: pong")
    assert "pong" in result.lower()


@pytest.mark.integration
@pytest.mark.external_api
def test_call_openrouter_against_real_api():
    result = llm_clients.call_openrouter("Reply with exactly the single word: pong")
    assert "pong" in result.lower()

# No live success-path integration test for Minimax: this account's plan
# doesn't support MiniMax-Text-01/M1, and MiniMax-M2 (the recognized model)
# returns "Token Plan usage limit reached" -- confirmed live, same
# unverified-on-success status as OPENAI_API_KEY (insufficient_quota).
# call_minimax's error-parsing path (business-logic failure via HTTP 200)
# *is* covered, by test_call_minimax_raises_a_clear_error_on_business_level_failure.


def test_call_deepseek_raises_when_key_missing(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_OPENROUTER_API_KEY", raising=False)

    with pytest.raises(llm_clients.ProviderUnavailable):
        llm_clients.call_deepseek("hello")


def test_call_deepseek_extracts_text_from_response(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(
        llm_clients.requests, "post",
        lambda *a, **k: _resp(json_body={"choices": [{"message": {"content": "deepseek says hi"}}]}),
    )

    assert llm_clients.call_deepseek("hello") == "deepseek says hi"


def test_call_deepseek_uses_dedicated_key_not_shared_openrouter_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_OPENROUTER_API_KEY", "dedicated-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "shared-key")

    captured = {}

    def capture_post(url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers", {})
        return _resp(json_body={"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(llm_clients.requests, "post", capture_post)

    llm_clients.call_deepseek("hello")

    assert "Bearer dedicated-key" in captured["headers"].get("Authorization", "")
    assert "shared-key" not in captured["headers"].get("Authorization", "")
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"


def test_call_deepseek_does_not_use_shared_openrouter_key_even_when_dedicated_key_is_missing(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "shared-key")
    monkeypatch.delenv("DEEPSEEK_OPENROUTER_API_KEY", raising=False)

    with pytest.raises(llm_clients.ProviderUnavailable, match="DEEPSEEK_OPENROUTER_API_KEY"):
        llm_clients.call_deepseek("hello")

# --- 13M: openrouter text-task model rotation list ---------------------------

def test_openrouter_models_contains_exactly_the_five_confirmed_live_models():
    assert llm_clients.OPENROUTER_MODELS == [
        "deepseek/deepseek-v4-flash",
        "openai/gpt-4o-mini",
        "z-ai/glm-5",
        "openai/gpt-5",
        "deepseek/deepseek-v4-pro",
    ]


def test_openrouter_models_prioritizes_deepseek_v4_flash_first():
    # Explicit user directive (2026-07-30): deepseek/deepseek-v4-flash is
    # tried before the rest of the rotation set.
    assert llm_clients.OPENROUTER_MODELS[0] == "deepseek/deepseek-v4-flash"


def test_openrouter_default_model_is_unchanged_for_back_compat():
    assert llm_clients.OPENROUTER_DEFAULT_MODEL == "openai/gpt-4o-mini"


def test_call_openrouter_still_defaults_to_the_default_model(monkeypatch):
    # Direct callers that pass no model kwarg keep getting
    # OPENROUTER_DEFAULT_MODEL -- rotation lives in
    # core.ai_provider._openrouter_run_text_task, not here.
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    captured = {}

    def fake_post(url, **kwargs):
        captured["model"] = kwargs["json"]["model"]
        return _resp(json_body={"choices": [{"message": {"content": "hi"}}]})

    monkeypatch.setattr(llm_clients.requests, "post", fake_post)

    llm_clients.call_openrouter("hello")

    assert captured["model"] == llm_clients.OPENROUTER_DEFAULT_MODEL


def test_call_openrouter_passes_an_explicit_model_through(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    captured = {}

    def fake_post(url, **kwargs):
        captured["model"] = kwargs["json"]["model"]
        return _resp(json_body={"choices": [{"message": {"content": "hi"}}]})

    monkeypatch.setattr(llm_clients.requests, "post", fake_post)

    llm_clients.call_openrouter("hello", model="z-ai/glm-5")

    assert captured["model"] == "z-ai/glm-5"


@pytest.mark.integration
@pytest.mark.external_api
def test_call_openrouter_deepseek_v4_flash_against_real_api():
    # Codifies the 2026-07-28 live verification of the new rotation models
    # (matching the existing test_call_openrouter_against_real_api pattern).
    result = llm_clients.call_openrouter(
        "Reply with exactly the single word: pong", model="deepseek/deepseek-v4-flash"
    )
    assert "pong" in result.lower()


# ── 2026-08-26: per-call token usage capture (cost tracker feed) ──────────

def test_pop_last_usage_returns_none_when_nothing_captured():
    assert llm_clients.pop_last_usage() is None


def test_call_groq_captures_openai_usage_block(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr(
        llm_clients.requests, "post",
        lambda *a, **k: _resp(json_body={
            "choices": [{"message": {"content": "hi"}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 45},
        }),
    )

    assert llm_clients.call_groq("hello") == "hi"
    assert llm_clients.pop_last_usage() == {"prompt_tokens": 120, "completion_tokens": 45}
    # Popping clears it -- a second pop must not replay the stale numbers.
    assert llm_clients.pop_last_usage() is None


def test_call_gemini_captures_usage_metadata(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(
        llm_clients.requests, "post",
        lambda *a, **k: _resp(json_body={
            "candidates": [{"content": {"parts": [{"text": "gemini hi"}]}}],
            "usageMetadata": {"promptTokenCount": 200, "candidatesTokenCount": 30},
        }),
    )

    assert llm_clients.call_gemini("hello") == "gemini hi"
    assert llm_clients.pop_last_usage() == {"prompt_tokens": 200, "completion_tokens": 30}


def test_call_ollama_qwen_captures_eval_counts(monkeypatch):
    class FakeOllamaResp:
        status_code = 200
        def json(self):
            return {
                "response": "local answer",
                "prompt_eval_count": 88,
                "eval_count": 21,
            }
        def raise_for_status(self):
            pass

    monkeypatch.setattr(llm_clients, "check_ollama_available", lambda: True)
    monkeypatch.setattr(llm_clients.requests, "post", lambda *a, **k: FakeOllamaResp())

    assert llm_clients.call_ollama_qwen("hello") == "local answer"
    assert llm_clients.pop_last_usage() == {"prompt_tokens": 88, "completion_tokens": 21}


def test_response_without_usage_leaves_capture_empty(monkeypatch):
    # Providers that omit the usage block must NOT fabricate zero-token
    # entries -- pop stays None and record_usage records usage=None.
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr(
        llm_clients.requests, "post",
        lambda *a, **k: _resp(json_body={"choices": [{"message": {"content": "hi"}}]}),
    )

    llm_clients.call_groq("hello")
    assert llm_clients.pop_last_usage() is None
