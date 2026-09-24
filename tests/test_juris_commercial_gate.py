"""The commercial (company) arm threads ``commercial=True`` through retrieval.

The strict no-ungrounded rule is unchanged: commercial mode only narrows the
source set, it never lets an ungrounded answer through.
"""
from core.juris_kai import grounding


def _hit():
    return {"title": "Criminal Offences Act", "citation": "Act 29",
            "store_mode": "full", "chunk_content": "x" * 500,
            "authority_level": "act", "score": 0.87, "confidence": 0.80,
            "bm25_rank": 1, "match_strategy": "hybrid"}


def test_retrieve_threads_commercial_to_search(monkeypatch):
    seen = []

    def fake(query, limit=3, mode="or", commercial=False):
        seen.append(commercial)
        return [_hit()] if mode == "hybrid" else []

    monkeypatch.setattr(grounding, "_search", fake)
    r = grounding.retrieve("rape", commercial=True)
    assert r["verdict"] == "GROUNDED"
    assert seen and all(flag is True for flag in seen)


def test_retrieve_defaults_to_foundation_mode(monkeypatch):
    seen = []

    def fake(query, limit=3, mode="or", commercial=False):
        seen.append(commercial)
        return [_hit()] if mode == "hybrid" else []

    monkeypatch.setattr(grounding, "_search", fake)
    grounding.retrieve("rape")
    assert seen and all(flag is False for flag in seen)


def test_build_grounded_plan_threads_commercial(monkeypatch):
    captured = {}

    def fake_retrieve(query, limit=3, context="", commercial=False):
        captured["commercial"] = commercial
        return {"docs": [{"title": "T", "chunk_content": "x" * 500,
                          "citation": "Act 1"}],
                "verdict": "GROUNDED", "stage": 2}

    monkeypatch.setattr(grounding, "retrieve", fake_retrieve)
    plan = grounding.build_grounded_plan("Criminal Offences Act", commercial=True)
    assert captured["commercial"] is True
    assert plan["groundable"] is True and plan["refusal"] is None


def test_build_grounded_plan_defaults_to_foundation_mode(monkeypatch):
    captured = {}

    def fake_retrieve(query, limit=3, context="", commercial=False):
        captured["commercial"] = commercial
        return {"docs": [], "verdict": "UNGROUNDED", "stage": 0}

    monkeypatch.setattr(grounding, "retrieve", fake_retrieve)
    plan = grounding.build_grounded_plan("quantum tax")
    assert captured["commercial"] is False
    assert plan["groundable"] is False and plan["refusal"]


def test_commercial_mode_keeps_strict_grounding(monkeypatch):
    # Nothing retrieved under the commercial gate => still a refusal.
    def fake(query, limit=3, mode="or", commercial=False):
        return []

    monkeypatch.setattr(grounding, "_search", fake)
    r = grounding.retrieve("rape", commercial=True)
    assert r["verdict"] == "UNGROUNDED" and r["docs"] == []


def test_hydrate_threads_commercial(monkeypatch):
    seen = {}

    def fake_get(doc_id, commercial=False):
        seen["commercial"] = commercial
        return {"content": "c" * 900}

    monkeypatch.setattr("core.legal_brain_client.get_document", fake_get)
    out = grounding._hydrate({"id": 1, "title": "T", "snippet": "short",
                              "store_mode": "full"}, commercial=True)
    assert seen["commercial"] is True
    assert out["chunk_content"]
