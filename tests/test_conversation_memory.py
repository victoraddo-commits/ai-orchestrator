"""Phase 17V: Kai conversation memory tests — session envelopes, long-term
store, and guarded compression."""

import json
import pytest
from core.kai import conversation


# ── Session envelope ───────────────────────────────────────────────────

def test_add_message_creates_session_envelope(tmp_path, monkeypatch):
    monkeypatch.setattr(conversation, "CHAT_HISTORY_FILE", "test_chat.json")
    conversation.add_message("user", "Hello Kai", directory=tmp_path)
    envelope = conversation.get_session(directory=tmp_path)
    assert envelope["schema_version"] == 2
    assert "session" in envelope
    assert "recent_messages" in envelope
    assert "compressed" in envelope
    assert len(envelope["recent_messages"]) == 1
    assert envelope["recent_messages"][0]["role"] == "user"
    assert envelope["recent_messages"][0]["content"] == "Hello Kai"


def test_active_goal_extracted_from_user_message(tmp_path, monkeypatch):
    monkeypatch.setattr(conversation, "CHAT_HISTORY_FILE", "test_chat.json")
    conversation.add_message("assistant", "What would you like to build?", directory=tmp_path)
    conversation.add_message("user", "Kai, build me a website for my restaurant.", directory=tmp_path)
    envelope = conversation.get_session(directory=tmp_path)
    assert envelope["session"]["active_goal"] is not None
    assert "build" in envelope["session"]["active_goal"].lower()


# ── Long-term memory ───────────────────────────────────────────────────

def test_remember_fact_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(conversation, "OPERATOR_LONG_TERM_FILE", "test_lt.json")
    conversation.remember_fact("favorite color", "blue", directory=tmp_path)
    ctx = conversation.get_long_term_context(directory=tmp_path)
    assert "favorite color" in ctx
    assert "blue" in ctx


def test_remember_directive_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(conversation, "OPERATOR_LONG_TERM_FILE", "test_lt.json")
    conversation.remember_directive("Always run tests before deploying", directory=tmp_path)
    ctx = conversation.get_long_term_context(directory=tmp_path)
    assert "Always run tests before deploying" in ctx


def test_long_term_empty_returns_empty_string(tmp_path, monkeypatch):
    monkeypatch.setattr(conversation, "OPERATOR_LONG_TERM_FILE", "test_lt.json")
    ctx = conversation.get_long_term_context(directory=tmp_path)
    assert ctx == ""


def test_long_term_context_not_injected_when_empty(monkeypatch):
    """build_chat_prompt should not inject empty long-term context."""
    # No long-term file exists — should still build a valid prompt
    monkeypatch.setattr(conversation, "CHAT_HISTORY_FILE", "nonexistent.json")
    monkeypatch.setattr(conversation, "OPERATOR_LONG_TERM_FILE", "nonexistent_lt.json")
    prompt = conversation.build_chat_prompt([], {"status": "ok"})
    assert "Kai" in prompt
    assert "Operator" not in prompt.split("##")[0]  # No long-term section header


def test_remember_fact_upserts(tmp_path, monkeypatch):
    monkeypatch.setattr(conversation, "OPERATOR_LONG_TERM_FILE", "test_lt.json")
    conversation.remember_fact("project", "old value", directory=tmp_path)
    conversation.remember_fact("project", "new value", directory=tmp_path)
    ctx = conversation.get_long_term_context(directory=tmp_path)
    assert "old value" not in ctx
    assert "new value" in ctx


# ── Compression ────────────────────────────────────────────────────────

def _build_long_conversation(n, directory, monkeypatch):
    monkeypatch.setattr(conversation, "CHAT_HISTORY_FILE", "test_chat.json")
    for i in range(n):
        conversation.add_message("user", f"Question number {i}", directory=directory)
        conversation.add_message("assistant", f"Answer number {i}", directory=directory)


def test_compression_triggers_above_threshold(tmp_path, monkeypatch):
    # Build enough messages to trigger compression (>30)
    _build_long_conversation(16, tmp_path, monkeypatch)  # 32 messages > 30

    envelope = conversation.get_session(directory=tmp_path)
    messages = envelope["recent_messages"]
    # After compression, recent messages should be <= threshold
    assert len(messages) <= conversation.COMPRESSION_THRESHOLD
    # Older summary should exist
    assert envelope["compressed"]["older_summary"] is not None


