"""TDD tests for the three-pass reasoning engine (Phase 5 Task 2).

Advocate -> Opponent -> Judge. Every pass is grounded (supplied sources only),
its citations pass through the firewall, and the judge integrates the
uncertainty engine. ``run_deep`` orchestrates the passes and degrades
gracefully: a failed pass must never yield a blank answer.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.juris_kai import reasoning  # noqa: E402


DOC = {
    "id": 1,
    "title": "Criminal Offences Act, 1960",
    "citation": "Act 29",
    "authority_level": "act",
    "store_mode": "full",
    "chunk_content": ("A person who commits rape is liable to imprisonment. "
                      "Act 29 defines the offence. ") + "x" * 500,
}

CONTRARY = {
    "id": 2,
    "title": "Criminal Offences (Amendment) Act",
    "citation": "Act 500",
    "authority_level": "act",
    "store_mode": "full",
    "chunk_content": ("This Act shall not apply where the accused is a minor. "
                      "Act 500 creates an exception. ") + "x" * 400,
}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Never touch CT100 or Ollama unless a test overrides these seams."""
    monkeypatch.setattr(reasoning, "_search",
                        lambda query, limit=4, mode="hybrid": [])
    monkeypatch.setattr(
        reasoning, "_verify",
        lambda text: {"citations": [], "summary": {}, "all_verified": True})


@pytest.fixture
def gen(monkeypatch):
    """Deterministic, structured model seam for the three passes."""
    calls = {}

    def fake(prompt, task_type):
        calls.setdefault(task_type, []).append(prompt)
        if task_type == "juris_advocate":
            return ("Under Act 29, rape is an offence. "
                    "Act 29 makes the offender liable to imprisonment.")
        if task_type == "juris_opponent":
            return ("However, Act 500 provides that this shall not apply to a "
                    "minor. Act 500 creates an exception.")
        if task_type == "juris_judge":
            return ("ISSUE: Whether rape is an offence.\n"
                    "RULE: Act 29 defines the offence.\n"
                    "APPLICATION: Act 29 applies, but Act 500 creates an "
                    "exception for minors.\n"
                    "CONCLUSION: The core point is established; the exception "
                    "is disputed.")
        return ""

    monkeypatch.setattr(reasoning, "_generate", fake)
    return calls


# ---------------------------------------------------------------------------
# pass structure
# ---------------------------------------------------------------------------

def test_advocate_structure_and_authorities_only(gen):
    out = reasoning.advocate("Is rape an offence?", [DOC])
    assert out["pass"] == "advocate"
    assert "Act 29" in out["argument"]
    assert out["authorities"] == ["Act 29"]
    assert isinstance(out["propositions"], list) and out["propositions"]
    assert "Act 29" in out["prompt"]
    assert "only" in out["prompt"].lower()
    assert out["docs"] == [DOC]


def test_opponent_structure(gen):
    out = reasoning.oppose("Is rape an offence?", [DOC],
                           "Rape is an offence under Act 29.")
    assert out["pass"] == "opponent"
    assert out["argument"].strip()
    assert isinstance(out["propositions"], list)
    assert "Act 29" in out["authorities"]


def test_judge_structure(gen):
    adv = reasoning.advocate("Is rape an offence?", [DOC])
    opp = reasoning.oppose("Is rape an offence?", [DOC], adv["argument"])
    out = reasoning.judge("Is rape an offence?", adv, opp)
    assert out["pass"] == "judge"
    assert isinstance(out["established"], list)
    assert isinstance(out["disputed"], list)
    assert isinstance(out["unresolved"], list)
    assert isinstance(out["authorities"], list)
    assert isinstance(out["confidence"], float)
    assert set(out["irac"]) >= {"issue", "rule", "application", "conclusion"}


# ---------------------------------------------------------------------------
# opponent actively hunts contrary clauses
# ---------------------------------------------------------------------------

def test_opponent_finds_planted_contrary_clause(gen, monkeypatch):
    def fake_search(query, limit=4, mode="hybrid"):
        return [CONTRARY] if "shall not apply" in query or "except" in query else []

    monkeypatch.setattr(reasoning, "_search", fake_search)
    out = reasoning.oppose("Is rape an offence?", [DOC],
                           "Rape is an offence under Act 29.")
    terms = {s["term"] for s in out["contrary_terms"]}
    assert "shall not apply" in terms
    assert "Act 500" in out["authorities"]
    assert "Act 500" in out["contrary_authorities"]
    assert "shall not apply" in out["prompt"]


