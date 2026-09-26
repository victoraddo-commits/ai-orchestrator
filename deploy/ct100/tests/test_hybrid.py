"""Tests for hybrid BM25 + dense retrieval with Reciprocal Rank Fusion (T3).

Fusion math is pure and tested directly; the orchestration is tested with fake
storage / embedding-index doubles so no model or database is needed. Retrieval
must degrade to BM25-only (never raise) whenever the dense side is missing,
empty, or failing.
"""
import pytest

from core.legal import hybrid
from core.legal.embeddings import EmbeddingError


# ── doubles ──────────────────────────────────────────────────────────────

class FakeStorage:
    conn = None

    def __init__(self, rows=None, rows_by_mode=None):
        self.rows = rows or []
        self.rows_by_mode = rows_by_mode or {}
        self.calls = []

    def search(self, query, limit=20, mode="or", commercial=False):
        self.calls.append((query, limit, mode))
        rows = self.rows_by_mode.get(mode, self.rows)
        return rows[:limit]


class BoomStorage:
    conn = None

    def search(self, *a, **k):
        raise RuntimeError("fts unavailable")


class FakeEmbedIndex:
    def __init__(self, hits=None, error=None):
        self.hits = hits or []
        self.error = error
        self.calls = []

    def search(self, query, limit=10):
        self.calls.append((query, limit))
        if self.error is not None:
            raise self.error
        return self.hits[:limit]


def bm25_row(doc_id, title, snippet="snippet text", type_="act",
             store_mode="full", year=1960, citation=""):
    return {"id": doc_id, "title": title, "snippet": snippet,
            "store_mode": store_mode, "type": type_, "year": year,
            "citation": citation}


def dense_hit(doc_id, title, score, snippet="dense text", type_="act",
              year=1960, citation=""):
    return {"document_id": doc_id, "title": title, "score": score,
            "snippet": snippet, "chunk_index": 0, "char_start": 0,
            "char_end": len(snippet), "type": type_, "year": year,
            "citation": citation}


# ── RRF math ─────────────────────────────────────────────────────────────

def test_rrf_fusion_math_k60():
    ordered, ranks = hybrid.rrf_fuse([["a", "b", "c"], ["c"]], k=60)
    scores = dict(ordered)
    assert scores["a"] == pytest.approx(1 / 61)
    assert scores["b"] == pytest.approx(1 / 62)
    assert scores["c"] == pytest.approx(1 / 63 + 1 / 61)
    assert ordered[0][0] == "c"
    assert ranks["a"] == {0: 1}


def test_rrf_k_changes_scores_but_not_single_list_order():
    ordered, _ = hybrid.rrf_fuse([["a", "b"]], k=1)
    scores = dict(ordered)
    assert scores["a"] == pytest.approx(1 / 2)
    assert scores["b"] == pytest.approx(1 / 3)
    assert [i for i, _ in ordered] == ["a", "b"]


def test_rrf_weighted_favours_heavier_list():
    ordered, _ = hybrid.rrf_fuse([["a"], ["b"]], k=60, weights=[1.0, 0.1])
    scores = dict(ordered)
    assert scores["a"] > scores["b"]


# ── fusion / dedup ───────────────────────────────────────────────────────

def test_hybrid_dedups_across_strategies():
    st = FakeStorage(rows=[bm25_row(1, "Act One"), bm25_row(2, "Act Two")])
    idx = FakeEmbedIndex([dense_hit(2, "Act Two", 0.9),
                          dense_hit(3, "Act Three", 0.8)])
    res = hybrid.hybrid_search("land", limit=10, storage=st, embed_index=idx)
    assert sorted(r["doc_id"] for r in res) == [1, 2, 3]
    doc2 = next(r for r in res if r["doc_id"] == 2)
    assert doc2["match_strategy"] == "hybrid"
    assert doc2["bm25_rank"] is not None
    assert doc2["dense_sim"] == pytest.approx(0.9)
    assert doc2["snippet"] == "snippet text"   # BM25 snippet preferred


