"""KAI Bet — Second Brain integration (§28).

Persists learned lessons (gate decisions, settled paper bets, calibration/drift
findings) into KAI's Second Brain so future decisions can consult them.

Bounded by design: one entity per (kind, market), updated in place under the
`newest_wins` policy, so the store does not grow without limit.
Best-effort: never raises into the caller.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

STORE = "project"
_MEMORY_TYPE = "business"


def lesson_entity(kind: str, market: str = "") -> str:
    m = (market or "general").strip().lower().replace(" ", "_")[:32]
    return f"kai_bet:{kind}:{m}"


def format_lesson(kind: str, subject: Dict[str, Any], outcome: Dict[str, Any]) -> Dict[str, Any]:
    """Pure: build the second-brain fact payload for a lesson."""
    return {
        "kind": kind,
        "market": subject.get("market"),
        "selection": subject.get("selection"),
        "odds": subject.get("odds"),
        "model_probability": subject.get("model_probability"),
        "value": subject.get("value"),
        "risk_level": subject.get("risk_level"),
        "decision": outcome.get("decision"),
        "result": outcome.get("result"),
        "profit_loss": outcome.get("profit_loss"),
        "notes": outcome.get("notes"),
        "source": "kai_bet",
    }


def remember_lesson(kind: str, subject: Dict[str, Any], outcome: Dict[str, Any],
                    entity_type: str = "kai_bet_lesson") -> Optional[str]:
    """Write/replace a lesson in the Second Brain. Returns record id or None."""
    try:
        from core.second_brain.writer import SecondBrainWriter
        from core.second_brain.types import MemoryType

        fact = format_lesson(kind, subject, outcome)
        entity = lesson_entity(kind, str(subject.get("market") or ""))
        w = SecondBrainWriter()
        return w.update(
            STORE,
            entity=entity,
            memory_type=MemoryType.BUSINESS,
            fact=fact,
            changed_reason=f"kai_bet:{kind}",
            entity_type=entity_type,
        )
    except Exception:  # noqa: BLE001 - memory is advisory, never break betting
        return None


def recent_lessons(limit: int = 25) -> List[Dict[str, Any]]:
    """Read back recent KAI Bet lessons from the Second Brain."""
    try:
        from core.second_brain.router import SecondBrainRouter
        r = SecondBrainRouter()
        res = r.query({"limit": limit})
        items = res.get("results", res) if isinstance(res, dict) else res
        out = []
        for it in (items or []):
            ent = it.get("entity", "") if isinstance(it, dict) else getattr(it, "entity", "")
            if isinstance(ent, str) and ent.startswith("kai_bet:"):
                out.append(it if isinstance(it, dict) else getattr(it, "fact", {}))
        return out
    except Exception:  # noqa: BLE001
        return []
