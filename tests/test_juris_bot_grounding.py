"""Strict-grounding tests for the Juris Kai bot free-text path.

Owner rule: no legal substance without a retrieved source. An UNGROUNDED
question must return the refusal text WITHOUT ever calling the model, while
GROUNDED/PARTIAL answers carry the deterministic Sources footer (plus a
banner for PARTIAL) even when the answer is streamed to Telegram.

Also covers the follow-up review fixes:
  * refusals are recorded for learning but are NEVER cacheable / replayable;
  * a cached grounded answer is only reused for the *same* source set
    (source signature in the FAQ + generation keys);
  * retrieval failure fails closed (refusal, no model call);
  * non-streamed sends retry as plain text when Markdown parsing fails.

Isolation mirrors tests/test_juris_learning_loop.py: per-test temp DB, reset
singletons, temp session store.
"""

import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault(
    "JURIS_KAI_DB_DIR",
    str(Path(tempfile.gettempdir()) / "juris_kai_test"),
)

import core.juris_kai.bot as bot  # noqa: E402
from core.juris_kai import cache, grounding  # noqa: E402

DOCS = [{
    "id": 101,
    "title": "Criminal Offences Act, 1960",
    "citation": "Act 29",
    "year": 1960,
    "store_mode": "full",
    "chunk_content": "Stealing is defined in section 124. " * 20,
}]

DOCS_B = [{
    "id": 202,
    "title": "Contracts Act, 1960",
    "citation": "Act 25",
    "year": 1960,
    "store_mode": "full",
    "chunk_content": "A contract requires offer and acceptance. " * 20,
}]

