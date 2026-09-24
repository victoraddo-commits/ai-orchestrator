"""TDD tests for Deep Research mode wired into the Juris Kai bot (Phase 5 T3).

Deep mode is an explicit, opt-in 3-pass path (``reasoning.run_deep``) reached
only by the ``/deep`` command or the "🔬 Deep Research" menu button. Quick mode
(single pass) stays the default for ordinary messages. The strict grounding
gate is shared: OUT-OF-SCOPE / UNGROUNDED never reach the passes, and the
rendered answer (IRAC + Authorities + Counter-authorities + Uncertainty) carries
the deterministic 📚 Sources footer (with temporal status) after passing through
the citation firewall.
"""

import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault(
    "JURIS_KAI_DB_DIR",
    str(Path(tempfile.gettempdir()) / "juris_kai_test"),
)

import core.juris_kai.bot as bot  # noqa: E402
from core.juris_kai import cache, grounding, menus  # noqa: E402


DOCS = [{
    "id": 101,
    "title": "Criminal Offences Act, 1960",
    "citation": "Act 29",
    "year": 1960,
    "store_mode": "full",
    "temporal_status": "AMENDED",
    "chunk_content": "Stealing is defined in section 124. " * 20,
}]


def _deep_result(docs=None, bad_citation=False):
    docs = list(docs or DOCS)
    rule = "Section 124 of Act 29 defines stealing."
    if bad_citation:
        rule = "Act 9999 defines stealing and Act 29 applies."
    return {
        "query": "Is stealing an offence?",
        "docs": docs,
        "verdict": "GROUNDED",
        "advocate": {"pass": "advocate", "argument": "Stealing is an offence.",
                     "authorities": ["Act 29"], "docs": docs},
        "opponent": {"pass": "opponent", "argument": "No contrary authority.",
                     "authorities": ["Act 29", "Act 500"],
                     "contrary_authorities": ["Act 500"],
                     "contrary_terms": [{"doc": "Act 500", "term": "except",
                                         "snippet": "except"}],
                     "docs": docs},
        "judge": {
            "pass": "judge",
            "established": [{"proposition": "Stealing is an offence."}],
            "disputed": [{"proposition": "The exception for minors."}],
            "unresolved": [],
            "authorities": ["Act 29", "Act 500"],
            "confidence": 0.35,
            "irac": {
                "issue": "Is stealing an offence?",
                "rule": rule,
                "application": "Act 29 applies.",
                "conclusion": "Established, but disputed on minors.",
            },
            "uncertainty": {"advocate": [], "opponent": []},
            "docs": docs,
        },
        "authorities": ["Act 29", "Act 500"],
        "uncertainty": {"advocate": [], "opponent": []},
        "degraded": False,
        "latency": {"total": 1.0},
    }


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db_dir = tmp_path / "juris_db"
    db_dir.mkdir()
    import core.juris_kai.accounts as accts
    monkeypatch.setattr(accts, "DB_DIR", str(db_dir))
    monkeypatch.setattr(accts, "DB_PATH", str(db_dir / "juris_kai_accounts.db"))
    accts._account_manager = None

    cache.clear_caches()

    import core.juris_kai.session as session
    monkeypatch.setattr(session, "STORAGE_PATH", tmp_path / "sessions.json")

    # The bot-level citation firewall verifies through CT100; never touch it in
    # a unit test. Individual tests override this with their own verifier.
    monkeypatch.setattr(
        "core.legal_brain_client.verify_citations",
        lambda text, record=True: {"citations": [], "summary": {},
                                   "all_verified": True})

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


def _no_deep(*a, **k):
    raise AssertionError("reasoning.run_deep must NOT run for this question")


def _failing_generate(*a, **k):
    raise AssertionError("quick _generate_reply must NOT run for a Deep success")


def _capturing_reply(answer="Quick fallback.", captured=None):
    def fake(prompt, task_type, query, fallback_label, account_id="",
             chat_id=None, reply_markup=None, context="", prefix="",
             suffix="", source_key="", **kwargs):
        if captured is not None:
            captured.update(prompt=prompt, task_type=task_type, prefix=prefix,
                            suffix=suffix, source_key=source_key)
        return answer, "m", False, False
    return fake


# ---------------------------------------------------------------------------
# Deep render + footer
# ---------------------------------------------------------------------------


def test_deep_renders_all_sections_and_footer(monkeypatch):
    acct = _account(910001)
    monkeypatch.setattr(
        grounding, "retrieve",
        lambda q, limit=3, context="": _retrieval("GROUNDED", DOCS))
    monkeypatch.setattr("core.juris_kai.reasoning.run_deep",
                        lambda query, docs=None, context="": _deep_result())
    monkeypatch.setattr(bot, "_generate_reply", _failing_generate)

    resp = bot._build_deep_reply("Is stealing an offence?", 910001, acct)

    text = resp["text"]
    assert "IRAC" in text
    assert "*Issue:*" in text and "*Rule:*" in text
    assert "*Application:*" in text and "*Conclusion:*" in text
    assert "*Authorities*" in text
    assert "*Counter-authorities*" in text and "Act 500" in text
    assert "*Uncertainty*" in text
    assert grounding.build_sources_footer(DOCS) in text
    assert "📚 *Sources*" in text
    assert "[AMENDED]" in text
    assert resp["parse_mode"] == "Markdown"


