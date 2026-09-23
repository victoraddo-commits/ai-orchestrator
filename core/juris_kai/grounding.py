"""Progressive retrieval + grounding verdict for Juris Kai.

Retrieval supplies the verdict and citations; the model may only cite what
retrieval returned. No legal substance without a source (owner directive).

Query normalization lives on the legal-brain (CT100) side, so this client
drives staged retrieval by passing a ``mode`` (phrase/and/or/like) — it never
builds FTS operators itself.
"""
from __future__ import annotations
import logging

from core.juris_kai.legal_context import MAX_CHUNK_LENGTH

logger = logging.getLogger("juris_kai.grounding")

MIN_SOURCE_CHARS = 400
# Rights-limited tiers (reference/search_only) store only a relevance-centered
# snippet, capped by the legal-brain's rights compliance. Requiring the full
# MIN_SOURCE_CHARS there would make them ungroundable (e.g. the Constitution),
# so they get a snippet floor that still rejects trivial stubs.
MIN_SNIPPET_CHARS = 120


def _search(query: str, limit: int = 3, mode: str = "or") -> list[dict]:
    """Pure transport seam over the legal-brain client (monkeypatched in tests)."""
    from core import legal_brain_client as lb
    return lb.search(query, limit=limit, mode=mode) or []


def _hydrate(hit: dict) -> dict:
    """Return ``hit`` with a content-bearing ``chunk_content``, capped.

    CT100's ``/search`` returns a relevance-centered bounded ``snippet``. That
    snippet is the best available text for ``reference``/``search_only``
    records — their stored ``content`` is an arbitrary head truncation (or
    empty) and would be worse than the snippet — so only ``full``-tier records
    are hydrated from ``/document/{id}``. Every result is capped at
    ``legal_context.MAX_CHUNK_LENGTH`` so prompts stay bounded.
    """
    content = (hit.get("chunk_content") or hit.get("snippet")
               or hit.get("content") or "")
    if hit.get("store_mode") == "full" and len(content.strip()) < MIN_SOURCE_CHARS:
        doc_id = hit.get("id")
        if doc_id is not None:
            try:
                from core import legal_brain_client as lb
                full = (lb.get_document(doc_id) or {}).get("content") or ""
                if len(full.strip()) > len(content.strip()):
                    content = full
            except Exception as exc:  # noqa: BLE001 - retrieval must never crash
                logger.warning(
                    "grounding: full-document fetch failed for id=%s: %s",
                    doc_id, exc)
    return {**hit, "chunk_content": content[:MAX_CHUNK_LENGTH]}


def _stage(query: str, limit: int, mode: str) -> list[dict]:
    """Run one retrieval stage: transport, hydrate, then keep usable docs."""
    return _usable([_hydrate(h) for h in _search(query, limit, mode=mode)])


def _usable(docs: list[dict]) -> list[dict]:
    """Keep docs whose text is substantial enough to ground an answer.

    The floor is tier-aware: ``full`` docs must clear ``MIN_SOURCE_CHARS``,
    while rights-limited ``reference``/``search_only`` docs — which by design
    hold only a relevance-centered snippet — clear ``MIN_SNIPPET_CHARS``. An
    unknown/blank tier is treated conservatively as needing ``MIN_SOURCE_CHARS``.
    """
    usable = []
    for d in docs:
        length = len((d.get("chunk_content") or "").strip())
        if d.get("store_mode") == "full":
            threshold = MIN_SOURCE_CHARS
        elif d.get("store_mode") in ("reference", "search_only"):
            threshold = MIN_SNIPPET_CHARS
        else:
            threshold = MIN_SOURCE_CHARS
        if length >= threshold:
            usable.append(d)
    return usable


def retrieve(query: str, limit: int = 3) -> dict:
    """Progressive retrieval. Returns {docs, verdict, stage}."""
    q = (query or "").strip()
    if not q:
        return {"docs": [], "verdict": "UNGROUNDED", "stage": 0}

    # Stage 1: exact phrase (server builds the FTS phrase query)
    docs = _stage(q, limit, "phrase")
    if docs:
        return {"docs": docs, "verdict": "GROUNDED", "stage": 1}

    # Stage 2: AND of tokens
    docs = _stage(q, limit, "and")
    if docs:
        return {"docs": docs, "verdict": "GROUNDED", "stage": 2}

    # Stage 3: OR of tokens
    docs = _stage(q, limit, "or")
    if docs:
        return {"docs": docs, "verdict": "PARTIAL", "stage": 3}

    # Stage 4: raw keyword fallback (title/citation LIKE)
    docs = _stage(q, limit, "like")
    if docs:
        return {"docs": docs, "verdict": "PARTIAL", "stage": 4}

    return {"docs": [], "verdict": "UNGROUNDED", "stage": 0}


def build_sources_footer(docs: list[dict]) -> str:
    """Deterministic Sources block built from retrieval (never the model)."""
    if not docs:
        return ""
    lines = ["\n\n📚 *Sources*"]
    for i, d in enumerate(docs, 1):
        title = (d.get("title") or "Untitled").strip()
        cite = (d.get("citation") or "").strip()
        year = d.get("year")
        mode = (d.get("store_mode") or "").strip()
        bits = [title]
        if cite and cite != title:
            bits.append(cite)
        if year:
            bits.append(str(year))
        if mode:
            bits.append(f"_{mode}_")
        lines.append(f"{i}. " + " — ".join(bits))
    return "\n".join(lines)
