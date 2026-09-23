"""Strict-grounding tests for the Juris Kai bot free-text path.

Owner rule: no legal substance without a retrieved source. An UNGROUNDED
question must return the refusal text WITHOUT ever calling the model, while
GROUNDED/PARTIAL answers carry the deterministic Sources footer (plus a
banner for PARTIAL) even when the answer is streamed to Telegram.

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
from core.juris_kai import grounding  # noqa: E402

DOCS = [{
    "title": "Criminal Offences Act, 1960",
    "citation": "Act 29",
    "year": 1960,
    "store_mode": "full",
    "chunk_content": "Stealing is defined in section 124. " * 20,
}]


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Per-test DB + reset caches/singletons (same pattern as learning loop)."""
    db_dir = tmp_path / "juris_db"
    db_dir.mkdir()
    import core.juris_kai.accounts as accts
    monkeypatch.setattr(accts, "DB_DIR", str(db_dir))
    monkeypatch.setattr(accts, "DB_PATH", str(db_dir / "juris_kai_accounts.db"))
    accts._account_manager = None

    import core.juris_kai.cache as cache
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
                   chat_id=None, reply_markup=None, context="", prefix="", suffix=""):
        captured["prompt"] = prompt
        captured["prefix"] = prefix
        captured["suffix"] = suffix
        return "The answer.", "m", False, False

    monkeypatch.setattr(bot, "_generate_reply", fake_reply)

    resp = bot._build_legal_reply("Criminal Offences Act", 900003, acct)

    assert "SOURCE 1: Criminal Offences Act, 1960 (Act 29)" in captured["prompt"]
    assert "Cite ONLY the sources below" in captured["prompt"]
    footer = grounding.build_sources_footer(DOCS)
    assert captured["prefix"] == ""
    assert footer in captured["suffix"]
    assert resp["text"] == "The answer." + footer
    assert bot.PARTIAL_BANNER not in resp["text"]


def test_partial_prefixes_banner_and_appends_footer(monkeypatch):
    acct = _account(900004)
    monkeypatch.setattr(grounding, "retrieve", lambda q, limit=3: _retrieval("PARTIAL", DOCS))
    monkeypatch.setattr(
        bot, "_generate_reply",
        lambda *a, **k: ("Partial answer.", "m", False, False))

    resp = bot._build_legal_reply("bail application", 900004, acct)

    footer = grounding.build_sources_footer(DOCS)
    assert resp["text"] == bot.PARTIAL_BANNER + "Partial answer." + footer


def test_grounded_followup_context_reaches_prompt(monkeypatch):
    from core.juris_kai import session

    acct = _account(900007)
    monkeypatch.setattr(grounding, "retrieve", lambda q, limit=3: _retrieval("GROUNDED", DOCS))
    captured = {}

    def fake_reply(prompt, task_type, query, fallback_label, account_id="",
                   chat_id=None, reply_markup=None, context="", prefix="", suffix=""):
        captured["prompt"] = prompt
        return "ok", "m", False, False

    monkeypatch.setattr(bot, "_generate_reply", fake_reply)
    session.record_conversation_turn(
        900007, "What is theft in Ghana?", "Section 124 of Act 29.")

    bot._build_legal_reply("and the penalty?", 900007, acct)

    assert "What is theft in Ghana?" in captured["prompt"]


# ---------------------------------------------------------------------------
# Streaming interaction — footer must land in the FINAL message
# ---------------------------------------------------------------------------


def test_streamed_reply_returns_none_and_forwards_footer(monkeypatch):
    acct = _account(900005)
    monkeypatch.setattr(grounding, "retrieve", lambda q, limit=3: _retrieval("GROUNDED", DOCS))
    captured = {}

    def fake_reply(prompt, task_type, query, fallback_label, account_id="",
                   chat_id=None, reply_markup=None, context="", prefix="", suffix=""):
        captured["prefix"] = prefix
        captured["suffix"] = suffix
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
                   chat_id=None, reply_markup=None, context="", prefix="", suffix=""):
        captured["prefix"] = prefix
        captured["suffix"] = suffix
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
