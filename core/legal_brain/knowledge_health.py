"""Legal Brain — Knowledge Health Dashboard backend (KAI 2.0 phase 18E).

Rolls up three integrity/quality signals from the corpus index + source
registry so operators can see at a glance whether the knowledge base is
trustworthy right now:

  * ``integrity``       — checksum verification counts
  * ``freshness``       — how stale the newest / oldest doc is
  * ``citation_graph``  — % of documents that carry outbound citations

Reads ``memory/legal_corpus_index.json`` and ``memory/legal_sources.json``.
Missing data → the corresponding block returns zeros with an
``available: False`` flag so the dashboard renders a "no data yet" state
without exceptions.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.memory import load

_CORPUS = "legal_corpus_index.json"
_SOURCES = "legal_sources.json"
_STALE_DAYS_DEFAULT = 90


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load_docs() -> list[dict[str, Any]]:
    data = load(_CORPUS)
    if isinstance(data, dict):
        for k in ("records", "documents", "items"):
            v = data.get(k)
            if isinstance(v, list):
                return v
    if isinstance(data, list):
        return data
    return []


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        # Accept trailing 'Z' for UTC.
        v = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _integrity(docs: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(docs)
    verified = 0
    failures = 0
    missing = 0
    for d in docs:
        status = (d.get("checksum_status") or "").lower()
        if status == "ok":
            verified += 1
        elif status == "fail":
            failures += 1
        else:
            # Fall back to hash presence + `checksum_expected` match.
            expected = d.get("checksum_expected")
            actual = d.get("checksum") or d.get("sha256")
            if expected and actual:
                if expected == actual:
                    verified += 1
                else:
                    failures += 1
            else:
                missing += 1
    return {
        "available": total > 0,
        "total_docs": total,
        "docs_with_valid_checksum": verified,
        "checksum_failures": failures,
        "checksum_missing": missing,
    }


def _freshness(docs: list[dict[str, Any]], stale_days: int) -> dict[str, Any]:
    now = _now()
    freshest: datetime | None = None
    oldest: datetime | None = None
    stale_count = 0
    for d in docs:
        ts = _parse_ts(d.get("indexed_at") or d.get("updated_at") or d.get("published_at"))
        if ts is None:
            continue
        if freshest is None or ts > freshest:
            freshest = ts
        if oldest is None or ts < oldest:
            oldest = ts
        if (now - ts).days > stale_days:
            stale_count += 1
    return {
        "available": bool(docs),
        "stale_days_default": stale_days,
        "stale_count": stale_count,
        "freshest_at": freshest.isoformat() if freshest else None,
        "oldest_at": oldest.isoformat() if oldest else None,
    }


def _citation_graph(docs: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(docs)
    with_cites = 0
    for d in docs:
        cites = d.get("citations") or d.get("outbound_citations")
        if isinstance(cites, list) and len(cites) > 0:
            with_cites += 1
    missing = total - with_cites
    coverage = round((with_cites / total) * 100, 1) if total else 0.0
    return {
        "available": total > 0,
        "docs_with_citations": with_cites,
        "docs_missing_citations": missing,
        "coverage_pct": coverage,
    }


def get_health(stale_days: int = _STALE_DAYS_DEFAULT) -> dict[str, Any]:
    """Return the full knowledge-health snapshot."""
    docs = _load_docs()
    sources = load(_SOURCES) or {}
    source_records = sources.get("records") if isinstance(sources, dict) else []
    active_sources = [
        r for r in (source_records or [])
        if isinstance(r, dict) and r.get("active", True)
    ]
    return {
        "generated_at": _now().isoformat(),
        "integrity": _integrity(docs),
        "freshness": _freshness(docs, stale_days),
        "citation_graph": _citation_graph(docs),
        "sources": {
            "active_count": len(active_sources),
            "total_count": len(source_records or []),
        },
    }
