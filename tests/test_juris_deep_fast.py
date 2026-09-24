"""TDD tests for Deep "fast" mode + streamed judge (Phase 8, Task 1).

The thorough Deep run (advocate ∥ opponent → judge) took ~31–34s on the single
P40 because two concurrent 500-token passes contend for one GPU and a third
serial judge pass follows. Fast mode:
  * the opponent is answered from retrieval (no GPU pass) — the root-cause fix,
  * the advocate and judge use lower per-pass budgets,
  * the judge can be streamed for low time-to-first-token.
Every existing guarantee is preserved: citation firewall per pass, uncertainty
engine, strict no-ungrounded, degradation, and per-pass latency reporting.
"""
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.juris_kai import prompt, reasoning  # noqa: E402
from core.juris_kai.prompts_reasoning import (  # noqa: E402
    ADVOCATE,
    JUDGE,
    OPPONENT,
    PASS_TASK_TYPE,
    PASS_TASK_TYPE_FAST,
    pass_task_type,
)


DOC = {
    "id": 1,
    "title": "Criminal Offences Act, 1960",
    "citation": "Act 29",
    "authority_level": "act",
    "store_mode": "full",
    "chunk_content": ("A person who commits rape is liable to imprisonment. "
                      "Act 29 defines the offence. ") + "x" * 300,
}

CONTRARY = {
    "id": 2,
    "title": "Criminal Code Amendment Act, 2003",
    "citation": "Act 646",
    "authority_level": "act",
    "store_mode": "full",
    "chunk_content": "Provided that this section shall not apply to minors.",
}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(reasoning, "_search",
                        lambda query, limit=4, mode="hybrid": [CONTRARY])
    monkeypatch.setattr(
        reasoning, "_verify",
        lambda text: {"citations": [], "summary": {}, "all_verified": True})
    monkeypatch.setattr(
        reasoning._grounding, "retrieve",
        lambda query, limit=3, context="": {
            "docs": [DOC], "verdict": "GROUNDED", "stage": 1})


def _fake_generate(monkeypatch, seen):
    def fake(task_prompt, task_type):
        seen.append(task_type)
        if task_type in ("juris_advocate", "juris_advocate_fast"):
            return "Under Act 29, rape is an offence."
        if task_type in ("juris_opponent", "juris_opponent_fast"):
            return "No contrary authority in the supplied sources."
        return ("ISSUE: Whether rape is an offence.\n"
                "RULE: Act 29 defines the offence.\n"
                "APPLICATION: Act 29 applies.\n"
                "CONCLUSION: Established.")
    monkeypatch.setattr(reasoning, "_generate", fake)
    return seen


# ---------------------------------------------------------------------------
# budgets / configuration
# ---------------------------------------------------------------------------

def test_fast_budgets_are_lower_than_thorough():
    assert prompt.budget_for(PASS_TASK_TYPE_FAST[ADVOCATE]) == 300
    assert prompt.budget_for(PASS_TASK_TYPE_FAST[OPPONENT]) == 300
    assert prompt.budget_for(PASS_TASK_TYPE_FAST[JUDGE]) == 500
    for name in (ADVOCATE, OPPONENT, JUDGE):
        assert (prompt.budget_for(PASS_TASK_TYPE_FAST[name])
                < prompt.budget_for(PASS_TASK_TYPE[name]))


def test_pass_task_type_selects_fast_variant():
    assert pass_task_type(ADVOCATE) == "juris_advocate"
    assert pass_task_type(ADVOCATE, fast=True) == "juris_advocate_fast"
    assert PASS_TASK_TYPE_FAST[OPPONENT] == "juris_opponent_fast"


def test_set_budget_is_configurable_and_validated():
    original = prompt.budget_for("juris_judge_fast")
    try:
        prompt.set_budget("juris_judge_fast", 420)
        assert prompt.budget_for("juris_judge_fast") == 420
        with pytest.raises(ValueError):
            prompt.set_budget("juris_judge_fast", 0)
    finally:
        prompt.set_budget("juris_judge_fast", original)


def test_env_budget_override(monkeypatch):
    monkeypatch.setenv("JURIS_KAI_TOKEN_BUDGETS",
                       '{"juris_judge_fast": 111}')
    original = prompt.TASK_MAX_TOKENS["juris_judge_fast"]
    try:
        prompt._apply_env_budget_overrides()
        assert prompt.budget_for("juris_judge_fast") == 111
    finally:
        prompt.TASK_MAX_TOKENS["juris_judge_fast"] = original


# ---------------------------------------------------------------------------
# fast pipeline
# ---------------------------------------------------------------------------

def test_fast_uses_fast_budgets_and_no_opponent_model_pass(monkeypatch):
    seen = _fake_generate(monkeypatch, [])
    res = reasoning.run_deep("Is rape an offence?", fast=True)

    assert "juris_advocate_fast" in seen
    assert "juris_judge_fast" in seen
    # The opponent is retrieval-only in fast mode: no GPU pass at all.
    assert not any("opponent" in t for t in seen)
    assert res["fast"] is True
    assert res["latency"]["mode"] == "fast"