def test_hybrid_result_shape():
    st = FakeStorage(rows=[bm25_row(1, "Act One")])
    idx = FakeEmbedIndex([dense_hit(1, "Act One", 0.7)])
    res = hybrid.hybrid_search("land", limit=5, storage=st, embed_index=idx)
    expected = {"doc_id", "title", "snippet", "store_mode", "authority_level",
                "authority_score", "bm25_rank", "dense_sim", "rrf_score",
                "match_strategy", "score", "confidence", "relevance"}
    assert expected <= set(res[0])


# ── graceful fallback ────────────────────────────────────────────────────

def test_fallback_when_dense_empty():
    st = FakeStorage(rows=[bm25_row(1, "Act One"), bm25_row(2, "Act Two")])
    res = hybrid.hybrid_search("land", storage=st, embed_index=FakeEmbedIndex([]))
    assert [r["doc_id"] for r in res] == [1, 2]
    assert all(r["match_strategy"] == "bm25" for r in res)
    assert all(r["dense_sim"] is None for r in res)


def test_fallback_when_dense_raises():
    st = FakeStorage(rows=[bm25_row(1, "Act One")])
    idx = FakeEmbedIndex(error=EmbeddingError("ollama down"))
    res = hybrid.hybrid_search("land", storage=st, embed_index=idx)
    assert [r["doc_id"] for r in res] == [1]
    assert res[0]["match_strategy"] == "bm25"


def test_fallback_when_no_dense_index():
    st = FakeStorage(rows=[bm25_row(1, "Act One")])
    res = hybrid.hybrid_search("land", storage=st, embed_index=None)
    assert [r["doc_id"] for r in res] == [1]


def test_dense_only_when_bm25_raises():
    idx = FakeEmbedIndex([dense_hit(7, "Judgment Seven", 0.8)])
    res = hybrid.hybrid_search("mensah", storage=BoomStorage(),
                               embed_index=idx)
    assert [r["doc_id"] for r in res] == [7]
    assert res[0]["match_strategy"] == "dense"


def test_empty_inputs_return_empty():
    assert hybrid.hybrid_search("x", storage=FakeStorage([]),
                                embed_index=FakeEmbedIndex([])) == []
    assert hybrid.hybrid_search("x", storage=None, embed_index=None) == []


def test_bm25_and_mode_falls_back_to_or():
    st = FakeStorage(rows_by_mode={"and": [], "or": [bm25_row(1, "Act 29")]})
    res = hybrid.hybrid_search("Act 29", storage=st, embed_index=None)
    assert [r["doc_id"] for r in res] == [1]
    modes = [c[2] for c in st.calls]
    assert modes == ["and", "or"]


# ── router integration ───────────────────────────────────────────────────

def test_statute_lookup_keeps_bm25_query_exact_but_expands_dense():
    st = FakeStorage(rows=[bm25_row(1, "Criminal Offences Act")])
    idx = FakeEmbedIndex([dense_hit(1, "Criminal Offences Act", 0.8)])
    hybrid.hybrid_search("Act 29", storage=st, embed_index=idx)
    assert st.calls[0][0] == "Act 29"                 # exact, unexpanded
    assert "Criminal Offences Act 1960" in idx.calls[0][0]


def test_concept_query_expands_bm25_and_dense():
    st = FakeStorage(rows=[bm25_row(1, "Theft")])
    idx = FakeEmbedIndex([dense_hit(1, "Theft", 0.7)])
    hybrid.hybrid_search("theft", storage=st, embed_index=idx)
    assert "stealing" in st.calls[0][0]
    assert "stealing" in idx.calls[0][0]


def test_authority_promotes_statute_over_commentary():
    st = FakeStorage(rows=[
        bm25_row(2, "Commentary on Land", type_="other"),
        bm25_row(1, "State Lands Act", type_="act"),
    ])
    idx = FakeEmbedIndex([
        dense_hit(2, "Commentary on Land", 0.7, type_="other"),
        dense_hit(1, "State Lands Act", 0.7, type_="act"),
    ])
    res = hybrid.hybrid_search("land", storage=st, embed_index=idx)
    assert res[0]["doc_id"] == 1
    assert res[0]["authority_level"] == "act"
    assert res[0]["score"] > res[1]["score"]


