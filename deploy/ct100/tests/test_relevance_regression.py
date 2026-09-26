"""Keyword relevance regression (fix A, 2026-09-26).

The handoff-proven failure: document-level max-chunk dense scoring let one
incidental chunk ("Director of the Commission", Fisheries Act 625) outrank the
Companies Act 2019 (Act 992) for "director duties". Passage aggregation (best
chunk + coverage bonus) must put the right statute on top.

Runs against the live engine's storage + embedding index; skips cleanly on a
machine without the corpus, embeddings, or the VM104 model tunnel.
Run on CT100:  cd /opt/kai-legal-brain && python3 -m pytest tests/test_relevance_regression.py
"""
import os

import pytest

from core.legal.embeddings import EmbeddingIndex


def _engine_pair():
    from legal_brain_api import engine
    storage = engine().storage
    try:
        index = EmbeddingIndex(storage)
    except Exception:  # noqa: BLE001 - no local endpoint configured
        return None, None
    try:
        if not index.stats().get("chunks"):
            return None, None
        index.client.embed("ping")
    except Exception:  # noqa: BLE001 - model/tunnel down
        return None, None
    return storage, index


PAIR = _engine_pair()
_skip = PAIR[0] is None
reason = "corpus/embeddings/model-tunnel unavailable"

pytestmark = pytest.mark.skipif(_skip, reason=reason)


@pytest.fixture(scope="module")
def pair():
    return PAIR


def test_director_duties_companies_act_is_top3(pair):
    storage, index = pair
    from core.legal.hybrid import hybrid_search
    res = hybrid_search("director duties", limit=8, storage=storage,
                        embed_index=index)
    top3 = [r.get("title") or "" for r in res[:3]]
    assert any("Companies Act" in t and "992" in t for t in top3), \
        f"Companies Act 2019 not in top 3: {top3}"


def test_director_duties_fisheries_no_longer_lead_by_margin(pair):
    """Fisheries' incidental 'Director of the Commission' chunk may still tie
    the top slot, but never on passage-coverage strength: the aggregated
    dense passage score of the Companies Act must exceed Fisheries'."""
    storage, index = pair
    from core.legal.hybrid import hybrid_search
    res = hybrid_search("director duties", limit=8, storage=storage,
                        embed_index=index)
    fish = next((r for r in res if "Fisheries" in str(r.get("title") or "")),
                None)
    comp = next((r for r in res
                 if "Companies Act" in str(r.get("title") or "")
                 and "992" in str(r.get("title") or "")), None)
    assert comp is not None, "Companies Act dropped out of results"
    if fish:
        assert (comp.get("dense_passage_score") or 0) > \
            (fish.get("dense_passage_score") or 0), \
            ("Fisheries passage coverage still beats Companies Act: "
             f"comp={comp.get('dense_passage_score')} "
             f"fish={fish.get('dense_passage_score')}")


def test_penalty_for_late_filing_hits_tax_or_companies(pair):
    storage, index = pair
    from core.legal.hybrid import hybrid_search
    res = hybrid_search("penalty for late filing", limit=8,
                        storage=storage, embed_index=index)
    titles = " || ".join((r.get("title") or "") for r in res)
    assert any(k in titles.lower() for k in
               ("companies", "revenue", "tax", "insolvency")), \
        f"no tax/companies provision in the hits: {titles!r}"


def test_company_registration_companies_act_top1(pair):
    storage, index = pair
    from core.legal.hybrid import hybrid_search
    res = hybrid_search("company registration", limit=3, storage=storage,
                        embed_index=index)
    top = str(res[0].get("title") or "")
    assert "Companies Act" in top and "992" in top, f"top was {top!r}"


def test_passage_fields_present_and_snippet_is_best_chunk(pair):
    storage, index = pair
    from core.legal.hybrid import hybrid_search
    res = hybrid_search("director duties", limit=5, storage=storage,
                        embed_index=index)
    assert res, "no results"
    for r in res:
        if r.get("match_strategy") in ("hybrid", "dense"):
            assert r.get("dense_passage_score") is not None
            assert (r.get("dense_chunks") or 0) >= 1
            assert r.get("snippet"), "best-passage snippet missing"