def test_thorough_unchanged_uses_original_task_types(monkeypatch):
    seen = _fake_generate(monkeypatch, [])
    res = reasoning.run_deep("Is rape an offence?")
    assert "juris_advocate" in seen and "juris_opponent" in seen
    assert "juris_judge" in seen
    assert not any(t.endswith("_fast") for t in seen)
    assert res["fast"] is False
    assert res["latency"]["mode"] == "thorough"


def test_fast_still_surfaces_counter_authorities_and_uncertainty(monkeypatch):
    _fake_generate(monkeypatch, [])
    res = reasoning.run_deep("Is rape an offence?", fast=True)
    opp = res["opponent"]
    # Counter-authorities come from retrieval, so fast mode still has them.
    assert "Act 646" in opp["authorities"]
    assert any(t["term"] == "shall not apply" for t in opp["contrary_terms"])
    assert res["judge"]["irac"]["conclusion"].strip()
    assert "opponent" in res["uncertainty"]


def test_fast_result_structure_is_identical(monkeypatch):
    _fake_generate(monkeypatch, [])
    res = reasoning.run_deep("Is rape an offence?", fast=True)
    assert set(res) >= {"query", "docs", "verdict", "advocate", "opponent",
                        "judge", "authorities", "uncertainty", "degraded",
                        "latency"}
    assert res["advocate"]["pass"] == "advocate"
    assert res["opponent"]["pass"] == "opponent"
    assert res["judge"]["pass"] == "judge"


def test_fast_ungrounded_never_calls_model(monkeypatch):
    monkeypatch.setattr(
        reasoning._grounding, "retrieve",
        lambda query, limit=3, context="": {
            "docs": [], "verdict": "UNGROUNDED", "stage": 0})
    called = []
    monkeypatch.setattr(reasoning, "_generate",
                        lambda *a, **k: called.append(a) or "x")
    res = reasoning.run_deep("quantum entanglement tax", fast=True)
    assert res["verdict"] == "UNGROUNDED"
    assert called == []


def test_fast_firewall_applied(monkeypatch):
    def bad_gen(prompt, task_type):
        if task_type == "juris_advocate_fast":
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
    res = reasoning.run_deep("Is rape lawful?", fast=True)
    assert "Act 9999" not in res["advocate"]["argument"]
    assert "unverified" in res["advocate"]["argument"].lower()


# ---------------------------------------------------------------------------
# streamed judge (low TTFT)
# ---------------------------------------------------------------------------

def test_streamed_judge_reports_ttft_and_chunks(monkeypatch):
    _fake_generate(monkeypatch, [])
    pieces = ["ISSUE: q\n", "RULE: r\n", "APPLICATION: a\n",
              "CONCLUSION: c"]

    def fake_stream(prompt, task_type):
        assert task_type == "juris_judge_fast"
        for p in pieces:
            time.sleep(0.01)
            yield p

    monkeypatch.setattr(reasoning, "_stream_generate", fake_stream)
    seen_chunks, ttfts = [], []
    res = reasoning.run_deep("Is rape an offence?", fast=True, stream_judge=True,
                             on_judge_chunk=seen_chunks.append,
                             on_judge_ttft=ttfts.append)
    assert "".join(seen_chunks) == "".join(pieces)
    assert res["latency"]["judge_ttft"] >= 0.0
    assert ttfts and ttfts[0] == res["latency"]["judge_ttft"]
    assert res["judge"]["irac"]["rule"] == "r"


def test_streamed_judge_reruns_firewall(monkeypatch):
    def bad_gen(prompt, task_type):
        if task_type == "juris_advocate_fast":
            return "Under the Fake Act 9999, rape is lawful."
        return "Act 29 applies."

    monkeypatch.setattr(reasoning, "_generate", bad_gen)

    def fake_stream(prompt, task_type):
        yield "Under the Fake Act 9999, rape is lawful."

    monkeypatch.setattr(reasoning, "_stream_generate", fake_stream)

    def ver(text):
        target = "Act 9999"
        if target in text:
            s = text.index(target)
            return {"citations": [{"display": target, "status": "UNVERIFIED",
                                   "start": s, "end": s + len(target)}],
                    "summary": {"UNVERIFIED": 1}, "all_verified": False}
        return {"citations": [], "summary": {}, "all_verified": True}

    monkeypatch.setattr(reasoning, "_verify", ver)
    res = reasoning.run_deep("Is rape lawful?", fast=True, stream_judge=True)
    assert "Act 9999" not in res["judge"]["irac"]["rule"]


def test_stream_failure_falls_back_to_blocking_judge(monkeypatch):
    _fake_generate(monkeypatch, [])

    def boom(prompt, task_type):
        raise RuntimeError("stream down")

    monkeypatch.setattr(reasoning, "_stream_generate", boom)
    res = reasoning.run_deep("Is rape an offence?", fast=True, stream_judge=True)
    assert res["judge"]["irac"]["conclusion"].strip()
    assert res["latency"].get("judge_ttft") is None


def test_thorough_judge_not_streamed_by_default(monkeypatch):
    seen = _fake_generate(monkeypatch, [])

    def boom(prompt, task_type):
        raise AssertionError("thorough judge must not stream by default")

    monkeypatch.setattr(reasoning, "_stream_generate", boom)
    res = reasoning.run_deep("Is rape an offence?")
    assert res["judge"]["irac"]["conclusion"].strip()
    assert "juris_judge" in seen