def test_deep_returns_full_rendered_answer_not_streamed(monkeypatch):
    acct = _account(910013)
    monkeypatch.setattr(
        grounding, "retrieve",
        lambda q, limit=3, context="": _retrieval("GROUNDED", DOCS))
    monkeypatch.setattr("core.juris_kai.reasoning.run_deep",
                        lambda query, docs=None, context="": _deep_result())
    resp = bot._build_deep_reply("Is stealing an offence?", 910013, acct)
    assert resp["text"] and resp["text"].strip()
    assert resp["text"] is not None


# ---------------------------------------------------------------------------
# Strict grounding preserved: UNGROUNDED / out-of-scope never run the passes
# ---------------------------------------------------------------------------


def test_deep_ungrounded_refuses_without_passes(monkeypatch):
    acct = _account(910002)
    monkeypatch.setattr(
        grounding, "retrieve",
        lambda q, limit=3, context="": _retrieval("UNGROUNDED"))
    monkeypatch.setattr("core.juris_kai.reasoning.run_deep", _no_deep)
    monkeypatch.setattr(bot, "_generate_reply", _failing_generate)

    resp = bot._build_deep_reply("quantum entanglement tax", 910002, acct)
    assert resp["text"] == bot.UNGROUNDED_REPLY


def test_deep_out_of_scope_refused(monkeypatch):
    acct = _account(910003)
    monkeypatch.setattr(
        grounding, "retrieve",
        lambda q, limit=3, context="": _retrieval("GROUNDED", DOCS))
    monkeypatch.setattr("core.juris_kai.reasoning.run_deep", _no_deep)

    resp = bot._build_deep_reply("What is the law in Nigeria?", 910003, acct)
    assert resp["text"] == bot.JURISDICTION_REFUSAL


# ---------------------------------------------------------------------------
# Citation firewall still applied to the rendered deep answer
# ---------------------------------------------------------------------------


def test_deep_citations_pass_through_firewall(monkeypatch):
    acct = _account(910004)
    monkeypatch.setattr(
        grounding, "retrieve",
        lambda q, limit=3, context="": _retrieval("GROUNDED", DOCS))
    monkeypatch.setattr("core.juris_kai.reasoning.run_deep",
                        lambda query, docs=None, context="": _deep_result(bad_citation=True))

    def fake_verify(text, record=True):
        target = "Act 9999"
        if target in text:
            s = text.index(target)
            return {"citations": [{"display": target, "status": "UNVERIFIED",
                                   "start": s, "end": s + len(target)}],
                    "summary": {"UNVERIFIED": 1}, "all_verified": False}
        return {"citations": [], "summary": {}, "all_verified": True}

    monkeypatch.setattr("core.legal_brain_client.verify_citations", fake_verify)

    resp = bot._build_deep_reply("Is stealing an offence?", 910004, acct)
    assert "Act 9999" not in resp["text"]
    assert "unverified" in resp["text"].lower()


# ---------------------------------------------------------------------------
# Quick mode is unchanged and never invokes the three passes
# ---------------------------------------------------------------------------


def test_quick_mode_unchanged_and_never_calls_deep(monkeypatch):
    acct = _account(910005)
    monkeypatch.setattr(
        grounding, "retrieve",
        lambda q, limit=3, context="": _retrieval("GROUNDED", DOCS))
    monkeypatch.setattr("core.juris_kai.reasoning.run_deep", _no_deep)
    captured = {}
    monkeypatch.setattr(bot, "_generate_reply",
                        _capturing_reply("Quick answer.", captured))

    resp = bot._handle_free_text("Is stealing an offence?", 910005, acct, False)

    assert resp["text"] == "Quick answer." + grounding.build_sources_footer(DOCS)
    assert captured["task_type"] == "juris_research"
    assert "SOURCE 1: Criminal Offences Act, 1960 (Act 29)" in captured["prompt"]
    assert "IRAC" not in resp["text"]


# ---------------------------------------------------------------------------
# Menu wiring
# ---------------------------------------------------------------------------


def test_learn_menu_exposes_deep_button():
    import json
    keyboard = json.loads(menus.learn_menu())
    labels = [b["text"] for row in keyboard["keyboard"] for b in row]
    assert "🔬 Deep Research" in labels
    # Existing buttons are untouched by the addition.
    assert "🇬🇭 Ghana Constitution" in labels
    assert "🔙 Back to Menu" in labels


def test_deep_button_is_an_action_not_navigation():
    assert menus.menu_for_text("🔬 Deep Research") is None


