"""Hybrid BM25 + dense retrieval with Reciprocal Rank Fusion (Phase 1 T3).

Two complementary rankers run over the rights-cleared corpus:

- **BM25** via FTS5 (:meth:`core.legal.storage.LegalStorage.search`).
- **Dense** cosine nearest-neighbours via
  :class:`core.legal.embeddings.EmbeddingIndex` — embeddings from
  ``nomic-embed-text`` (768-dim) served by ollama on the **VM104 Tesla P40**,
  reached through the CT100 localhost tunnel (``127.0.0.1:11434``). There is no
  CPU model anywhere in this path.

Their rank lists are fused with **Reciprocal Rank Fusion** (Cormack et al.):
``score(d) = Σ_r w_r / (k + rank_r(d))`` with ``k=60`` by default. RRF needs no
score calibration, so a BM25 score and a cosine similarity combine safely.

Dense hits are scored at **passage level**: every fetched chunk belongs to a
document, and a document's dense signature is its best chunk *plus how many
chunks match within a small window*. A document-level max alone lets one
incidental chunk in a huge Act ("Director of the Commission", Fisheries Act)
outrank an Act with a broad, consistent match ("director duties" -> Companies
Act 2019); averaging maxes out the passage-level signal instead.

Retrieval must **never fail**: a missing, empty or erroring dense index
degrades to BM25-only, and vice-versa. The query router (T5) chooses the BM25
mode / RRF weights per intent and supplies the additive expansion used for the
dense query; the original query is never mutated.
"""
from __future__ import annotations

import math

from core.legal import ranking
from core.legal.query_router import route_query

DEFAULT_RRF_K = 60
DEFAULT_BM25_LIMIT = 150
# Dense fetch is CHUNK-level; a chunk score is cheap (already rank-ordered),
# so fetching 200 chunks costs no more than 50 and gives the passage
# aggregation enough coverage to see beyond one incidental chunk.
DEFAULT_DENSE_LIMIT = 200

# A second chunk "matches" when its cosine is within DENSE_MATCH_WINDOW of the
# document's best chunk — these are we-keep-talking-about-the-same-thing hits.
DENSE_MATCH_WINDOW = 0.05

# Coverage bonus: each extra near-best chunk lifts the passage score by a
# logarithmic step (0.10 * ln(1 + n-1)), capped at 1.0. One chunk alone gets
# bonus 0; the constants were measured on "director duties" (Fisheries' 2
# incidental chunks 0.788 vs Companies Act's 21 0.901) and "company
# registration" — they flip the ranking without inflating weak matches.
DENSE_COVERAGE_BONUS = 0.10


def rrf_fuse(rank_lists, k: int = DEFAULT_RRF_K, weights=None):
    """Fuse ordered id lists with Reciprocal Rank Fusion.

    ``rank_lists`` is a sequence of best-first id sequences (one per ranker).
    Returns ``(ordered, ranks)`` where ``ordered`` is a list of
    ``(id, score)`` sorted by descending score and ``ranks`` maps each id to
    ``{ranker_index: 1-based rank}``.
    """
    rank_lists = [list(lst) for lst in rank_lists]
    if weights is None:
        weights = [1.0] * len(rank_lists)
    weights = list(weights)
    if len(weights) != len(rank_lists):
        raise ValueError("weights must match rank_lists")
    if k < 0:
        k = 0
    scores: dict = {}
    ranks: dict = {}
    for index, lst in enumerate(rank_lists):
        weight = weights[index]
        for position, item in enumerate(lst, start=1):
            scores[item] = scores.get(item, 0.0) + weight / (k + position)
            ranks.setdefault(item, {})[index] = position
    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return ordered, ranks


def _storage_search(storage, query, limit, mode, commercial):
    """storage.search with snippets OFF when the implementation supports it.

    Fusion over-fetches (150 rows) to rank, then returns ~limit rows; FTS5
    snippet() is computed per fetched row (~6ms/row over the full-copy
    content column), so computing snippets for discarded rows wastes
    ~0.9s/query. Doubles that predate ``with_snippet`` keep working.
    """
    try:
        return storage.search(query, limit=limit, mode=mode,
                              commercial=commercial, with_snippet=False)
    except TypeError:
        return storage.search(query, limit=limit, mode=mode,
                              commercial=commercial)


