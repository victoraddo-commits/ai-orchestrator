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


def test_call_openai_raises_when_key_missing(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(llm_clients.ProviderUnavailable):
        llm_clients.call_openai("hello")


def test_call_openai_extracts_text_from_response(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        llm_clients.requests, "post",
        lambda *a, **k: _resp(json_body={"choices": [{"message": {"content": "an answer"}}]}),
    )

    assert llm_clients.call_openai("hello") == "an answer"


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


def test_call_openai_captures_quota_exceeded_on_429(monkeypatch):
    import core.ai.provider_health as provider_health

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        llm_clients.requests, "post",
        lambda *a, **k: _resp(status=429, json_body={"error": {"code": "insufficient_quota"}}),
    )

    with pytest.raises(Exception):
        llm_clients.call_openai("hello")

    snapshot = provider_health.get_quota_snapshot("openai")
    assert snapshot["status"] == "quota_exceeded"
    assert snapshot["percent_remaining"] == 0


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
def test_call_gemini_against_real_api():
    result = llm_clients.call_gemini("Reply with exactly the single word: pong")
    assert "pong" in result.lower()


@pytest.mark.integration
def test_call_groq_against_real_api():
    result = llm_clients.call_groq("Reply with exactly the single word: pong")
    assert "pong" in result.lower()


@pytest.mark.integration
def test_call_openrouter_against_real_api():
    result = llm_clients.call_openrouter("Reply with exactly the single word: pong")
    assert "pong" in result.lower()

# No live success-path integration test for Minimax: this account's plan
# doesn't support MiniMax-Text-01/M1, and MiniMax-M2 (the recognized model)
# returns "Token Plan usage limit reached" -- confirmed live, same
# unverified-on-success status as OPENAI_API_KEY (insufficient_quota).
# call_minimax's error-parsing path (business-logic failure via HTTP 200)
# *is* covered, by test_call_minimax_raises_a_clear_error_on_business_level_failure.