def test_deep_menu_button_sets_state_then_runs_deep(monkeypatch):
    acct = _account(910006)
    prompt = bot._handle_menu_action("🔬 Deep Research", 910006, acct, False, {})
    assert "Deep Research" in prompt["text"]
    assert bot._conversation_state["910006"]["step"] == "deep_research"

    monkeypatch.setattr(
        grounding, "retrieve",
        lambda q, limit=3, context="": _retrieval("GROUNDED", DOCS))
    monkeypatch.setattr("core.juris_kai.reasoning.run_deep",
                        lambda query, docs=None, context="": _deep_result())

    out = bot._handle_conversation_flow("Is stealing an offence?", 910006, acct)
    assert "Uncertainty" in out["text"]
    assert "910006" not in bot._conversation_state


# ---------------------------------------------------------------------------
# /deep command
# ---------------------------------------------------------------------------


def test_deep_command_without_question_shows_usage():
    acct = _account(910007)
    resp = bot._handle_deep_command("", 910007, acct, False)
    assert "/deep" in resp["text"]
    assert "Deep Research" in resp["text"]


def test_deep_command_with_question_runs_deep(monkeypatch):
    acct = _account(910008)
    monkeypatch.setattr(
        grounding, "retrieve",
        lambda q, limit=3, context="": _retrieval("GROUNDED", DOCS))
    monkeypatch.setattr("core.juris_kai.reasoning.run_deep",
                        lambda query, docs=None, context="": _deep_result())
    resp = bot._handle_deep_command("Is stealing an offence?", 910008, acct, False)
    assert "IRAC" in resp["text"]
    assert "📚 *Sources*" in resp["text"]


def test_handle_message_routes_slash_deep(monkeypatch):
    _account(910009)
    seen = {}

    def fake_deep(question, chat_id, account, admin):
        seen.update(question=question)
        return {"chat_id": chat_id, "text": "ok"}

    monkeypatch.setattr(bot, "_handle_deep_command", fake_deep)

    resp = bot.handle_message({
        "chat_id": 910009, "text": "/deep is theft an offence",
        "from_first_name": "T"})

    assert seen["question"] == "is theft an offence"
    assert resp["text"] == "ok"


# ---------------------------------------------------------------------------
# Latency safety: a stalled deep run degrades, never hangs the poller
# ---------------------------------------------------------------------------


def test_deep_timeout_degrades_to_single_pass(monkeypatch):
    acct = _account(910010)
    monkeypatch.setattr(
        grounding, "retrieve",
        lambda q, limit=3, context="": _retrieval("GROUNDED", DOCS))
    monkeypatch.setattr(bot, "DEEP_TIMEOUT", 0.05)

    def slow(*a, **k):
        time.sleep(0.3)
        return _deep_result()

    monkeypatch.setattr("core.juris_kai.reasoning.run_deep", slow)
    captured = {}
    monkeypatch.setattr(bot, "_generate_reply",
                        _capturing_reply("Quick fallback.", captured))

    resp = bot._build_deep_reply("Is stealing an offence?", 910010, acct)

    assert "exceeded its time budget" in resp["text"]
    assert "Quick fallback." in resp["text"]
    assert grounding.build_sources_footer(DOCS) in resp["text"]
    assert bot.DEEP_FALLBACK_NOTE in captured["prefix"]


# ---------------------------------------------------------------------------
# Learning-loop recording
# ---------------------------------------------------------------------------


def test_deep_records_turn_for_learning_loop(monkeypatch):
    from core.juris_kai.accounts import get_account_manager

    acct = _account(910011)
    monkeypatch.setattr(
        grounding, "retrieve",
        lambda q, limit=3, context="": _retrieval("GROUNDED", DOCS))
    monkeypatch.setattr("core.juris_kai.reasoning.run_deep",
                        lambda query, docs=None, context="": _deep_result())

    bot._build_deep_reply("Is stealing an offence?", 910011, acct)

    rows = get_account_manager().qa_history(acct["account_id"])
    assert len(rows) == 1
    assert rows[0]["task_type"] == bot.DEEP_TASK_TYPE
    assert rows[0]["question"] == "Is stealing an offence?"
    # The stored answer is the judgement body, not the presentation footer.
    assert grounding.build_sources_footer(DOCS) not in rows[0]["answer"]


def test_deep_answer_not_replayed_as_quick(monkeypatch):
    from core.juris_kai.accounts import get_account_manager

    acct = _account(910012)
    q = "Is stealing an offence?"
    monkeypatch.setattr(
        grounding, "retrieve",
        lambda query, limit=3, context="": _retrieval("GROUNDED", DOCS))
    monkeypatch.setattr("core.juris_kai.reasoning.run_deep",
                        lambda query, docs=None, context="": _deep_result())

    bot._build_deep_reply(q, 910012, acct)

    assert get_account_manager().lookup_qa(
        acct["account_id"], "juris_research", q,
        source_key=grounding.source_signature(DOCS, "GROUNDED")) is None
