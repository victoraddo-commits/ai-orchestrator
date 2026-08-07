import pytest

import core.ai.chief_architect as chief_architect
from core.ai.ai_router import AllProvidersFailed


def test_primary_success_no_fallback_recorded(monkeypatch):
    def fake_delegate(description, task_type=None, return_attempts=False, **kwargs):
        result = {
            "provider": "claude",
            "task_type": "architecture",
            "response": "plan done",
            "duration_ms": 100,
        }
        if return_attempts:
            result["attempts"] = []
        return result

    monkeypatch.setattr(chief_architect, "delegate", fake_delegate)

    result = chief_architect.delegate_chief_architect("design the system")

    assert result["provider"] == "claude"
    history = chief_architect.get_architecture_history()
    assert len(history) == 1
    record = history[0]
    assert record["task"] == "architecture planning"
    assert record["primary"] == "claude"
    assert record["fallback_used"] is None
    assert record["reason"] == ""
    assert record["result"] == "success"


def test_one_level_fallback(monkeypatch):
    def fake_delegate(description, task_type=None, return_attempts=False, **kwargs):
        result = {
            "provider": "gemini",
            "task_type": "architecture",
            "response": "plan from gemini",
            "duration_ms": 150,
        }
        if return_attempts:
            result["attempts"] = [
                {"provider": "claude", "error_type": "quota_exceeded", "error": "claude quota exhausted"},
            ]
        return result

    monkeypatch.setattr(chief_architect, "delegate", fake_delegate)

    result = chief_architect.delegate_chief_architect("design the system")

    assert result["provider"] == "gemini"
    history = chief_architect.get_architecture_history()
    assert len(history) == 1
    record = history[0]
    assert record["task"] == "architecture planning"
    assert record["primary"] == "claude"
    assert record["fallback_used"] == "gemini"
    assert record["reason"] == "claude quota exhausted"
    assert record["result"] == "success"


def test_full_chain_exhaustion(monkeypatch):
    attempts = [
        {"provider": "claude", "error_type": "quota_exceeded", "error": "claude quota exhausted"},
        {"provider": "gemini", "error_type": "timeout", "error": "gemini timed out"},
        {"provider": "deepseek", "error_type": "unavailable", "error": "not available (no credentials configured)"},
        {"provider": "openrouter_claude", "error_type": "error", "error": "request failed: ConnectionError"},
        {"provider": "qwen4_text", "error_type": "degraded_health", "error": "openai error"},
    ]

    def fake_delegate(description, task_type=None, return_attempts=False, **kwargs):
        raise AllProvidersFailed("all providers failed", attempts=attempts)

    monkeypatch.setattr(chief_architect, "delegate", fake_delegate)

    with pytest.raises(AllProvidersFailed):
        chief_architect.delegate_chief_architect("design the system")

    history = chief_architect.get_architecture_history()
    assert len(history) == 1
    record = history[0]
    assert record["task"] == "architecture planning"
    assert record["primary"] == "claude"
    assert record["fallback_used"] is None
    assert record["reason"] == "openai degraded health"
    assert record["result"] == "failure"


def test_timeout_fallback_reason(monkeypatch):
    def fake_delegate(description, task_type=None, return_attempts=False, **kwargs):
        result = {
            "provider": "gemini",
            "task_type": "architecture",
            "response": "plan from gemini",
            "duration_ms": 150,
        }
        if return_attempts:
            result["attempts"] = [
                {"provider": "claude", "error_type": "timeout", "error": "request failed: ReadTimeout"},
            ]
        return result

    monkeypatch.setattr(chief_architect, "delegate", fake_delegate)

    chief_architect.delegate_chief_architect("design the system")

    record = chief_architect.get_architecture_history()[-1]
    assert record["reason"] == "claude timeout"
    assert record["result"] == "success"


def test_multiple_calls_create_separate_records(monkeypatch):
    call_number = {"n": 0}

    def fake_delegate(description, task_type=None, return_attempts=False, **kwargs):
        call_number["n"] += 1
        result = {
            "provider": "claude",
            "task_type": "architecture",
            "response": f"plan {call_number['n']}",
            "duration_ms": 100,
        }
        if return_attempts:
            result["attempts"] = []
        return result

    monkeypatch.setattr(chief_architect, "delegate", fake_delegate)

    chief_architect.delegate_chief_architect("task 1")
    chief_architect.delegate_chief_architect("task 2")
    chief_architect.delegate_chief_architect("task 3")

    history = chief_architect.get_architecture_history()
    assert len(history) == 3
    for record in history:
        assert record["task"] == "architecture planning"
        assert record["primary"] == "claude"
        assert record["result"] == "success"


def test_get_architecture_history_returns_empty_list_for_no_records():
    assert chief_architect.get_architecture_history() == []


def test_delegate_chief_architect_passes_kwargs_through(monkeypatch):
    captured = {}

    def fake_delegate(description, task_type=None, return_attempts=False, **kwargs):
        captured["kwargs"] = kwargs
        result = {
            "provider": "claude",
            "task_type": "architecture",
            "response": "done",
            "duration_ms": 100,
        }
        if return_attempts:
            result["attempts"] = []
        return result

    monkeypatch.setattr(chief_architect, "delegate", fake_delegate)

    chief_architect.delegate_chief_architect("design", timeout=90, capability="text_task")

    assert captured["kwargs"]["timeout"] == 90
    assert captured["kwargs"]["capability"] == "text_task"


def test_fallback_used_is_none_when_no_provider_failed_but_record_has_empty_attempts(monkeypatch):
    def fake_delegate(description, task_type=None, return_attempts=False, **kwargs):
        result = {
            "provider": "claude",
            "task_type": "architecture",
            "response": "done",
            "duration_ms": 100,
            "attempts": [],  # return_attempts=True adds this
        }
        return result

    monkeypatch.setattr(chief_architect, "delegate", fake_delegate)

    chief_architect.delegate_chief_architect("design")

    record = chief_architect.get_architecture_history()[-1]
    assert record["fallback_used"] is None
    assert record["reason"] == ""
    assert record["result"] == "success"


def test_full_chain_exhaustion_with_empty_attempts(monkeypatch):
    def fake_delegate(description, task_type=None, return_attempts=False, **kwargs):
        raise AllProvidersFailed("all providers failed", attempts=[])

    monkeypatch.setattr(chief_architect, "delegate", fake_delegate)

    with pytest.raises(AllProvidersFailed):
        chief_architect.delegate_chief_architect("design")

    record = chief_architect.get_architecture_history()[-1]
    assert record["reason"] == "all providers failed"
    assert record["result"] == "failure"