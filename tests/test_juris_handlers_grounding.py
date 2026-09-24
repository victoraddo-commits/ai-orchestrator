"""Strict-grounding tests for the newly-wired legal-answer handlers.

Owner rule: no legal substance without a retrieved source. The free-text path
already obeyed it; these cover the Telegram menu handlers (Learn Law, Cases,
conversation flows) and the slash commands, which must route through the same
``grounding.build_grounded_plan`` gate: out-of-scope refusal, UNGROUNDED
refusal without a model call, GROUNDED/PARTIAL with ``build_grounded_prompt``
plus the deterministic Sources footer (and PARTIAL banner).

Document summaries (the ``summarize`` conversation step) are not Ghana-law
answers, so they must stay on the ungrounded ``build_prompt`` path.

Isolation mirrors tests/test_juris_bot_grounding.py: per-test temp DB, reset
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
from core.juris_kai import commands  # noqa: E402

DOCS = [{
    "id": 101,
    "title": "Criminal Offences Act, 1960",
    "citation": "Act 29",
    "year": 1960,
    "store_mode": "full",
    "chunk_content": "Stealing is defined in section 124. " * 20,
}]


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

    yield
    accts._account_manager = None
    cache.clear_caches()


def _account(telegram_id=None, tier=None):
    from core.juris_kai.accounts import get_account_manager
    mgr = get_account_manager()
    tid = str(telegram_id) if telegram_id is not None else str(uuid.uuid4().int)[:9]
    acct = mgr.get_or_create(tid, "Tester")
    mgr.accept_disclaimer(acct["account_id"])
    if tier:
        # Feature-gated handlers (flashcards/quiz/argument) need a plan that
        # grants the feature; these tests are about grounding, not entitlements.
        mgr.set_subscription(acct["account_id"], tier)
    return acct


def _retrieval(verdict, docs=None):
    return {"docs": list(docs or []), "verdict": verdict, "stage": 1}


def _failing_generate(*args, **kwargs):
    raise AssertionError("_generate_reply must NOT be called without a source")


def _capture_reply(monkeypatch, answer="The answer."):
    captured = {}

    def fake_reply(prompt, task_type, query, fallback_label, account_id="",
                   chat_id=None, reply_markup=None, context="", prefix="",
                   suffix="", source_key="", **kwargs):
        captured.update(prompt=prompt, task_type=task_type, query=query,
                        prefix=prefix, suffix=suffix, source_key=source_key)
        return answer, "m", False, False

    monkeypatch.setattr(bot, "_generate_reply", fake_reply)
    return captured


# ---------------------------------------------------------------------------
# Telegram menu handlers
# ---------------------------------------------------------------------------


def test_learn_topic_grounded_uses_grounded_prompt_and_footer(monkeypatch):
    acct = _account()
    monkeypatch.setattr(grounding, "retrieve",
                        lambda q, limit=3: _retrieval("GROUNDED", DOCS))
    captured = _capture_reply(monkeypatch, "Teaching answer.")

    resp = bot._handle_learn_topic("contract", "📘 Contract Law", 12345, acct)

    assert captured["task_type"] == "juris_legal_teaching"
    assert "SOURCE 1: Criminal Offences Act, 1960 (Act 29)" in captured["prompt"]
    assert "Cite ONLY the sources below" in captured["prompt"]
    assert grounding.build_sources_footer(DOCS) in captured["suffix"]
    assert resp["text"] == "Teaching answer." + grounding.build_sources_footer(DOCS)


def test_learn_topic_ungrounded_refuses_without_model(monkeypatch):
    acct = _account()
    monkeypatch.setattr(grounding, "retrieve",
                        lambda q, limit=3: _retrieval("UNGROUNDED"))
    monkeypatch.setattr(bot, "_generate_reply", _failing_generate)

    resp = bot._handle_learn_topic("contract", "📘 Contract Law", 12345, acct)

    assert resp["text"] == bot.UNGROUNDED_REPLY


def test_case_query_grounded_uses_case_task_type(monkeypatch):
    acct = _account()
    monkeypatch.setattr(grounding, "retrieve",
                        lambda q, limit=3: _retrieval("GROUNDED", DOCS))
    captured = _capture_reply(monkeypatch, "Case answer.")

    resp = bot._handle_case_query("donoghue", 12346, acct)

    assert captured["task_type"] == "juris_case_analysis"
    assert "SOURCE 1" in captured["prompt"]
    assert grounding.build_sources_footer(DOCS) in resp["text"]


def test_case_query_ungrounded_refuses_without_model(monkeypatch):
    acct = _account()
    monkeypatch.setattr(grounding, "retrieve",
                        lambda q, limit=3, context="": _retrieval("UNGROUNDED"))
    monkeypatch.setattr(bot, "_generate_reply", _failing_generate)

    resp = bot._handle_case_query("donoghue", 12346, acct)

    assert resp["text"] == bot.UNGROUNDED_REPLY


def test_case_query_nonsense_with_ghana_is_ungrounded_without_model(monkeypatch):
    """The synthesised "Ghana law" suffix must not ground a nonsense topic.

    The fake search mimics the legal-brain matching the generic "ghana" token:
    before stripping, the handler's query was "<topic> Ghana law", so the token
    grounded unrelated sources and burned a model call.
    """
    acct = _account()

    def fake_search(query, limit=3, mode="or"):
        return [DOCS[0]] if "ghana" in query.lower() else []

    monkeypatch.setattr(grounding, "_search", fake_search)
    monkeypatch.setattr(bot, "_generate_reply", _failing_generate)

    resp = bot._handle_case_query("xylophone zzz bananas", 12347, acct)

    assert resp["text"] == bot.UNGROUNDED_REPLY


def test_case_query_searches_raw_topic_without_generic_tokens(monkeypatch):
    acct = _account()
    seen = []
    monkeypatch.setattr(grounding, "_search",
                        lambda query, limit=3, mode="or": seen.append(query) or [])
    monkeypatch.setattr(bot, "_generate_reply", _failing_generate)

    bot._handle_case_query("xylophone zzz bananas", 12348, acct)

    assert seen
    for query in seen:
        toks = query.lower().split()
        assert "ghana" not in toks and "law" not in toks


def test_conversation_flow_grounded_uses_step_task_type(monkeypatch):
    acct = _account(tier="monthly_pro")
    monkeypatch.setattr(grounding, "retrieve",
                        lambda q, limit=3: _retrieval("GROUNDED", DOCS))
    captured = _capture_reply(monkeypatch, "Flashcards.")
    chat = 22222
    bot._conversation_state[str(chat)] = {"step": "flashcards", "data": {}}

    resp = bot._handle_conversation_flow("contract law", chat, acct)

    assert captured["task_type"] == "juris_flashcards"
    assert "SOURCE 1" in captured["prompt"]
    assert grounding.build_sources_footer(DOCS) in resp["text"]
    assert str(chat) not in bot._conversation_state


def test_conversation_flow_ungrounded_refuses_without_model(monkeypatch):
    acct = _account(tier="monthly_pro")
    monkeypatch.setattr(grounding, "retrieve",
                        lambda q, limit=3: _retrieval("UNGROUNDED"))
    monkeypatch.setattr(bot, "_generate_reply", _failing_generate)
    chat = 33333
    bot._conversation_state[str(chat)] = {"step": "quiz", "data": {}}

    resp = bot._handle_conversation_flow("xylophone zzz", chat, acct)

    assert resp["text"] == bot.UNGROUNDED_REPLY
    assert str(chat) not in bot._conversation_state


def test_summarize_flow_stays_on_ungrounded_document_path(monkeypatch):
    acct = _account()

    def no_retrieve(*a, **k):
        raise AssertionError("document summary is not a Ghana-law answer")

    monkeypatch.setattr(grounding, "retrieve", no_retrieve)
    monkeypatch.setattr("core.juris_kai.legal_context.query_knowledge_base",
                        lambda q: [])
    monkeypatch.setattr("core.juris_kai.legal_context.build_context_preamble",
                        lambda docs: "")
    captured = _capture_reply(monkeypatch, "Summary.")
    chat = 44444
    bot._conversation_state[str(chat)] = {"step": "summarize", "data": {}}

    resp = bot._handle_conversation_flow("This is my document text.", chat, acct)

    assert captured["task_type"] == "juris_research"
    assert "SOURCE" not in captured["prompt"]
    assert resp["text"] == "Summary."
    assert str(chat) not in bot._conversation_state


# ---------------------------------------------------------------------------
# Slash commands (ground + return text, no chat_id needed)
# ---------------------------------------------------------------------------


def _delegate_capture(monkeypatch, response="Command answer."):
    captured = {}

    def fake_delegate(prompt, task_type=None, capability=None, **kw):
        captured["prompt"] = prompt
        captured["task_type"] = task_type
        return {"response": response, "provider": "test"}

    monkeypatch.setattr("core.ai.ai_router.delegate", fake_delegate)
    return captured


def _delegate_must_not_run(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("delegate must NOT be called without a source")

    monkeypatch.setattr("core.ai.ai_router.delegate", boom)


@pytest.mark.parametrize("handler,topic,task_type,response", [
    (commands.handle_learn, "contract law", "juris_legal_teaching", "Teaching."),
    (commands.handle_case, "donoghue v stevenson", "juris_case_analysis", "Case."),
    (commands.handle_argument, "self-defense", "juris_argument_construction", "Argument."),
    (commands.handle_flashcards, "contract law", "juris_flashcards", "Flashcards."),
])
def test_slash_commands_grounded(monkeypatch, handler, topic, task_type, response):
    monkeypatch.setattr(grounding, "retrieve",
                        lambda q, limit=3: _retrieval("GROUNDED", DOCS))
    captured = _delegate_capture(monkeypatch, response)
    account = _account(tier="monthly_pro")

    out = handler(topic, {}, account)

    assert captured["task_type"] == task_type
    assert "SOURCE 1: Criminal Offences Act, 1960 (Act 29)" in captured["prompt"]
    assert "Cite ONLY the sources below" in captured["prompt"]
    assert out.startswith(response)
    assert grounding.build_sources_footer(DOCS) in out


@pytest.mark.parametrize("handler,topic", [
    (commands.handle_learn, "xylophone zzz"),
    (commands.handle_case, "xylophone zzz"),
    (commands.handle_argument, "xylophone zzz"),
    (commands.handle_flashcards, "xylophone zzz"),
])
def test_slash_commands_ungrounded_refuse_without_model(monkeypatch, handler, topic):
    monkeypatch.setattr(grounding, "retrieve",
                        lambda q, limit=3: _retrieval("UNGROUNDED"))
    _delegate_must_not_run(monkeypatch)

    out = handler(topic, {}, _account(tier="monthly_pro"))

    assert out == grounding.UNGROUNDED_REPLY


def test_slash_command_partial_prefixes_banner(monkeypatch):
    monkeypatch.setattr(grounding, "retrieve",
                        lambda q, limit=3: _retrieval("PARTIAL", DOCS))
    _delegate_capture(monkeypatch, "Teaching.")

    out = commands.handle_learn("contract law", {}, {"account_id": "acct-1"})

    assert out.startswith(grounding.PARTIAL_BANNER)
    assert grounding.build_sources_footer(DOCS) in out


def test_handle_research_ungrounded_refuses_without_workforce(monkeypatch):
    monkeypatch.setattr(grounding, "retrieve",
                        lambda q, limit=3: _retrieval("UNGROUNDED"))
    called = {"bridge": False}

    def get_bridge():
        called["bridge"] = True
        raise AssertionError("workforce must not run without a source")

    monkeypatch.setattr("core.integration.module_bridge.get_bridge", get_bridge)
    _delegate_must_not_run(monkeypatch)

    out = commands.handle_research("xylophone zzz", {}, {"account_id": "acct-1"})

    assert out == grounding.UNGROUNDED_REPLY
    assert called["bridge"] is False


def test_handle_research_grounded_uses_grounded_prompt(monkeypatch):
    monkeypatch.setattr(grounding, "retrieve",
                        lambda q, limit=3: _retrieval("GROUNDED", DOCS))

    class _NoWork:
        def request_capability(self, *a, **k):
            return {}

    monkeypatch.setattr("core.integration.module_bridge.get_bridge",
                        lambda: _NoWork())
    captured = _delegate_capture(monkeypatch, "Research.")

    out = commands.handle_research("contract law", {}, {"account_id": "acct-1"})

    assert "SOURCE 1" in captured["prompt"]
    assert grounding.build_sources_footer(DOCS) in out
