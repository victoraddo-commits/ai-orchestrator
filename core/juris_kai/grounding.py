"""Progressive retrieval + grounding verdict for Juris Kai.

Retrieval supplies the verdict and citations; the model may only cite what
retrieval returned. No legal substance without a source (owner directive).

Query normalization lives on the legal-brain (CT100) side, so this client
drives staged retrieval by passing a ``mode`` (phrase/and/or/like) — it never
builds FTS operators itself.
"""
from __future__ import annotations
import logging

logger = logging.getLogger("juris_kai.grounding")

MIN_SOURCE_CHARS = 400


def _search(query: str, limit: int = 3, mode: str = "or") -> list[dict]:
    """Thin seam over the legal-brain client (monkeypatched in tests).

    CT100's ``/search`` returns a bounded ``snippet`` (a few hundred chars),
    not the full ``chunk_content`` downstream grounding needs. Each hit is
    therefore normalized and, when its snippet is shorter than
    ``MIN_SOURCE_CHARS``, hydrated from ``/document/{id}``: full-tier records
    yield their full text, while rights-tier records only ever store a
    bounded snippet, which is kept as-is.
    """
    from core import legal_brain_client as lb
    hits = lb.search(query, limit=limit, mode=mode) or []
    return [_normalize(lb, h) for h in hits]


def _normalize(lb, hit: dict) -> dict:
    content = (hit.get("chunk_content") or hit.get("snippet")
               or hit.get("content") or "")
    if len(content.strip()) < MIN_SOURCE_CHARS and hit.get("id") is not None:
        try:
            doc = lb.get_document(hit["id"]) or {}
            full = doc.get("content") or ""
            if len(full.strip()) > len(content.strip()):
                content = full
        except Exception as exc:  # noqa: BLE001 - retrieval must never crash
            logger.warning(
                "grounding: full-document fetch failed for id=%s: %s",
                hit.get("id"), exc)
    return {**hit, "chunk_content": content}


def _usable(docs: list[dict]) -> list[dict]:
    return [d for d in docs
            if len((d.get("chunk_content") or d.get("content") or "").strip()) >= MIN_SOURCE_CHARS]


def retrieve(query: str, limit: int = 3) -> dict:
    """Progressive retrieval. Returns {docs, verdict, stage}."""
    q = (query or "").strip()
    if not q:
        return {"docs": [], "verdict": "UNGROUNDED", "stage": 0}

    # Stage 1: exact phrase (server builds the FTS phrase query)
    docs = _usable(_search(q, limit, mode="phrase"))
    if docs:
        return {"docs": docs, "verdict": "GROUNDED", "stage": 1}

    # Stage 2: AND of tokens
    docs = _usable(_search(q, limit, mode="and"))
    if docs:
        return {"docs": docs, "verdict": "GROUNDED", "stage": 2}

    # Stage 3: OR of tokens
    docs = _usable(_search(q, limit, mode="or"))
    if docs:
        return {"docs": docs, "verdict": "PARTIAL", "stage": 3}

    # Stage 4: raw keyword fallback (title/citation LIKE)
    docs = _usable(_search(q, limit, mode="like"))
    if docs:
        return {"docs": docs, "verdict": "PARTIAL", "stage": 4}

    return {"docs": [], "verdict": "UNGROUNDED", "stage": 0}
