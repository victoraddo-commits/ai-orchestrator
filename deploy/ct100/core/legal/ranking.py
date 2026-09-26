"""Authority-aware ranking + per-result confidence (Legal Brain 2.0 Phase 1 T4).

Retrieval relevance is not the whole answer: a controlling statute must never
be outranked by secondary commentary or an adjacent article. This module maps
a document (plus its optional ``document_meta`` sidecar) onto a small authority
tier — Constitution > Act > LI/CI/EI (instrument) > judgment > gazette >
secondary/commentary > unverified — and blends that with relevance.

The blend is intentionally bounded: authority can settle a near-tie but cannot
drag a much more relevant secondary source to the top. ``confidence`` is a
per-result 0..1 signal (similarity + authority + match stage) that
``grounding.retrieve`` can threshold on.
"""
from __future__ import annotations

import re

TIER_CONSTITUTION = 0
TIER_ACT = 1
TIER_INSTRUMENT = 2
TIER_JUDGMENT = 3
TIER_GAZETTE = 4
TIER_SECONDARY = 5
TIER_UNVERIFIED = 6

TIER_LABELS = {
    TIER_CONSTITUTION: "constitution",
    TIER_ACT: "act",
    TIER_INSTRUMENT: "instrument",
    TIER_JUDGMENT: "judgment",
    TIER_GAZETTE: "gazette",
    TIER_SECONDARY: "secondary",
    TIER_UNVERIFIED: "unverified",
}

# Authority component in [0, 1]; higher = stronger.
AUTHORITY_SCORES = {
    TIER_CONSTITUTION: 1.0,
    TIER_ACT: 0.9,
    TIER_INSTRUMENT: 0.75,
    TIER_JUDGMENT: 0.6,
    TIER_GAZETTE: 0.4,
    TIER_SECONDARY: 0.15,
    TIER_UNVERIFIED: 0.0,
}

# Relevance-level mix: the fused RRF rank signal vs the per-ranker
# similarity signal. RRF is rank-only (a BM25 rank-1 on an incidental
# "or"-mode hit can outweigh a much higher dense cosine otherwise);
# 0.5/0.5 lets the passage score (dense_sim) settle genuine near-ties
# measured on "director duties" (Fisheries 0.788 vs Companies Act 0.901).
RELEVANCE_RRF_WEIGHT = 0.5
RELEVANCE_DENSE_WEIGHT = 0.5

# Default blend weights: relevance leads, authority breaks near-ties.
W_RELEVANCE = 0.7
W_AUTHORITY = 0.3

# Confidence weights: similarity, authority, match stage.
CONFIDENCE_WEIGHTS = (0.5, 0.3, 0.2)
STAGE_CONFIDENCE = {"hybrid": 1.0, "dense": 0.7, "bm25": 0.5}

_CONSTITUTION_RE = re.compile(r"\bconstitution\b", re.IGNORECASE)
# A Bill is proposed legislation: it has not been enacted and carries no legal
# authority. It must never earn a primary tier just because its title names the
# Constitution or an Act's subject. Singular "bill" only -- "Bills of Exchange
# Act" is an enacted Act (plural) and must stay a primary Act.
_BILL_RE = re.compile(r"\bbill\b", re.IGNORECASE)

_SOURCE_TYPE_KIND = {
    "li": "instrument", "ci": "instrument", "ei": "instrument",
    "regulation": "instrument", "act": "act", "judgment": "judgment",
    "gazette": "gazette", "bill": "unverified",
}

# core.legal.authority.authority_level(type) -> our kind.
_AUTHORITY_KIND = {
    "constitution": "constitution", "act": "act",
    "legislative_instrument": "instrument", "regulation": "instrument",
    "judgment": "judgment", "gazette": "gazette",
    "secondary": "secondary", "unverified": "unverified",
}

_KIND_TIER = {
    "constitution": TIER_CONSTITUTION, "act": TIER_ACT,
    "instrument": TIER_INSTRUMENT, "judgment": TIER_JUDGMENT,
    "gazette": TIER_GAZETTE, "secondary": TIER_SECONDARY,
    "unverified": TIER_UNVERIFIED,
}

_PRIMARY_KINDS = ("constitution", "act", "instrument", "judgment")


def clamp01(value) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, value))