def _safe_bm25(storage, query, limit, mode, commercial=False):
    """Fetch BM25 rows snippet-free; returns ``(rows, mode_used)``.

    ``mode_used`` tracks the and->or fallback so the snippet backfill asks
    FTS5 with the match expression that actually produced the rows.
    """
    if storage is None or not (query or "").strip():
        return [], mode
    try:
        rows = _storage_search(storage, query, limit, mode, commercial)
    except Exception:  # noqa: BLE001 - never fail retrieval
        rows = []
    if not rows and mode == "and":
        try:
            rows = _storage_search(storage, query, limit, "or", commercial)
            mode = "or"
        except Exception:  # noqa: BLE001
            rows = []
    return rows or [], mode


def _commercial_allowed(storage, doc_id):
    """Whether a dense-only hit may surface in commercial mode (fail-closed)."""
    if storage is None or doc_id is None:
        return False
    try:
        return storage.get_document(doc_id, commercial=True) is not None
    except Exception:  # noqa: BLE001 - an unverifiable hit is excluded
        return False


def _safe_dense(embed_index, query, limit):
    if embed_index is None or not (query or "").strip():
        return []
    try:
        return embed_index.search(query, limit=limit) or []
    except Exception:  # noqa: BLE001 - model/tunnel down -> BM25-only
        return []


def _aggregate_passages(dense_hits):
    """Collapse chunk-level dense hits into one row per document.

    Every document keeps its **best** chunk (score + the hit dict, which gives
    snippet/char offsets) and a passage score that rewards how many chunks
    match near the best. Input may be globally chunk-ordered in any direction;
    output is best-passage-first per document, sorted by descending
    ``passage_score``.
    """
    hits = sorted(dense_hits, key=lambda h: -float(h.get("score") or 0.0))
    by_doc: dict = {}
    for hit in hits:
        doc_id = hit.get("document_id")
        if doc_id is None:
            continue
        by_doc.setdefault(doc_id, []).append(hit)
    aggregated = []
    for doc_id, group in by_doc.items():
        best_hit = group[0]
        best = float(best_hit.get("score") or 0.0)
        n_hits = sum(1 for h in group
                     if float(h.get("score") or 0.0)
                     >= best - DENSE_MATCH_WINDOW)
        bonus = 1.0 + DENSE_COVERAGE_BONUS * math.log(1.0 + (n_hits - 1))
        passage_score = min(1.0, best * bonus)
        aggregated.append({
            "document_id": doc_id,
            "best_score": best,
            "chunks_matched": n_hits,
            "passage_score": passage_score,
            "best_hit": best_hit,
            "hit": group,
        })
    aggregated.sort(key=lambda a: (-a["passage_score"], a["document_id"]))
    return aggregated


def _ids(rows, key):
    seen, out = set(), []
    for row in rows:
        value = row.get(key)
        if value is None or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _first_by(rows, key):
    out = {}
    for row in rows:
        value = row.get(key)
        if value is not None and value not in out:
            out[value] = row
    return out


def _doc_view(doc_id, bm25_row, dense_hit, storage):
    if bm25_row:
        return dict(bm25_row)
    if dense_hit:
        return {"id": doc_id, "title": dense_hit.get("title"),
                "citation": dense_hit.get("citation"),
                "type": dense_hit.get("type"), "year": dense_hit.get("year")}
    if storage is not None:
        try:
            return storage.get_document(doc_id) or {}
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _default_meta_fn(storage):
    conn = getattr(storage, "conn", None)
    if conn is None:
        return lambda doc_id: None

    def _lookup(doc_id):
        from core.legal import document_meta
        return document_meta.get_meta(conn, doc_id)
    return _lookup


def _safe_meta(meta_fn, doc_id):
    try:
        return meta_fn(doc_id)
    except Exception:  # noqa: BLE001
        return None


