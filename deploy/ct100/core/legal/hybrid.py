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

**Dual dense-source fusion** (env ``JURIS_KAI_DENSE_FUSION``): the legacy
1100-char window index (SQLite) and the pgvector structure-chunk index see
different chunk granularities of the same corpus, so fusing BOTH dense lists
alongside BM25 reinforces documents that match in either space while a
structure chunk that lost context in the pgvector list can still be carried
by its legacy-window twin. Values:

- ``both``      — fuse ``[bm25, legacy_dense, pg_dense]`` with weights
  ``[bm25_w, dense_w, dense_w * DENSE_SECONDARY_WEIGHT]``;
- ``pgvector`` / ``sqlite`` (or unset) — single-source fusion, i.e. today's
  two-list behaviour (default follows ``JURIS_KAI_DENSE_BACKEND``).

The companion index is built lazily from ``storage`` and cached per
``(kind, db_path)``; every dense call stays fail-safe, so a dead companion
silently degrades to single-source fusion.
"""
from __future__ import annotations

import math
import os
import threading

from core.legal import ranking
from core.legal.query_router import route_query

DENSE_FUSION_ENV = "JURIS_KAI_DENSE_FUSION"
DENSE_SECONDARY_WEIGHT = 0.9

DEFAULT_RRF_K = 60
DEFAULT_BM25_LIMIT = 150
# Dense fetch is CHUNK-level; a chunk score is cheap (already rank-ordered),
# so fetching 200 chunks costs no more than 50 and gives the passage
# aggregation enough coverage to see beyond one incidental chunk.
DEFAULT_DENSE_LIMIT = 200

# A second chunk "matches" when its cosine is within DENSE_MATCH_WINDOW of the
# document's best chunk — these are we-keep-talking-about-the-same-thing hits.
DENSE_MATCH_WINDOW = 0.05

# The authority-aware re-rank must see more candidates than the final page:
# cutting by raw RRF first re-creates the near-tie bug at small limits
# ("director duties" limit=3 returned Fisheries: Companies Act 992 was RRF
# rank 4 and never reached the re-rank; measured 2026-09-26).
DEFAULT_RANK_POOL = 25

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


def dense_fusion_mode(override: str = None) -> str:
    """Resolve the dual-source fusion mode.

    ``both`` fuses both dense lists; ``pgvector``/``sqlite`` (or unknown)
    keep today's single-source behaviour. Unset follows
    ``JURIS_KAI_DENSE_BACKEND`` so the drop-in switch keeps working unchanged.
    """
    fused = (os.environ.get(DENSE_FUSION_ENV, "").strip().lower()
             if (override or "") == "" else override.strip().lower())
    if fused == "both":
        return "both"
    return "single"


def _is_pg_provider(index) -> bool:
    return type(index).__name__ == "PgVectorDense"


_COMPANION_LOCK = threading.Lock()
_COMPANION: dict = {}


def _companion_index(embed_index, storage):
    """The OTHER dense source for dual-source fusion, cached per storage.

    A PgVectorDense primary gets a legacy :class:`EmbeddingIndex` companion
    and vice-versa; the companion is built lazily (construction is cheap:
    the ANN snapshot is cached module-wide and pg connects on first use) and
    cached in ``_COMPANION`` keyed by ``(kind, db_path)`` so connection
    persistence and caches survive across requests. Any failure returns None
    and retrieval silently degrades to single-source.
    """
    if storage is None or embed_index is None:
        return None
    kind = "pg" if _is_pg_provider(embed_index) else "legacy"
    key = (kind, getattr(storage, "db_path", None) or id(storage))
    with _COMPANION_LOCK:
        idx = _COMPANION.get(key)
        if idx is not None:
            return idx
        try:
            if kind == "pg":
                from core.legal.pg_dense import PgVectorDense
                idx = PgVectorDense(storage=storage)
            else:
                from core.legal.embeddings import EmbeddingIndex
                idx = EmbeddingIndex(storage)
        except Exception:  # noqa: BLE001 - companion is optional
            return None
        _COMPANION[key] = idx
        return idx


def _merge_dense_agg(rows_by_list) -> dict:
    """Merge per-source passage aggregations into one ``document_id`` map.

    A document seen by several dense sources keeps the aggregation with the
    higher passage score (its strongest evidence), which also feeds
    ``_doc_view``/snippet selection exactly like the single-source path.
    """
    merged: dict = {}
    for rows in rows_by_list:
        for row in rows:
            doc_id = row["document_id"]
            current = merged.get(doc_id)
            if (current is None
                    or float(row["passage_score"]) > float(
                        current["passage_score"])):
                merged[doc_id] = row
    return merged


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
                  meta_fn=None, commercial: bool = False,
                  fusion: str = None) -> list:
    """Fuse BM25 + dense hits into one authority-aware ranked list.

    Dense ranking happens per **passage**: a document's dense list position is
    its best chunk score boosted by how many chunks match near it, so a
    one-off keyword hit inside a large unrelated Act cannot outrank an Act
    that treats the query throughout.

    With dual-source fusion (``JURIS_KAI_DENSE_FUSION=both``, or the
    ``fusion`` override), the legacy window index AND the pgvector
    structure-chunk index both contribute a dense list to the RRF fusion; see
    the module docstring.

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

    # Dual-source fusion: when enabled, the primary dense provider runs at
    # ~2x dense_limit (passage aggregation wants chunk coverage, and both
    # sources pay for it in rank position, not latency) and the companion
    # index contributes a second dense list fused at a 0.9-damped weight.
    dual = dense_fusion_mode(fusion) == "both"
    fusion_fetch = (dense_limit or DEFAULT_DENSE_LIMIT) * 2 if dual else (
        dense_limit or DEFAULT_DENSE_LIMIT)
    dense_hits = _safe_dense(embed_index, expanded, fusion_fetch)
    companion = _companion_index(embed_index, storage) if dual else None
    companion_hits = (_safe_dense(companion, expanded, fusion_fetch)
                      if companion is not None else [])
    if commercial:
        # The dense indexes are not rights-filtered; drop any hit the
        # commercial gate would not serve so RRF can never surface a
        # non-commercial doc.
        dense_hits = [h for h in dense_hits
                      if _commercial_allowed(storage, h.get("document_id"))]
        companion_hits = [h for h in companion_hits
                          if _commercial_allowed(storage, h.get("document_id"))]

    bm25_ids = _ids(bm25_rows, "id")
    dense_doc_rows = _aggregate_passages(dense_hits)
    companion_doc_rows = (_aggregate_passages(companion_hits)
                          if dual and companion is not None else [])
    # The PRIMARY dense list feeds RRF with its own docs only; the merged
    # aggregation (primary wins on equal/greater passage score) is used for
    # the doc view / snippet / dense stats below.
    dense_ids = [row["document_id"] for row in dense_doc_rows]
    dense_agg_by_id = _merge_dense_agg([dense_doc_rows, companion_doc_rows])
    if not bm25_ids and not dense_agg_by_id:
        return []

    if dual and companion_doc_rows:
        # Primary source keeps the full dense weight; the companion list is
        # fused damped so neither dense space dominates the fusion.
        ordered, ranks = rrf_fuse(
            [bm25_ids,
             [r["document_id"] for r in companion_doc_rows],
             dense_ids],
            k=rrf_k,
            weights=[weights[0], weights[1] * DENSE_SECONDARY_WEIGHT,
                     weights[1]])
    else:
        ordered, ranks = rrf_fuse([bm25_ids, dense_ids], k=rrf_k,
                                  weights=weights)
    pool = max(limit, DEFAULT_RANK_POOL)
    bm25_by_id = _first_by(bm25_rows, "id")
    if meta_fn is None:
        meta_fn = _default_meta_fn(storage)

    results = []
    for doc_id, rrf_score in ordered[:pool]:
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
    return ranking.rank_results(results)[:limit]
