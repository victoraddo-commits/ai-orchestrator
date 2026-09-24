"""TDD tests for Deep Research parallelisation (Phase 8 Task 1).

``reasoning.run_deep`` must run the two retrieval-only passes — advocate and
opponent — **concurrently**, then feed both into the judge. The opponent no
longer consumes the advocate's output (it depended only on retrieval anyway),
which is what makes the two independent.

Every existing guarantee is preserved: per-pass citation firewall, uncertainty
engine, strict no-ungrounded (the model is never called without retrieval),
graceful degradation to the single-pass fallback, per-pass latency reporting,
and a sequential fallback if the thread pool cannot be used.
"""
import sys
import threading
import time
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


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(reasoning, "_search",
                        lambda query, limit=4, mode="hybrid": [])
    monkeypatch.setattr(
        reasoning, "_verify",
        lambda text: {"citations": [], "summary": {}, "all_verified": True})
    monkeypatch.setattr(
        reasoning._grounding, "retrieve",
        lambda query, limit=3, context="": {
            "docs": [DOC], "verdict": "GROUNDED", "stage": 1})


def _gen(monkeypatch):
    def fake(prompt, task_type):
        if task_type == "juris_advocate":
            return "Under Act 29, rape is an offence."
        if task_type == "juris_opponent":
            return "No contrary authority in the supplied sources."
        if task_type == "juris_judge":
            return ("ISSUE: Whether rape is an offence.\n"
                    "RULE: Act 29 defines the offence.\n"
                    "APPLICATION: Act 29 applies.\n"
                    "CONCLUSION: Established.")
        return ""
    monkeypatch.setattr(reasoning, "_generate", fake)


# ---------------------------------------------------------------------------
# concurrency
# ---------------------------------------------------------------------------

def test_advocate_and_opponent_run_concurrently(monkeypatch):
    """Both passes must overlap in time; sequential execution cannot."""
    _gen(monkeypatch)
    starts, ends = {}, {}
    lock = threading.Lock()

    def fake_adv(q, docs):
        with lock:
            starts["adv"] = time.perf_counter()
        time.sleep(0.25)
        with lock:
            ends["adv"] = time.perf_counter()
        return {"pass": "advocate", "argument": "A", "propositions": [],
                "authorities": ["Act 29"], "docs": list(docs),
                "citations": [], "error": None}

    def fake_opp(q, docs, argument=""):
        with lock:
            starts["opp"] = time.perf_counter()
        time.sleep(0.25)
        with lock:
            ends["opp"] = time.perf_counter()
        return {"pass": "opponent", "argument": "B", "propositions": [],
                "authorities": ["Act 29"], "docs": list(docs),
                "contrary_authorities": [], "contrary_terms": [],
                "citations": [], "error": None}

    monkeypatch.setattr(reasoning, "advocate", fake_adv)
    monkeypatch.setattr(reasoning, "oppose", fake_opp)

    reasoning.run_deep("Is rape an offence?")

    assert set(starts) == {"adv", "opp"}
    # Overlap: each pass started before the other finished.
    assert starts["opp"] < ends["adv"]
    assert starts["adv"] < ends["opp"]


def test_opponent_does_not_depend_on_advocate_output(monkeypatch):
    """The opponent is called with no advocate argument (independent pass)."""
    _gen(monkeypatch)
    seen = {}

    def fake_opp(q, docs, argument=""):
        seen["argument"] = argument
        return {"pass": "opponent", "argument": "B", "propositions": [],
                "authorities": [], "docs": list(docs),
                "contrary_authorities": [], "contrary_terms": [],
                "citations": [], "error": None}

    monkeypatch.setattr(reasoning, "oppose", fake_opp)
    reasoning.run_deep("Is rape an offence?")
    assert seen["argument"] == ""


def test_judge_consumes_both_pass_results(monkeypatch):
    _gen(monkeypatch)
    captured = {}

    def fake_judge(q, adv, opp, generate=None):
        captured["adv"] = adv
        captured["opp"] = opp
        return {"pass": "judge", "established": [], "disputed": [],
                "unresolved": [], "authorities": [], "confidence": 0.0,
                "irac": {"issue": q, "rule": "r", "application": "a",
                         "conclusion": "c"}, "uncertainty": {}, "docs": []}

    monkeypatch.setattr(reasoning, "judge", fake_judge)
    res = reasoning.run_deep("Is rape an offence?")
    assert captured["adv"]["pass"] == "advocate"
    assert captured["opp"]["pass"] == "opponent"
    assert res["judge"]["pass"] == "judge"


# ---------------------------------------------------------------------------
# degradation
# ---------------------------------------------------------------------------

def test_advocate_failure_degrades_but_opponent_still_runs(monkeypatch):
    def boom(prompt, task_type):
        if task_type == "juris_advocate":
            raise RuntimeError("model down")
        if task_type == "juris_judge":
            return "ISSUE: q\nRULE: r\nAPPLICATION: a\nCONCLUSION: c"
        return "No contrary authority in the supplied sources."

    monkeypatch.setattr(reasoning, "_generate", boom)
    res = reasoning.run_deep("Is rape an offence?")
    assert res["degraded"] is True
    assert res["advocate"]["argument"].strip()
    assert res["opponent"]["pass"] == "opponent"
    assert res["judge"]["irac"]["conclusion"].strip()


def test_opponent_failure_degrades_to_empty_pass(monkeypatch):
    _gen(monkeypatch)

    def boom(q, docs, argument=""):
        raise RuntimeError("opponent down")

    monkeypatch.setattr(reasoning, "oppose", boom)
    res = reasoning.run_deep("Is rape an offence?")
    assert res["degraded"] is True
    assert res["opponent"]["pass"] == "opponent"
    assert res["opponent"]["argument"] == ""
    assert res["judge"]["irac"]["conclusion"].strip()


def test_sequential_fallback_when_pool_unavailable(monkeypatch):
    _gen(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("no threads")

    monkeypatch.setattr(reasoning, "_ThreadPoolExecutor", boom)
    res = reasoning.run_deep("Is rape an offence?")
    assert res["advocate"]["pass"] == "advocate"
    assert res["opponent"]["pass"] == "opponent"
    assert res["judge"]["irac"]["issue"].strip()


def test_ungrounded_never_calls_model(monkeypatch):
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


# ---------------------------------------------------------------------------
# guarantees preserved
# ---------------------------------------------------------------------------

def test_firewall_applied_to_each_pass(monkeypatch):
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
    res = reasoning.run_deep("Is rape lawful?")
    assert "Act 9999" not in res["advocate"]["argument"]
    assert "unverified" in res["advocate"]["argument"].lower()


def test_latency_reported_per_pass(monkeypatch):
    _gen(monkeypatch)
    res = reasoning.run_deep("Is rape an offence?")
    lat = res["latency"]
    for key in ("advocate", "opponent", "judge", "total"):
        assert key in lat, key
        assert lat[key] >= 0.0
    assert lat["total"] >= 0.0


def test_result_structure_is_identical(monkeypatch):
    _gen(monkeypatch)
    res = reasoning.run_deep("Is rape an offence?")
    assert set(res) >= {"query", "docs", "verdict", "advocate", "opponent",
                        "judge", "authorities", "uncertainty", "degraded",
                        "latency"}
    assert res["advocate"]["pass"] == "advocate"
    assert res["opponent"]["pass"] == "opponent"
    assert res["judge"]["pass"] == "judge"
    assert isinstance(res["authorities"], list)
    assert isinstance(res["uncertainty"], dict)
    assert res["degraded"] is False