LONG_ANSWER = (
    "Under the Criminal Offences Act 1960 (Act 29), section 124 defines "
    "stealing. The Supreme Court has held that the prosecution must prove "
    "the essential elements beyond reasonable doubt under Ghanaian law."
)


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Per-test DB + reset caches/singletons (same pattern as learning loop)."""
    db_dir = tmp_path / "juris_db"
    db_dir.mkdir()
    import core.juris_kai.accounts as accts
    monkeypatch.setattr(accts, "DB_DIR", str(db_dir))
    monkeypatch.setattr(accts, "DB_PATH", str(db_dir / "juris_kai_accounts.db"))
    accts._account_manager = None

    cache.clear_caches()

    import core.juris_kai.session as session
    monkeypatch.setattr(session, "STORAGE_PATH", tmp_path / "sessions.json")

    yield
    accts._account_manager = None
    cache.clear_caches()


def _account(telegram_id=None):
    from core.juris_kai.accounts import get_account_manager
    mgr = get_account_manager()
    tid = str(telegram_id) if telegram_id is not None else str(uuid.uuid4().int)[:9]
    acct = mgr.get_or_create(tid, "Tester")
    mgr.accept_disclaimer(acct["account_id"])
    return acct


def _retrieval(verdict, docs=None):
    return {"docs": list(docs or []), "verdict": verdict, "stage": 1}


def _failing_generate(*args, **kwargs):
    raise AssertionError("_generate_reply must NOT be called for UNGROUNDED")


def _fake_reply(prompt, task_type, query, fallback_label, account_id="",
                chat_id=None, reply_markup=None, context="", prefix="",
                suffix="", source_key=""):
    return "The answer.", "m", False, False


# ---------------------------------------------------------------------------
# UNGROUNDED — honest refusal, no model call, turn still recorded
# ---------------------------------------------------------------------------


def test_ungrounded_returns_refusal_without_model(monkeypatch):
    acct = _account(900001)
    monkeypatch.setattr(grounding, "retrieve", lambda q, limit=3: _retrieval("UNGROUNDED"))
    monkeypatch.setattr(bot, "_generate_reply", _failing_generate)

    resp = bot._build_legal_reply("quantum entanglement tax", 900001, acct)

    assert resp["text"] == bot.UNGROUNDED_REPLY
    assert "won't guess" in resp["text"]
    assert resp["parse_mode"] == "Markdown"


def test_out_of_scope_question_refused_without_model(monkeypatch):
    acct = _account(900777)
    # Retrieval would ground this, but the pre-model jurisdiction check must
    # refuse it before the model is ever called.
    monkeypatch.setattr(grounding, "retrieve", lambda q, limit=3: _retrieval("GROUNDED", DOCS))
    monkeypatch.setattr(bot, "_generate_reply", _failing_generate)

    resp = bot._build_legal_reply("What is the law in Nigeria?", 900777, acct)

    assert resp["text"] == bot.JURISDICTION_REFUSAL
    assert "Ghana" in resp["text"]


def test_ungrounded_records_turn_for_learning_loop(monkeypatch):
    from core.juris_kai.accounts import get_account_manager

    acct = _account(900002)
    monkeypatch.setattr(grounding, "retrieve", lambda q, limit=3: _retrieval("UNGROUNDED"))
    monkeypatch.setattr(bot, "_generate_reply", _failing_generate)

    bot._build_legal_reply("quantum entanglement tax", 900002, acct)

    rows = get_account_manager().qa_history(acct["account_id"])
    assert len(rows) == 1
    assert rows[0]["question"] == "quantum entanglement tax"
    assert rows[0]["answer"] == bot.UNGROUNDED_REPLY


def test_ungrounded_refusal_is_recorded_but_not_cacheable(monkeypatch):
    from core.juris_kai.accounts import get_account_manager

    acct = _account(900002)
    q = "quantum entanglement tax"
    monkeypatch.setattr(grounding, "retrieve", lambda query, limit=3: _retrieval("UNGROUNDED"))
    monkeypatch.setattr(bot, "_generate_reply", _failing_generate)

    bot._build_legal_reply(q, 900002, acct)

    assert cache.answer_is_cacheable(bot.UNGROUNDED_REPLY) is False
    assert cache.answer_is_refusal(bot.UNGROUNDED_REPLY) is True

    rows = get_account_manager().qa_history(acct["account_id"])
    assert rows[0]["cache_eligible"] == 0
    # Never served back as a reusable answer.
    assert get_account_manager().lookup_qa(
        acct["account_id"], bot.LEGAL_GROUNDING_TASK, q) is None


def test_ungrounded_then_grounded_is_not_replayed(monkeypatch):
    acct = _account(900010)
    q = "quantum entanglement tax"
    monkeypatch.setattr(bot, "_generate_reply", _failing_generate)

    monkeypatch.setattr(grounding, "retrieve", lambda query, limit=3: _retrieval("UNGROUNDED"))
    first = bot._build_legal_reply(q, 900010, acct)
    assert first["text"] == bot.UNGROUNDED_REPLY

    # Same question now grounds: the stored refusal must not be replayed.
    calls = {"n": 0}

    def fake_reply(prompt, task_type, query, fallback_label, account_id="",
                   chat_id=None, reply_markup=None, context="", prefix="",
                   suffix="", source_key=""):
        calls["n"] += 1
        return "Grounded answer.", "m", False, False

    monkeypatch.setattr(grounding, "retrieve", lambda query, limit=3: _retrieval("GROUNDED", DOCS))
    monkeypatch.setattr(bot, "_generate_reply", fake_reply)

    second = bot._build_legal_reply(q, 900010, acct)

    assert calls["n"] == 1
    assert bot.UNGROUNDED_REPLY not in second["text"]
    assert grounding.build_sources_footer(DOCS) in second["text"]


def test_retrieve_raises_fails_closed(monkeypatch):
    from core.juris_kai.accounts import get_account_manager

    acct = _account(900011)

    def boom(query, limit=3):
        raise RuntimeError("legal brain unreachable")

    monkeypatch.setattr(grounding, "retrieve", boom)
    monkeypatch.setattr(bot, "_generate_reply", _failing_generate)

    resp = bot._build_legal_reply("Criminal Offences Act 1960", 900011, acct)

    assert resp["text"] == bot.UNGROUNDED_REPLY
    rows = get_account_manager().qa_history(acct["account_id"])
    assert len(rows) == 1
    assert rows[0]["answer"] == bot.UNGROUNDED_REPLY
    assert rows[0]["cache_eligible"] == 0


def test_handle_free_text_ungrounded_end_to_end(monkeypatch):
    acct = _account(900006)
    monkeypatch.setattr(grounding, "_search", lambda *a, **k: [])
    monkeypatch.setattr(bot, "_generate_reply", _failing_generate)

    resp = bot._handle_free_text(
        "what is the tax on quantum entanglement", 900006, acct, False)

    assert resp["text"] == bot.UNGROUNDED_REPLY


# ---------------------------------------------------------------------------
# GROUNDED / PARTIAL — grounded prompt + footer/banner
# ---------------------------------------------------------------------------


def test_grounded_appends_footer_and_uses_grounded_prompt(monkeypatch):
    acct = _account(900003)
    monkeypatch.setattr(grounding, "retrieve", lambda q, limit=3: _retrieval("GROUNDED", DOCS))
    captured = {}

    def fake_reply(prompt, task_type, query, fallback_label, account_id="",
                   chat_id=None, reply_markup=None, context="", prefix="",
                   suffix="", source_key=""):
        captured.update(prompt=prompt, prefix=prefix, suffix=suffix,
                        source_key=source_key)
        return "The answer.", "m", False, False

    monkeypatch.setattr(bot, "_generate_reply", fake_reply)

    resp = bot._build_legal_reply("Criminal Offences Act", 900003, acct)

    assert "SOURCE 1: Criminal Offences Act, 1960 (Act 29)" in captured["prompt"]
    assert "Cite ONLY the sources below" in captured["prompt"]
    footer = grounding.build_sources_footer(DOCS)
    assert captured["prefix"] == ""
    assert footer in captured["suffix"]
    assert captured["source_key"] == grounding.source_signature(DOCS, "GROUNDED")
    assert resp["text"] == "The answer." + footer
    assert bot.PARTIAL_BANNER not in resp["text"]


def test_partial_prefixes_banner_and_appends_footer(monkeypatch):
    acct = _account(900004)
    monkeypatch.setattr(grounding, "retrieve", lambda q, limit=3: _retrieval("PARTIAL", DOCS))
    monkeypatch.setattr(bot, "_generate_reply", _fake_reply)

    resp = bot._build_legal_reply("bail application", 900004, acct)

    footer = grounding.build_sources_footer(DOCS)
    assert resp["text"] == bot.PARTIAL_BANNER + "The answer." + footer


def test_empty_answer_gets_no_footer(monkeypatch):
    acct = _account(900012)
    monkeypatch.setattr(grounding, "retrieve", lambda q, limit=3: _retrieval("GROUNDED", DOCS))
    monkeypatch.setattr(bot, "_generate_reply", lambda *a, **k: ("", "m", False, False))

    resp = bot._build_legal_reply("Criminal Offences Act", 900012, acct)

    assert "Sources" not in resp["text"]
    assert "📚" not in resp["text"]
    assert "couldn't process" in resp["text"]


def test_grounded_followup_context_reaches_prompt(monkeypatch):
    from core.juris_kai import session

    acct = _account(900007)
    monkeypatch.setattr(grounding, "retrieve", lambda q, limit=3, context="": _retrieval("GROUNDED", DOCS))
    captured = {}

    def fake_reply(prompt, task_type, query, fallback_label, account_id="",
                   chat_id=None, reply_markup=None, context="", prefix="",
                   suffix="", source_key=""):
        captured["prompt"] = prompt
        return "ok", "m", False, False

    monkeypatch.setattr(bot, "_generate_reply", fake_reply)
    session.record_conversation_turn(
        900007, "What is theft in Ghana?", "Section 124 of Act 29.")

    bot._build_legal_reply("and the penalty?", 900007, acct)

    assert "What is theft in Ghana?" in captured["prompt"]


def test_anaphoric_followup_retrieves_on_prior_topic(monkeypatch):
    from core.juris_kai import session

    acct = _account(900020)
    seen = []

    def fake_search(query, limit=3, mode="or"):
        seen.append(query)
        return [DOCS[0]] if "criminal" in query.lower() else []

    monkeypatch.setattr(grounding, "_search", fake_search)
    monkeypatch.setattr(bot, "_generate_reply", _fake_reply)
    session.record_conversation_turn(
        900020, "Criminal Offences Act 1960", "Act 29 defines stealing.")

    bot._build_legal_reply("and the penalty?", 900020, acct)

    assert seen, "retrieval must run"
    assert any("criminal" in q.lower() for q in seen)
    assert all("insolvency" not in q.lower() for q in seen)


# ---------------------------------------------------------------------------
# Source-signature cache binding
# ---------------------------------------------------------------------------


def test_source_signature_stable_and_source_sensitive():
    assert grounding.source_signature(DOCS, "GROUNDED") == \
        grounding.source_signature(list(reversed(DOCS)), "GROUNDED")
    assert grounding.source_signature(DOCS, "GROUNDED") != \
        grounding.source_signature(DOCS, "PARTIAL")
    assert grounding.source_signature(DOCS, "GROUNDED") != \
        grounding.source_signature(DOCS_B, "GROUNDED")
    assert grounding.source_signature([], "UNGROUNDED") == ""


def test_grounded_repeat_same_sources_hits_cache_footer_once(monkeypatch):
    acct = _account(900009)
    q = "What is the offence of stealing under Ghanaian criminal law?"
    monkeypatch.setenv("JURIS_KAI_STREAM", "0")
    monkeypatch.setattr(grounding, "retrieve", lambda query, limit=3: _retrieval("GROUNDED", DOCS))
    monkeypatch.setattr(bot._cache, "corpus_version", lambda *a, **k: "test")
    calls = {"n": 0}

    def fake_delegate(prompt, task_type, fallback_label, account_id=""):
        calls["n"] += 1
        return LONG_ANSWER, "m"

    monkeypatch.setattr(bot, "_delegate_with_timeout", fake_delegate)

    first = bot._build_legal_reply(q, 900009, acct)
    second = bot._build_legal_reply(q, 900009, acct)

    assert calls["n"] == 1  # second call served from cache
    assert first["text"].count("📚 *Sources*") == 1
    assert second["text"].count("📚 *Sources*") == 1
    assert LONG_ANSWER in second["text"]


def test_grounded_cache_not_reused_across_source_sets(monkeypatch):
    acct = _account(900013)
    q = "What is the offence of stealing under Ghanaian criminal law?"
    monkeypatch.setenv("JURIS_KAI_STREAM", "0")
    monkeypatch.setattr(bot._cache, "corpus_version", lambda *a, **k: "test")
    calls = {"n": 0}

    def fake_delegate(prompt, task_type, fallback_label, account_id=""):
        calls["n"] += 1
        return f"Answer {calls['n']}. " + LONG_ANSWER, "m"

    monkeypatch.setattr(bot, "_delegate_with_timeout", fake_delegate)

    monkeypatch.setattr(grounding, "retrieve", lambda query, limit=3: _retrieval("GROUNDED", DOCS))
    first = bot._build_legal_reply(q, 900013, acct)

    # Same question, different retrieved sources -> must NOT reuse the answer.
    monkeypatch.setattr(grounding, "retrieve", lambda query, limit=3: _retrieval("GROUNDED", DOCS_B))
    second = bot._build_legal_reply(q, 900013, acct)

    assert calls["n"] == 2
    assert grounding.build_sources_footer(DOCS) in first["text"]
    assert grounding.build_sources_footer(DOCS_B) in second["text"]
    assert grounding.build_sources_footer(DOCS) not in second["text"]
    assert "Answer 1." not in second["text"]


# ---------------------------------------------------------------------------
# Streaming interaction — footer must land in the FINAL message
# ---------------------------------------------------------------------------


def test_streamed_reply_returns_none_and_forwards_footer(monkeypatch):
    acct = _account(900005)
    monkeypatch.setattr(grounding, "retrieve", lambda q, limit=3: _retrieval("GROUNDED", DOCS))
    captured = {}

    def fake_reply(prompt, task_type, query, fallback_label, account_id="",
                   chat_id=None, reply_markup=None, context="", prefix="",
                   suffix="", source_key=""):
        captured.update(prefix=prefix, suffix=suffix)
        return "Streamed answer.", "m", True, False

    monkeypatch.setattr(bot, "_generate_reply", fake_reply)

    resp = bot._build_legal_reply("Criminal Offences Act", 900005, acct)

    assert resp["text"] is None
    assert captured["prefix"] == ""
    assert grounding.build_sources_footer(DOCS) in captured["suffix"]


def test_streamed_partial_forwards_banner_prefix(monkeypatch):
    acct = _account(900008)
    monkeypatch.setattr(grounding, "retrieve", lambda q, limit=3: _retrieval("PARTIAL", DOCS))
    captured = {}

    def fake_reply(prompt, task_type, query, fallback_label, account_id="",
                   chat_id=None, reply_markup=None, context="", prefix="",
                   suffix="", source_key=""):
        captured.update(prefix=prefix, suffix=suffix)
        return "Streamed answer.", "m", True, False

    monkeypatch.setattr(bot, "_generate_reply", fake_reply)

    bot._build_legal_reply("bail application", 900008, acct)

    assert captured["prefix"] == bot.PARTIAL_BANNER
    assert grounding.build_sources_footer(DOCS) in captured["suffix"]


def test_stream_to_telegram_finalizes_with_prefix_and_suffix(monkeypatch):
    monkeypatch.setattr(bot, "_send_placeholder", lambda chat_id: 42)
    monkeypatch.setattr(
        bot._streaming, "stream_chat",
        lambda prompt, task_type="legal_research", **k: iter(["hello ", "world"]))
    monkeypatch.setattr(bot, "_edit_message_text", lambda *a, **k: {"ok": True})
    captured = {}
    monkeypatch.setattr(
        bot, "_finalize_stream",
        lambda chat_id, mid, text, reply_markup=None: captured.update(text=text))

    prefix = "ℹ️ _Limited sources._\n\n"
    suffix = "\n\n📚 *Sources*\n1. Act 29"
    text, model, delivered = bot._stream_to_telegram(
        "prompt", "juris_research", 1, None, prefix=prefix, suffix=suffix)

    assert delivered is True
    assert text == "hello world"
    assert captured["text"] == prefix + "hello world" + suffix


def test_stream_to_telegram_no_suffix_is_unchanged(monkeypatch):
    monkeypatch.setattr(bot, "_send_placeholder", lambda chat_id: 42)
    monkeypatch.setattr(
        bot._streaming, "stream_chat",
        lambda prompt, task_type="legal_research", **k: iter(["plain answer"]))
    monkeypatch.setattr(bot, "_edit_message_text", lambda *a, **k: {"ok": True})
    captured = {}
    monkeypatch.setattr(
        bot, "_finalize_stream",
        lambda chat_id, mid, text, reply_markup=None: captured.update(text=text))

    text, model, delivered = bot._stream_to_telegram(
        "prompt", "juris_research", 1, None)

    assert delivered is True
    assert captured["text"] == "plain answer"


# ---------------------------------------------------------------------------
# Non-streamed send: plain-text fallback when Markdown parsing fails
# ---------------------------------------------------------------------------


def test_send_guarded_retries_plain_on_markdown_failure(monkeypatch):
    calls = []

    def fake_send(chat_id, text, reply_markup=None, parse_mode="Markdown"):
        calls.append(parse_mode)
        return {"ok": parse_mode is None}

    monkeypatch.setattr(bot, "send_message", fake_send)

    bot._send_guarded(1, {"chat_id": 1, "text": "Sources: Act_29",
                          "reply_markup": "KB", "parse_mode": "Markdown"})

    assert calls == ["Markdown", None]


def test_send_guarded_markdown_success_no_retry(monkeypatch):
    calls = []

    def fake_send(chat_id, text, reply_markup=None, parse_mode="Markdown"):
        calls.append(parse_mode)
        return {"ok": True}

    monkeypatch.setattr(bot, "send_message", fake_send)

    bot._send_guarded(1, {"chat_id": 1, "text": "fine", "parse_mode": "Markdown"})

    assert calls == ["Markdown"]