def _kind(doc, meta) -> str:
    """Classify what a document *is*, independent of where it is hosted."""
    doc = doc or {}
    title = f"{doc.get('title') or ''} {doc.get('citation') or ''}"
    if _BILL_RE.search(title):
        return "unverified"
    if _CONSTITUTION_RE.search(title):
        return "constitution"
    source_type = ((meta or {}).get("source_type") or "").strip().lower()
    kind = _SOURCE_TYPE_KIND.get(source_type)
    if kind:
        return kind
    from core.legal.authority import authority_level as _type_authority
    return _AUTHORITY_KIND.get(_type_authority(doc), "secondary")


def authority_tier(doc, meta=None) -> int:
    """Numeric authority tier (lower = stronger)."""
    kind = _kind(doc, meta)
    tier = _KIND_TIER.get(kind, TIER_SECONDARY)
    auth_level = ((meta or {}).get("authority_level") or "").strip().lower()
    rights_class = ((meta or {}).get("rights_class") or "").strip().lower()
    if kind not in _PRIMARY_KINDS:
        # A secondary/tertiary source or secondary rights class never earns a
        # primary tier — a commentary article cannot outrank a statute.
        if auth_level in ("secondary", "tertiary") or rights_class == "secondary":
            tier = TIER_SECONDARY
    return tier


def authority_level(doc, meta=None) -> str:
    """Human-readable authority label for a result."""
    return TIER_LABELS[authority_tier(doc, meta)]


def authority_score(doc, meta=None) -> float:
    """Authority component in [0, 1]."""
    return AUTHORITY_SCORES[authority_tier(doc, meta)]


def annotate(doc, meta=None) -> dict:
    """Authority fields to merge into a result dict."""
    tier = authority_tier(doc, meta)
    return {
        "authority_tier": tier,
        "authority_level": TIER_LABELS[tier],
        "authority_score": AUTHORITY_SCORES[tier],
    }


def blend_score(relevance, authority, w_relevance: float = W_RELEVANCE,
                w_authority: float = W_AUTHORITY) -> float:
    """Blend a relevance and an authority component into a final score."""
    return clamp01(w_relevance * clamp01(relevance)
                   + w_authority * clamp01(authority))


def similarity_component(result: dict) -> float:
    """Similarity in [0, 1] from dense cosine when present, else BM25 rank."""
    dense = result.get("dense_sim")
    if dense is not None:
        return clamp01(dense)
    rank = result.get("bm25_rank")
    if rank:
        return clamp01(1.0 / (1.0 + float(rank)))
    return 0.0


def relevance_score(result: dict, max_rrf=None) -> float:
    """Relevance in [0, 1] from the fused RRF score (dense/BM25 assisted)."""
    rrf = float(result.get("rrf_score") or 0.0)
    if max_rrf is None:
        max_rrf = rrf
    rrf_norm = (rrf / max_rrf) if max_rrf > 0 else 0.0
    dense = result.get("dense_sim")
    if dense is not None:
        secondary = clamp01(dense)
    else:
        rank = result.get("bm25_rank")
        secondary = clamp01(1.0 / (1.0 + float(rank))) if rank else 0.0
    return clamp01(RELEVANCE_RRF_WEIGHT * rrf_norm
                   + RELEVANCE_DENSE_WEIGHT * secondary)


def confidence(similarity, authority, stage,
               weights=CONFIDENCE_WEIGHTS) -> float:
    """Per-result confidence in [0, 1] from similarity, authority and stage."""
    w_sim, w_auth, w_stage = weights
    return clamp01(w_sim * clamp01(similarity)
                   + w_auth * clamp01(authority)
                   + w_stage * clamp01(stage))


def rank_results(results, w_relevance: float = W_RELEVANCE,
                 w_authority: float = W_AUTHORITY) -> list:
    """Add ``relevance``/``score``/``confidence`` and sort by final score.

    Results must already carry ``authority_score`` (from :func:`annotate`),
    ``rrf_score``, ``dense_sim``/``bm25_rank`` and ``match_strategy``.
    """
    results = [dict(r) for r in results]
    if not results:
        return []
    max_rrf = max(float(r.get("rrf_score") or 0.0) for r in results)
    for r in results:
        authority = float(r.get("authority_score") or 0.0)
        r["relevance"] = relevance_score(r, max_rrf)
        r["score"] = blend_score(r["relevance"], authority,
                                 w_relevance, w_authority)
        r["confidence"] = confidence(
            similarity_component(r), authority,
            STAGE_CONFIDENCE.get(r.get("match_strategy"), 0.5))
    results.sort(key=lambda r: (-r["score"],
                                -float(r.get("rrf_score") or 0.0),
                                int(r.get("doc_id") or 0)))
    return results
