"""Legal Brain — Corpus Coverage Dashboard backend (KAI 2.0 phase 18E).

Returns a 16-tier snapshot of the legal corpus: how many documents each
tier is expected to hold (target), how many are currently indexed (actual),
and where the gaps are. Reads ``memory/legal_corpus_index.json`` if
present; otherwise returns an empty-state shape so the dashboard has
something to render before the ingestion pipeline runs.

The index JSON, when it exists, is expected to be a list of documents each
tagged with a ``tier`` field (1..16). Non-integer / out-of-range tiers land
in ``unclassified`` and are surfaced separately.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.memory import load

_STORE = "legal_corpus_index.json"

# 16-tier legal knowledge hierarchy — canonical order + target coverage.
# Targets are placeholder values (the phase description does not fix them);
# adjust in one place if the ingestion team gives us real numbers.
_TIERS: list[tuple[int, str, int]] = [
    (1,  "Constitution",                       1),
    (2,  "International Treaties",           200),
    (3,  "Primary Legislation (Acts)",      1500),
    (4,  "Subsidiary Legislation (LI/CI)",  3000),
    (5,  "Bills & Legislative History",      500),
    (6,  "Supreme Court Judgments",         3000),
    (7,  "Court of Appeal Judgments",       6000),
    (8,  "High Court Judgments",           10000),
    (9,  "Specialised Tribunal Rulings",    2500),
    (10, "Practice Directions",              300),
    (11, "Legal Textbooks & Treatises",      400),
    (12, "Law Journals & Articles",         2500),
    (13, "Restatements & Model Codes",       200),
    (14, "Bar Council & Ethics Rulings",     150),
    (15, "Government Gazettes & Notices",   5000),
    (16, "Contract & Form Precedents",      1000),
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_index() -> list[dict[str, Any]]:
    data = load(_STORE)
    if isinstance(data, dict):
        for k in ("records", "documents", "items"):
            v = data.get(k)
            if isinstance(v, list):
                return v
    if isinstance(data, list):
        return data
    return []


def _classify(docs: list[dict[str, Any]]) -> tuple[dict[int, int], int]:
    by_tier: dict[int, int] = {n: 0 for n, _, _ in _TIERS}
    unclassified = 0
    for d in docs:
        t = d.get("tier")
        if isinstance(t, int) and 1 <= t <= 16:
            by_tier[t] = by_tier.get(t, 0) + 1
        else:
            unclassified += 1
    return by_tier, unclassified


def get_coverage() -> dict[str, Any]:
    """Return coverage snapshot: per-tier target/actual/gap + rollups + alerts."""
    docs = _load_index()
    by_tier, unclassified = _classify(docs)

    tiers: list[dict[str, Any]] = []
    total_target = 0
    total_actual = 0
    empty_tiers: list[int] = []
    for number, name, target in _TIERS:
        actual = int(by_tier.get(number, 0))
        gap = max(0, target - actual)
        total_target += target
        total_actual += actual
        if actual == 0:
            empty_tiers.append(number)
        tiers.append({
            "tier": number,
            "name": name,
            "target": target,
            "actual": actual,
            "gap": gap,
            "coverage_pct": round(min(100.0, (actual / target) * 100), 1) if target else 0.0,
        })

    coverage_pct = round(min(100.0, (total_actual / total_target) * 100), 1) if total_target else 0.0
    return {
        "generated_at": _now_iso(),
        "source": _STORE if docs else "placeholder",
        "tiers": tiers,
        "totals": {
            "target": total_target,
            "actual": total_actual,
            "gap": max(0, total_target - total_actual),
            "coverage_pct": coverage_pct,
            "unclassified": unclassified,
        },
        "alerts": {
            "empty_tiers": empty_tiers,
            "empty_tier_count": len(empty_tiers),
        },
    }
