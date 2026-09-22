"""Tests for the Juris Kai learning loop, follow-up context and speed path.

Covers:
  * task 1 — persistent ``juris_qa_log`` (full question + answer, per account)
  * task 2 — DB-backed FAQ / repeat cache (account-scoped, generic sharing)
  * task 3 — bounded follow-up context fed into prompts
  * task 4 — weak-area mining report
  * task 5 — repeat questions served from cache (fast path)

Isolation: every test points the account DB at a temp dir and resets the
module singleton, mirroring tests/test_juris_kai_multitenant.py.
"""

import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("JURIS_KAI_DB_DIR",
                      str(Path(tempfile.gettempdir()) / "juris_kai_learning_test"))


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Point accounts at a per-test DB and reset all caches/singletons."""
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


def _manager():
    from core.juris_kai.accounts import get_account_manager
    return get_account_manager()


def _account(name="User", telegram_id=None):
    mgr = _manager()
    tid = str(telegram_id) if telegram_id is not None else str(uuid.uuid4().int)[:9]
    acct = mgr.get_or_create(tid, name)
    mgr.accept_disclaimer(acct["account_id"])
    return acct


GOOD_ANSWER = (
    "Under the Criminal Offences Act 1960 (Act 29), the offence is defined in "
    "section 124. The Supreme Court in Gligah & Atiso v The Republic held that "
    "the prosecution must prove the essential elements beyond reasonable doubt. "
    "This is the settled position under Ghanaian law and the 1992 Constitution."
)


# ---------------------------------------------------------------------------
# Task 1 — persistent Q&A log
# ---------------------------------------------------------------------------

class TestQaLog:
    def test_record_qa_persists_full_question_and_answer(self):
        mgr = _manager()
        acct = _account()
        ok = mgr.record_qa(
            acct["account_id"],
            question="What is the penalty for stealing in Ghana?",
            answer=GOOD_ANSWER,
            chat_id="555",
            task_type="juris_research",
            model="qwen3-coder:kai",
            latency_ms=1234,
        )
        assert ok is True
        rows = mgr.db.execute(
            "SELECT * FROM juris_qa_log WHERE account_id = ?",
            (acct["account_id"],),
        ).fetchall()
        assert len(rows) == 1
        row = dict(rows[0])
        assert row["question"] == "What is the penalty for stealing in Ghana?"
        assert row["answer"] == GOOD_ANSWER
        assert row["chat_id"] == "555"
        assert row["task_type"] == "juris_research"
        assert row["model"] == "qwen3-coder:kai"
        assert row["latency_ms"] == 1234
        assert row["question_hash"]
        assert row["created_at"]

    def test_record_qa_truncates_to_8kb(self):
        mgr = _manager()
        acct = _account()
        huge = "x" * 20000
        mgr.record_qa(acct["account_id"], question=huge, answer=huge)
        row = dict(mgr.db.execute("SELECT * FROM juris_qa_log").fetchone())
        assert len(row["question"]) <= 8192
        assert len(row["answer"]) <= 8192

    def test_question_hash_ignores_punctuation_and_case(self):
        from core.juris_kai.cache import normalize_query
        a = normalize_query("What is Contract Law?")
        b = normalize_query("what is   contract law")
        assert a == b

    def test_qa_log_index_present(self):
        mgr = _manager()
        idx = [dict(r) for r in mgr.db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='juris_qa_log'").fetchall()]
        names = {r["name"] for r in idx}
        assert any("account" in n for n in names)

    def test_forget_qa_deletes_only_own_rows(self):
        mgr = _manager()
        a = _account()
        b = _account()
        mgr.record_qa(a["account_id"], question="Q-A", answer=GOOD_ANSWER)
        mgr.record_qa(b["account_id"], question="Q-B", answer=GOOD_ANSWER)
        res = mgr.forget_qa(a["account_id"])
        assert res["deleted"] == 1
        assert mgr.qa_history(a["account_id"]) == []
        assert len(mgr.qa_history(b["account_id"])) == 1


# ---------------------------------------------------------------------------
# Task 2 — FAQ / repeat cache
# ---------------------------------------------------------------------------

class TestFaqCache:
    def test_generic_question_detection(self):
        from core.juris_kai.cache import is_generic_question
        assert is_generic_question("What is contract law?") is True
        assert is_generic_question("Explain the 1992 Constitution") is True
        assert is_generic_question("What does my contract say?") is False
        assert is_generic_question("Can I sue my landlord?") is False
        assert is_generic_question("") is False

    def test_repeat_served_from_db_cache(self):
        mgr = _manager()
        a = _account("A")
        b = _account("B")
        mgr.record_qa(a["account_id"], task_type="juris_research",
                      question="What is contract law?", answer=GOOD_ANSWER,
                      model="m1")
        hit = mgr.lookup_qa(b["account_id"], "juris_research",
                            "What is contract law?")
        assert hit is not None
        assert hit["answer"] == GOOD_ANSWER
        assert hit["cache_scope"] == "generic"

    def test_personal_question_not_shared_across_accounts(self):
        mgr = _manager()
        a = _account("A")
        b = _account("B")
        mgr.record_qa(a["account_id"], task_type="juris_research",
                      question="What does my tenancy contract say?",
                      answer=GOOD_ANSWER)
        assert mgr.lookup_qa(b["account_id"], "juris_research",
                             "What does my tenancy contract say?") is None
        # ...but the same account can reuse its own personalised answer.
        hit = mgr.lookup_qa(a["account_id"], "juris_research",
                            "What does my tenancy contract say?")
        assert hit is not None
        assert hit["cache_scope"] == "account"

    def test_short_or_error_answers_not_cacheable(self):
        from core.juris_kai.cache import answer_is_cacheable
        assert answer_is_cacheable(GOOD_ANSWER) is True
        assert answer_is_cacheable("No.") is False
        assert answer_is_cacheable(
            "⚠️ I couldn't generate a response for that query.") is False
        assert answer_is_cacheable("") is False

    def test_record_qa_populates_inprocess_faq_cache(self):
        mgr = _manager()
        a = _account("A")
        mgr.record_qa(a["account_id"], task_type="juris_research",
                      question="What is bail?", answer=GOOD_ANSWER)
        from core.juris_kai import cache
        # Same account lookup should hit without touching SQLite.
        hit = mgr.lookup_qa(a["account_id"], "juris_research", "What is bail?")
        assert hit is not None
        assert cache.FAQ_CACHE.stats()["size"] >= 1

    def test_cache_stats_reports_faq(self):
        from core.juris_kai.cache import cache_stats
        stats = cache_stats()
        assert "faq" in stats


# ---------------------------------------------------------------------------
# Task 3 — follow-up context
# ---------------------------------------------------------------------------

class TestFollowUpContext:
    def test_looks_like_followup(self):
        from core.juris_kai.session import looks_like_followup
        assert looks_like_followup("and the penalty?") is True
        assert looks_like_followup("what about under the 2020 Act?") is True
        assert looks_like_followup("why?") is True
        assert looks_like_followup("What is contract law?") is False
        assert looks_like_followup("Explain the doctrine of frustration") is False

    def test_followup_prompt_contains_prior_turn(self):
        from core.juris_kai import session
        from core.juris_kai.prompt import build_prompt
        session.record_conversation_turn(
            "42", "What is theft in Ghana?",
            "Theft is defined by section 124 of the Criminal Offences Act.")
        ctx = session.get_followup_context("42", "and the penalty?")
        assert "What is theft in Ghana?" in ctx
        prompt = build_prompt("legal_research", "and the penalty?", context=ctx)
        assert "What is theft in Ghana?" in prompt
        assert "and the penalty?" in prompt

    def test_context_absent_for_standalone_first_question(self):
        from core.juris_kai import session
        assert session.get_followup_context("nobody", "What is contract law?") == ""

    def test_context_cap_holds(self):
        from core.juris_kai import session
        for i in range(10):
            session.record_conversation_turn(
                "7", f"question number {i} " + "x" * 200,
                f"answer number {i} " + "y" * 400)
        ctx = session.get_followup_context("7", "and then?", turns=3,
                                           max_chars=1200)
        assert len(ctx) <= 1200 + 64  # header allowance
        # Only the bounded tail is present.
        assert "question number 9" in ctx
        assert "question number 0" not in ctx

    def test_build_prompt_without_context_has_no_context_block(self):
        from core.juris_kai.prompt import build_prompt
        p = build_prompt("legal_research", "What is contract law?")
        assert "Recent conversation" not in p


# ---------------------------------------------------------------------------
# Task 4 — weak-area mining
# ---------------------------------------------------------------------------

class TestLearningReport:
    def test_report_mining(self):
        from core.juris_kai import learning
        mgr = _manager()
        a = _account("A")
        for _ in range(3):
            mgr.record_qa(a["account_id"], task_type="juris_research",
                          question="What is contract law?", answer=GOOD_ANSWER)
        mgr.record_qa(a["account_id"], task_type="juris_research",
                      question="What is the rule in Rylands v Fletcher?",
                      answer="No.")
        mgr.record_qa(a["account_id"], task_type="juris_research",
                      question="blocked one",
                      answer="⚠️ I couldn't generate a response for that query.")
        rep = learning.analyze(limit=100, min_count=2)
        assert rep["total_records"] == 5
        # most-asked topic (by task_type)
        assert rep["by_task_type"]["juris_research"] == 5
        # repeat questions
        assert any(r["count"] >= 2 for r in rep["repeat_questions"])
        # short / blocked flagged
        assert any("Rylands" in (r["question"] or "")
                   for r in rep["weak_answers"])
        assert rep["blocked_answers"] >= 1

    def test_report_empty_db_is_safe(self):
        from core.juris_kai import learning
        rep = learning.analyze()
        assert rep["total_records"] == 0
        assert rep["repeat_questions"] == []

    def test_cc_learning_route_helper(self):
        from core.juris_kai.cc_routes import _learning_report
        rep = _learning_report()
        assert "total_records" in rep


class TestForgetCommand:
    def test_forget_command_clears_rows(self):
        from core.juris_kai.commands import handle_forget
        mgr = _manager()
        acct = _account("F")
        mgr.record_qa(acct["account_id"], question="Q", answer=GOOD_ANSWER)
        msg = handle_forget(acct)
        assert "Deleted 1" in msg
        assert mgr.qa_history(acct["account_id"]) == []


# ---------------------------------------------------------------------------
# Task 5 — bot wiring: record_qa from handlers + FAQ speed path
# ---------------------------------------------------------------------------

class TestBotWiring:
    def _patch_generation(self, monkeypatch, answer=GOOD_ANSWER):
        import core.juris_kai.bot as bot
        calls = {"n": 0}

        def fake_delegate(prompt, task_type, fallback_label, account_id=""):
            calls["n"] += 1
            return answer, "testmodel"

        monkeypatch.setenv("JURIS_KAI_STREAM", "0")
        monkeypatch.setattr(bot, "_delegate_with_timeout", fake_delegate)
        monkeypatch.setattr(bot._cache, "corpus_version", lambda *a, **k: "test")
        monkeypatch.setattr(
            "core.juris_kai.legal_context.query_knowledge_base",
            lambda *a, **k: [])
        monkeypatch.setattr(
            "core.juris_kai.legal_context.build_context_preamble",
            lambda *a, **k: "")
        return bot, calls

    def test_free_text_generation_records_qa(self, monkeypatch):
        bot, calls = self._patch_generation(monkeypatch)
        chat_id = 909090
        acct = _account("Bot", telegram_id=chat_id)
        resp = bot.handle_message({"chat_id": chat_id,
                                   "text": "What is the penalty for stealing?"})
        assert resp is not None
        assert calls["n"] == 1
        rows = _manager().qa_history(acct["account_id"])
        assert len(rows) == 1
        assert rows[0]["question"] == "What is the penalty for stealing?"
        assert rows[0]["answer"] == GOOD_ANSWER

    def test_repeat_question_served_from_faq(self, monkeypatch):
        bot, calls = self._patch_generation(monkeypatch)
        chat_id = 919191
        _account("Bot", telegram_id=chat_id)
        q = "What is the doctrine of frustration?"
        bot.handle_message({"chat_id": chat_id, "text": q})
        assert calls["n"] == 1
        # Simulate a process restart: drop the in-process generation cache but
        # keep the DB + FAQ cache rows that record_qa wrote.
        bot._cache.GENERATION_CACHE.clear()
        bot.handle_message({"chat_id": chat_id, "text": q})
        # No second model call — served from the FAQ cache.
        assert calls["n"] == 1

    def test_followup_prompt_uses_session_history(self, monkeypatch):
        import core.juris_kai.bot as bot
        from core.juris_kai import session
        captured = {}
        monkeypatch.setenv("JURIS_KAI_STREAM", "0")

        def fake_delegate(prompt, task_type, fallback_label, account_id=""):
            captured["prompt"] = prompt
            return GOOD_ANSWER, "testmodel"

        monkeypatch.setattr(bot, "_delegate_with_timeout", fake_delegate)
        monkeypatch.setattr(bot._cache, "corpus_version", lambda *a, **k: "test")
        monkeypatch.setattr(
            "core.juris_kai.legal_context.query_knowledge_base",
            lambda *a, **k: [])
        monkeypatch.setattr(
            "core.juris_kai.legal_context.build_context_preamble",
            lambda *a, **k: "")

        chat_id = 929292
        _account("Bot", telegram_id=chat_id)
        session.record_conversation_turn(
            chat_id, "What is theft in Ghana?",
            "Section 124 of Act 29 defines theft.")
        bot.handle_message({"chat_id": chat_id, "text": "and the penalty?"})
        assert "What is theft in Ghana?" in captured["prompt"]
        assert "and the penalty?" in captured["prompt"]