def test_opponent_retrieves_with_contrary_terms(gen, monkeypatch):
    queries = []

    def fake_search(query, limit=4, mode="hybrid"):
        queries.append(query)
        return []

    monkeypatch.setattr(reasoning, "_search", fake_search)
    reasoning.oppose("bail", [DOC], "Bail is available.")
    assert any("except" in q or "notwithstanding" in q for q in queries)


# ---------------------------------------------------------------------------
# judge: dispute / unresolved / IRAC
# ---------------------------------------------------------------------------

def test_judge_marks_disputed_on_conflict(gen, monkeypatch):
    monkeypatch.setattr(reasoning, "_search",
                        lambda query, limit=4, mode="hybrid": [CONTRARY])
    adv = reasoning.advocate("Is rape an offence?", [DOC])
    opp = reasoning.oppose("Is rape an offence?", [DOC], adv["argument"])
    out = reasoning.judge("Is rape an offence?", adv, opp)
    assert out["disputed"], out
    assert out["confidence"] <= 0.5


def test_judge_marks_unresolved_when_weak(gen):
    adv = {"argument": "Nothing.", "propositions": ["The outcome depends."],
           "authorities": [], "docs": []}
    opp = {"argument": "", "propositions": [], "authorities": [],
           "docs": [], "contrary_terms": []}
    out = reasoning.judge("An obscure question?", adv, opp)
    assert out["unresolved"]
    assert not out["established"]
    assert out["confidence"] <= 0.45


def test_irac_is_always_well_formed(gen):
    adv = reasoning.advocate("Is rape an offence?", [DOC])
    opp = reasoning.oppose("Is rape an offence?", [DOC], adv["argument"])
    out = reasoning.judge("Is rape an offence?", adv, opp)
    for key in ("issue", "rule", "application", "conclusion"):
        assert isinstance(out["irac"][key], str)
        assert out["irac"][key].strip()


def test_uncertainty_is_integrated_in_judge(gen):
    adv = reasoning.advocate("Is rape an offence?", [DOC])
    opp = reasoning.oppose("Is rape an offence?", [DOC], adv["argument"])
    out = reasoning.judge("Is rape an offence?", adv, opp)
    assert out["uncertainty"]["advocate"]
    assert out["uncertainty"]["opponent"] is not None


# ---------------------------------------------------------------------------
# authority-only: no invented citations survive
# ---------------------------------------------------------------------------

def test_invented_citation_is_stripped_from_a_pass(gen, monkeypatch):
    def bad_gen(prompt, task_type):
        if task_type == "juris_advocate":
            return "Under the Fake Act 9999, rape is lawful."
        return "Act 29 applies."

    monkeypatch.setattr(reasoning, "_generate", bad_gen)

    def ver(text):
        target = "Act 9999"
        if target in text:
            s = text.index(target)
            return {"citations": [{"display": target, "status": "UNVERIFIED",
                                   "start": s, "end": s + len(target)}],
                    "summary": {"UNVERIFIED": 1}, "all_verified": False}
        return {"citations": [], "summary": {}, "all_verified": True}

    monkeypatch.setattr(reasoning, "_verify", ver)
    out = reasoning.advocate("Is rape lawful?", [DOC])
    assert "Act 9999" not in out["argument"]
    assert "unverified" in out["argument"].lower()


def test_authorities_come_only_from_supplied_docs(gen):
    out = reasoning.advocate("Is rape an offence?", [DOC])
    assert out["authorities"] == ["Act 29"]


# ---------------------------------------------------------------------------
# orchestration + graceful degradation
# ---------------------------------------------------------------------------

def test_run_deep_returns_structured_result(gen, monkeypatch):
    monkeypatch.setattr(
        reasoning._grounding, "retrieve",
        lambda query, limit=3, context="": {
            "docs": [DOC], "verdict": "GROUNDED", "stage": 1})
    res = reasoning.run_deep("Is rape an offence?")
    assert set(res) >= {"query", "docs", "verdict", "advocate", "opponent",
                        "judge", "authorities", "uncertainty", "degraded",
                        "latency"}
    assert res["advocate"]["pass"] == "advocate"
    assert res["opponent"]["pass"] == "opponent"
    assert res["judge"]["pass"] == "judge"
    assert res["authorities"]
    assert res["latency"]["advocate"] >= 0.0


def test_run_deep_ungrounded_never_calls_model(monkeypatch):
    monkeypatch.setattr(
        reasoning._grounding, "retrieve",
        lambda query, limit=3, context="": {
            "docs": [], "verdict": "UNGROUNDED", "stage": 0})
    called = []
    monkeypatch.setattr(reasoning, "_generate",
                        lambda *a, **k: called.append(a) or "x")
    res = reasoning.run_deep("quantum entanglement tax")
    assert res["verdict"] == "UNGROUNDED"
    assert called == []
    assert res["judge"]["irac"]["conclusion"].strip()
    assert res["judge"]["unresolved"]


