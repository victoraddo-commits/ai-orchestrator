"""KAI Bet — AI prediction audit record (§43).

Persists every AI-assisted prediction into `ai_prediction_records` so decisions
are auditable: model probabilities, agreement, prompt version, decision, and
local-inference token/cost metadata. Never raises into the caller.
"""
from __future__ import annotations

from typing import Any, Optional


def _tier_prob(result: Any, tier: str) -> Optional[float]:
    d = getattr(result, tier, None) or {}
    try:
        v = d.get("probability")
        return float(v) if v is not None else None
    except (TypeError, ValueError, AttributeError):
        return None


def record_ai_prediction(conn, *, prediction: Any, result: Any) -> Optional[str]:
    """Insert/replace an audit record. Returns the prediction id or None."""
    if conn is None or result is None:
        return None
    pid = getattr(result, "prediction_id", "") or ""
    if not pid:
        return None
    row = {
        "prediction_id": pid,
        "event_id": str(getattr(prediction, "event_id", "") or ""),
        "sport": getattr(prediction, "sport_key", "") or "",
        "competition": getattr(prediction, "league_key", "") or "",
        "market": getattr(prediction, "market_type", "") or "",
        "selection": getattr(prediction, "selection", "") or "",
        "odds": getattr(prediction, "bookmaker_odds", None),
        "kai_probability": getattr(prediction, "estimated_probability", None),
        "qwen_probability": _tier_prob(result, "qwen"),
        "deepseek_probability": _tier_prob(result, "deepseek"),
        "k3_probability": _tier_prob(result, "k3"),
        "final_probability": getattr(result, "final_probability", None),
        "market_probability": getattr(prediction, "implied_probability", None),
        "estimated_edge": getattr(prediction, "edge", None),
        "confidence": getattr(prediction, "confidence", None),
        "risk_score": getattr(prediction, "risk_score", None),
        "model_agreement": getattr(result, "status", "") or "",
        "prompt_version": getattr(result, "prompt_version", "") or "",
        "model_versions": ",".join(getattr(result, "tiers_run", []) or []),
        "decision": getattr(result, "final_decision", "") or "",
        "gpuai_input_tokens": int(getattr(result, "total_input_tokens", 0) or 0),
        "gpuai_output_tokens": int(getattr(result, "total_output_tokens", 0) or 0),
        "gpuai_estimated_cost": float(getattr(result, "total_cost", 0.0) or 0.0),
        "status": "complete",
    }
    cols = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    conn.execute(
        f"INSERT OR REPLACE INTO ai_prediction_records ({cols}) VALUES ({placeholders})",
        tuple(row.values()),
    )
    return pid