def test_router_can_be_disabled():
    st = FakeStorage(rows=[bm25_row(1, "Act One")])
    idx = FakeEmbedIndex([dense_hit(1, "Act One", 0.5)])
    res = hybrid.hybrid_search("theft", storage=st, embed_index=idx,
                               use_router=False)
    assert st.calls[0][0] == "theft"
    assert idx.calls[0][0] == "theft"
    assert res


# ── snippet-free fusion fetch (fix C, 2026-09-26) ─────────────────────────

class ModernStorage(FakeStorage):
    """Storage whose search supports ``with_snippet`` (real signature)."""

    def search(self, query, limit=20, mode="or", commercial=False,
               with_snippet=True):
        self.calls.append((query, limit, mode, with_snippet))
        rows = self.rows_by_mode.get(mode, self.rows)
        return rows[:limit]


class SnippetlessRowsStorage(FakeStorage):
    """Modern signature; returns rows WITHOUT a snippet column (like the
    real with_snippet=False path)."""

    def search(self, query, limit=20, mode="or", commercial=False,
               with_snippet=True):
        self.calls.append((query, limit, mode, with_snippet))
        return [dict(r, snippet=None) for r in
                self.rows_by_mode.get(mode, self.rows)[:limit]]


def test_fusion_fetches_snippet_free_when_supported():
    st = ModernStorage(rows=[bm25_row(1, "Act One", snippet="s")])
    idx = FakeEmbedIndex([])
    res = hybrid.hybrid_search("land", limit=5, storage=st, embed_index=idx)
    assert res
    assert st.calls[0][3] is False, "fusion must request snippet-free rows"


def test_bm25_snippet_backfilled_from_snippet_batch():
    st = SnippetlessRowsStorage(rows=[bm25_row(1, "Act One")])
    st.snippets = {1: "the refetched FTS snippet"}
    st.snippet_calls = []

    def snippet_batch(query, doc_ids, mode="or", tokens=None):
        st.snippet_calls.append((query, tuple(doc_ids)))
        return st.snippets

    st.snippet_batch = snippet_batch
    idx = FakeEmbedIndex([])
    res = hybrid.hybrid_search("land", limit=5, storage=st, embed_index=idx)
    assert res[0]["snippet"] == "the refetched FTS snippet"


def test_snippet_backfill_tolerates_storage_without_snippet_batch():
    st = SnippetlessRowsStorage(rows=[bm25_row(1, "Act One")])
    idx = FakeEmbedIndex([])
    res = hybrid.hybrid_search("land", limit=5, storage=st, embed_index=idx)
    assert res, "search must not fail without snippet_batch support"
    assert res[0]["snippet"] == ""


def test_and_fallback_tracks_mode_for_snippets():
    st = ModernStorage(
        rows=[bm25_row(1, "Act One")],
        rows_by_mode={"and": [], "or": [bm25_row(2, "Act Two", snippet=None)]})
    idx = FakeEmbedIndex([])
    st.fallback_mode = None

    def snippet_batch(query, doc_ids, mode="or", tokens=None):
        st.fallback_mode = mode
        return {}

    st.snippet_batch = snippet_batch
    res = hybrid.hybrid_search("Act 29", limit=5, storage=st,
                               embed_index=idx, use_router=False)
    assert res
    assert st.fallback_mode == "or", (
        "snippet refetch must use the mode that produced the rows")


def test_dense_snippet_preferred_over_backfill():
    """Hybrid rows already carry the best passage; the FTS backfill must not
    overwrite it."""
    st = SnippetlessRowsStorage(rows=[bm25_row(1, "Act One")])

    def snippet_batch(query, doc_ids, mode="or", tokens=None):
        return {1: "fts text"}

    st.snippet_batch = snippet_batch
    idx = FakeEmbedIndex([dense_hit(1, "Act One", 0.5,
                                    snippet="dense passage")])
    res = hybrid.hybrid_search("land", limit=5, storage=st, embed_index=idx)
    assert res[0]["snippet"] == "dense passage"