def test_compression_preserves_recent_turns_verbatim(tmp_path, monkeypatch):
    _build_long_conversation(20, tmp_path, monkeypatch)

    envelope = conversation.get_session(directory=tmp_path)
    messages = envelope["recent_messages"]

    # Last two turns should be the most recent ones, verbatim
    assert messages[-2]["content"] == "Question number 19"
    assert messages[-1]["content"] == "Answer number 19"


def test_citations_survive_compression(tmp_path, monkeypatch):
    """Non-negotiable: exact statutory citations must survive compression."""
    monkeypatch.setattr(conversation, "CHAT_HISTORY_FILE", "test_chat.json")

    # Add the legal citation messages EARLY so they fall in the older block
    conversation.add_message("user",
        "Under the Constitution of Ghana, Article 12(3), and Act 123 of 2020, "
        "as interpreted in Donoghue v Stevenson [1932] AC 562, the Supreme Court "
        "of Ghana held in Brown v Attorney-General [2010] SCGLR 183 that...",
        directory=tmp_path)
    conversation.add_message("assistant", "Citing those authorities...", directory=tmp_path)

    # Then fill with enough generic messages to trigger compression
    for i in range(15):
        conversation.add_message("user", f"General chat {i}", directory=tmp_path)
        conversation.add_message("assistant", f"Response {i}", directory=tmp_path)

    envelope = conversation.get_session(directory=tmp_path)
    key_entities = envelope["compressed"].get("key_entities", [])

    # Citations must be in the preserved entities
    citation_values = [e["value"] for e in key_entities if e["type"] == "citation"]
    combined = " ".join(citation_values)

    # These specific citations must survive
    assert "Article 12" in combined, f"Article 12 missing from: {citation_values}"
    assert "Act 123" in combined, f"Act 123 missing from: {citation_values}"
    assert "Donoghue" in combined, f"Donoghue missing from: {citation_values}"
    assert "SCGLR 183" in combined, f"SCGLR 183 missing from: {citation_values}"
    assert "Supreme Court" in combined, f"Supreme Court missing from: {citation_values}"


def test_operator_directives_survive_compression(tmp_path, monkeypatch):
    """Non-negotiable: operator 'always'/'never' directives survive compression."""
    monkeypatch.setattr(conversation, "CHAT_HISTORY_FILE", "test_chat.json")

    # Add directive messages EARLY so they fall in the older compressed block
    conversation.add_message("user",
        "Always run the full test suite before deploying to production. "
        "Never approve a build that doesn't have passing tests.",
        directory=tmp_path)
    conversation.add_message("assistant", "Understood.", directory=tmp_path)

    for i in range(15):
        conversation.add_message("user", f"Chat {i}", directory=tmp_path)
        conversation.add_message("assistant", f"Response {i}", directory=tmp_path)

    envelope = conversation.get_session(directory=tmp_path)
    key_entities = envelope["compressed"].get("key_entities", [])

    directive_values = [e["value"] for e in key_entities if e["type"] == "directive"]
    combined = " ".join(directive_values)

    assert "full test suite" in combined
    assert "deploying" in combined


def test_build_chat_prompt_includes_long_term_and_session(tmp_path, monkeypatch):
    monkeypatch.setattr(conversation, "CHAT_HISTORY_FILE", "test_chat.json")
    monkeypatch.setattr(conversation, "OPERATOR_LONG_TERM_FILE", "test_lt.json")

    conversation.remember_fact("project", "ai-orchestrator", directory=tmp_path)
    conversation.add_message("user", "Build a dashboard", directory=tmp_path)

    prompt = conversation.build_chat_prompt([], {"status": "ok"}, directory=tmp_path)

    assert "ai-orchestrator" in prompt  # Long-term fact injected
    assert "Build a dashboard" in prompt  # Recent message included
    assert "Kai" in prompt  # System prompt present


def test_no_per_user_scoping(monkeypatch):
    """Structural guarantee: this module must not import or reference
    core.authz, core/accounts.json, or any per-user concept.  Phase 15A
    is explicitly out of scope for 17V."""
    import importlib, inspect

    source = inspect.getsource(conversation)
    assert "authz" not in source.lower(), "17V must not depend on authz"
    assert "account" not in source.lower(), "17V must not depend on accounts"
    assert "15A" not in source, "17V must not reference Phase 15A"