def test_run_deep_degrades_to_single_pass_never_blank(monkeypatch):
    monkeypatch.setattr(
        reasoning._grounding, "retrieve",
        lambda query, limit=3, context="": {
            "docs": [DOC], "verdict": "GROUNDED", "stage": 1})
    monkeypatch.setattr(reasoning, "_search",
                        lambda query, limit=4, mode="hybrid": [])
    monkeypatch.setattr(
        reasoning, "_verify",
        lambda text: {"citations": [], "summary": {}, "all_verified": True})

    def boom(prompt, task_type):
        if task_type == "juris_advocate":
            raise RuntimeError("model down")
        return "Judge narrative only."

    monkeypatch.setattr(reasoning, "_generate", boom)
    res = reasoning.run_deep("Is rape an offence?")
    assert res["degraded"] is True
    assert res["advocate"]["argument"].strip()
    assert res["judge"]["irac"]["conclusion"].strip()
    assert not res["judge"]["irac"]["rule"].strip() == ""


# ---------------------------------------------------------------------------
# render_deep — the structured answer the bot delivers
# ---------------------------------------------------------------------------

def test_render_deep_has_structured_sections():
    res = {
        "authorities": ["Act 29"],
        "opponent": {"contrary_authorities": ["Act 500"]},
        "judge": {
            "irac": {"issue": "Is rape an offence?", "rule": "Act 29 says so.",
                     "application": "It applies.", "conclusion": "Established."},
            "established": [{"proposition": "Rape is an offence."}],
            "disputed": [{"proposition": "The minor exception."}],
            "unresolved": [],
            "confidence": 0.5,
            "authorities": ["Act 29"],
        },
        "docs": [DOC],
        "degraded": False,
    }
    text = reasoning.render_deep(res)
    for token in ("IRAC", "Issue", "Rule", "Application", "Conclusion",
                  "Authorities", "Act 29", "Counter-authorities", "Act 500",
                  "Uncertainty", "Established", "Disputed", "Confidence"):
        assert token in text, token


def test_render_deep_narrative_transform_skips_authority_lists():
    res = {
        "authorities": ["Act 29"],
        "opponent": {"contrary_authorities": ["Act 500"]},
        "judge": {"irac": {"issue": "I", "rule": "R", "application": "A",
                           "conclusion": "C"}, "established": [],
                  "disputed": [], "unresolved": [], "confidence": 0.0,
                  "authorities": ["Act 29"]},
        "docs": [DOC], "degraded": False,
    }
    seen = {}

    def transform(text):
        seen["text"] = text
        return text.replace("ISSUE-PLACEHOLDER", "REWRITTEN")

    out = reasoning.render_deep(res, narrative_transform=transform)
    assert "IRAC" in seen["text"]           # only the narrative is transformed
    assert "Act 500" not in seen["text"]    # authority lists are not passed in
    assert "• Act 500" in out               # ...but still rendered untouched


def test_render_deep_is_never_blank():
    text = reasoning.render_deep({})
    assert text.strip()
    assert "IRAC" in text
    assert "Authorities" in text
    assert "Uncertainty" in text


def test_render_deep_flags_degradation():
    res = {
        "judge": {"irac": {"issue": "Q"}, "established": [], "disputed": [],
                  "unresolved": [], "confidence": 0.0, "authorities": []},
        "authorities": [], "opponent": {"contrary_authorities": []},
        "docs": [], "degraded": True,
    }
    text = reasoning.render_deep(res)
    assert "degraded" in text.lower()
    assert "None retrieved" in text


def test_run_deep_survives_total_model_outage(monkeypatch):
    monkeypatch.setattr(
        reasoning._grounding, "retrieve",
        lambda query, limit=3, context="": {
            "docs": [DOC], "verdict": "GROUNDED", "stage": 1})
    monkeypatch.setattr(reasoning, "_search",
                        lambda query, limit=4, mode="hybrid": [])

    def boom(*a, **k):
        raise RuntimeError("everything is down")

    monkeypatch.setattr(reasoning, "_generate", boom)
    monkeypatch.setattr(reasoning, "_verify",
                        lambda text: (_ for _ in ()).throw(RuntimeError("down")))
    res = reasoning.run_deep("Is rape an offence?")
    assert res["degraded"] is True
    assert res["advocate"]["argument"].strip()
    assert res["judge"]["irac"]["issue"].strip()
    assert res["judge"]["irac"]["conclusion"].strip()