def hybrid_search(query: str, limit: int = 10, *, storage=None,
                  embed_index=None, bm25_limit=None, dense_limit=None,
                  rrf_k: int = DEFAULT_RRF_K, use_router: bool = True,
                  meta_fn=None, commercial: bool = False) -> list:
    """Fuse BM25 + dense hits into one authority-aware ranked list.

    Dense ranking happens per **passage**: a document's dense list position is
    its best chunk score boosted by how many chunks match near it, so a
    one-off keyword hit inside a large unrelated Act cannot outrank an Act
    that treats the query throughout.

    Returns results with ``{doc_id, title, snippet, store_mode, citation, type,
    year, authority_level, authority_score, bm25_rank, dense_sim,
    dense_passage_score, dense_chunks, rrf_score, match_strategy, relevance,
    score, confidence}``.
    """
    limit = max(1, int(limit))
    plan = route_query(query) if use_router else None
    original = plan["original"] if plan else (query or "")
    expanded = plan["expanded_query"] if plan else (query or "")
    bm25_mode = plan["bm25_mode"] if plan else "or"
    weights = ([plan["weights"]["bm25"], plan["weights"]["dense"]]
               if plan else [1.0, 1.0])
    bm25_query = expanded if (plan and plan["expand_bm25"]) else original

    bm25_rows, bm25_mode_used = _safe_bm25(
        storage, bm25_query, bm25_limit or DEFAULT_BM25_LIMIT, bm25_mode,
        commercial=commercial)
    dense_hits = _safe_dense(embed_index, expanded,
                             dense_limit or DEFAULT_DENSE_LIMIT)
    if commercial:
        # The dense index is not rights-filtered; drop any hit the commercial
        # gate would not serve so RRF can never surface a non-commercial doc.
        dense_hits = [h for h in dense_hits
                      if _commercial_allowed(storage, h.get("document_id"))]

    bm25_ids = _ids(bm25_rows, "id")
    dense_doc_rows = _aggregate_passages(dense_hits)
    dense_ids = [row["document_id"] for row in dense_doc_rows]
    if not bm25_ids and not dense_ids:
        return []

    ordered, ranks = rrf_fuse([bm25_ids, dense_ids], k=rrf_k, weights=weights)
    bm25_by_id = _first_by(bm25_rows, "id")
    dense_agg_by_id = {row["document_id"]: row for row in dense_doc_rows}
    if meta_fn is None:
        meta_fn = _default_meta_fn(storage)

    results = []
    for doc_id, rrf_score in ordered[:limit]:
        bm25_row = bm25_by_id.get(doc_id)
        agg = dense_agg_by_id.get(doc_id)
        dense_hit = (agg["best_hit"] if agg else None)
        doc = _doc_view(doc_id, bm25_row, dense_hit, storage)
        ann = ranking.annotate(doc, _safe_meta(meta_fn, doc_id))
        match_strategy = ("hybrid" if (bm25_row and dense_hit)
                          else ("bm25" if bm25_row else "dense"))
        source = bm25_row or dense_hit or {}
        dense_sim = (float(agg["passage_score"]) if agg else None)
        best_chunk_score = (float(dense_hit["score"]) if dense_hit
                            and dense_hit.get("score") is not None
                            else None)
        # The best passage doubles as the snippet so consumers see the exact
        # section that matched, not an arbitrary first chunk.
        best_snippet = (dense_hit or {}).get("snippet") or ""
        results.append({
            "doc_id": doc_id,
            "id": doc_id,           # alias for /search consumers
            "title": source.get("title") or "",
            "snippet": ((bm25_row or {}).get("snippet") or best_snippet),
            "store_mode": (bm25_row or {}).get("store_mode")
            or doc.get("store_mode") or "",
            "citation": source.get("citation") or "",
            "type": source.get("type") or "",
            "year": source.get("year"),
            "authority_level": ann["authority_level"],
            "authority_tier": ann["authority_tier"],
            "authority_score": ann["authority_score"],
            "bm25_rank": (ranks.get(doc_id) or {}).get(0),
            "dense_sim": dense_sim,
            "dense_best_chunk": best_chunk_score,
            "dense_passage_score": (float(agg["passage_score"])
                                    if agg else None),
            "dense_chunks": (int(agg["chunks_matched"]) if agg else None),
            "rrf_score": rrf_score,
            "match_strategy": match_strategy,
        })
    # Backfill snippets for the surviving top hits (the BM25 over-fetch ran
    # snippet-free; see _storage_search). Dense rows already carry their best
    # passage as the snippet, so only BM25-only/hybrid rows need the FTS
    # snippet here.
    need = [r["doc_id"] for r in results
            if not r["snippet"] and r["match_strategy"] in ("bm25", "hybrid")]
    if need:
        try:
            snaps = storage.snippet_batch(bm25_query, need,
                                          mode=bm25_mode_used)
        except Exception:  # noqa: BLE001 - snippet backfill is best effort
            snaps = {}
        for r in results:
            if not r["snippet"]:
                r["snippet"] = snaps.get(r["doc_id"]) or ""
    return ranking.rank_results(results)
