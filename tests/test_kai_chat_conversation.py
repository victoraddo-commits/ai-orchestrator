"""Kai bot must converse normally, not dump system status on every message.

2026-09-23 owner directive: a plain "hello" was answered with a full system
status report because ``handle_kai_chat`` unconditionally injected
``gather_signals()`` into the prompt. Status/health context is now included
only when the operator explicitly asks for it; plain conversation goes to the
local LLM as ordinary chat.
"""
from __future__ import annotations

import pytest

import core.api as api
from core.kai import conversation, planner


# ── status-intent detection ────────────────────────────────────────────────
@pytest.mark.parametrize("text", [
    "hello",
    "hi there",
    "what can you do?",
    "write me a poem",
    "tell me a joke about servers",
    "thanks!",
])
def test_plain_conversation_is_not_a_status_request(text):
    assert planner.is_status_request(text) is False


@pytest.mark.parametrize("text", [
    "what's the system status?",
    "system health?",
    "how are you?",
    "any incidents?",
    "status",
    "what is wrong?",
    "is everything ok?",
    "give me diagnostics",
])
def test_explicit_status_questions_are_detected(text):
    assert planner.is_status_request(text) is True


def test_status_request_rejects_empty():
    assert planner.is_status_request("") is False
    assert planner.is_status_request(None) is False


# ── prompt builder gating ──────────────────────────────────────────────────
def _isolate_prompt_files(monkeypatch):
    monkeypatch.setattr(conversation, "CHAT_HISTORY_FILE", "nonexistent.json")
    monkeypatch.setattr(conversation, "OPERATOR_LONG_TERM_FILE", "nonexistent_lt.json")


def test_build_chat_prompt_omits_state_when_no_status_signals(monkeypatch):
    _isolate_prompt_files(monkeypatch)
    prompt = conversation.build_chat_prompt([], {})
    assert "Current system state" not in prompt


def test_build_chat_prompt_ignores_non_status_signals(monkeypatch):
    _isolate_prompt_files(monkeypatch)
    prompt = conversation.build_chat_prompt([], {"knowledge_context": "some rag text"})
    assert "Current system state" not in prompt


def test_build_chat_prompt_includes_state_for_status_signals(monkeypatch):
    _isolate_prompt_files(monkeypatch)
    prompt = conversation.build_chat_prompt([], {"health_findings": ["docker down"]})
    assert "Current system state" in prompt
    assert "docker down" in prompt


# ── handle_kai_chat end-to-end (no disk, no network) ───────────────────────
def _wire_chat(monkeypatch):
    captured = {}
    calls = {"gather": 0}

    monkeypatch.setattr(api, "_append_chat_message", lambda role, content: None)
    monkeypatch.setattr(api, "_get_chat_messages", lambda: [])
    monkeypatch.setattr(api, "kai_dispatch", lambda text, **kwargs: {"matched": False})
    monkeypatch.setattr(api, "_extract_build_intent", lambda text: None)

    def fake_gather():
        calls["gather"] += 1
        return {"health_findings": ["docker critical"],
                "roadmap_progress": {"done": 1}}

    monkeypatch.setattr(api, "gather_signals", fake_gather)

    def fake_chat(history, signals):
        captured["signals"] = signals
        return "a normal conversational answer"

    monkeypatch.setattr(api, "ai_chat", fake_chat)
    return captured, calls


def test_greeting_does_not_dump_status(monkeypatch):
    captured, calls = _wire_chat(monkeypatch)

    reply = api.handle_kai_chat("hello", operator="op")

    assert reply["matched"] is False
    assert reply["response"] == "a normal conversational answer"
    assert calls["gather"] == 0, "gather_signals must not run for plain chat"
    assert not (planner.STATUS_SIGNAL_KEYS & set(captured["signals"])), \
        "plain chat must not send system-status signals"


def test_explicit_status_question_includes_status(monkeypatch):
    captured, calls = _wire_chat(monkeypatch)

    reply = api.handle_kai_chat("what's the system status?", operator="op")

    assert reply["matched"] is False
    assert calls["gather"] == 1, "explicit status question must gather signals"
    assert captured["signals"]["health_findings"] == ["docker critical"]
