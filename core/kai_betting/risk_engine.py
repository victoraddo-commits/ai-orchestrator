"""KAI Bet — risk engine (§26) and no-bet gate (§45).

Pure, deterministic. KAI must prefer being wrong less often over producing more
picks (§55.30); "NO BET" is a valid, successful outcome (§49).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.kai_betting.value_engine import ValueResult

# Thresholds (env-overridable)
RISK_LOW_MAX = float(os.environ.get("KAI_BET_RISK_LOW_MAX", "40"))
RISK_MED_MAX = float(os.environ.get("KAI_BET_RISK_MED_MAX", "70"))
MIN_DATA_QUALITY = float(os.environ.get("KAI_BET_MIN_DATA_QUALITY", "0.6"))

# Component weights (sum = 1.0)
_WEIGHTS = {
    "model": 0.28,
    "data": 0.20,
    "market": 0.12,
    "execution": 0.10,
    "correlation": 0.15,
    "event": 0.08,
    "bankroll": 0.07,
}

EVENT_FLAG_WEIGHT = 25.0  # each active event flag adds this (capped at 100)


@dataclass
class RiskResult:
    components: Dict[str, float]
    total: float
    level: str  # low | medium | high


@dataclass
class GateResult:
    decision: str  # BET | WATCH | NO BET
    reasons: List[str] = field(default_factory=list)
    risk_level: str = ""
    value: str = ""


def _clamp(x: float) -> float:
    return max(0.0, min(100.0, float(x)))


def assess_risk(
    *,
    model_uncertainty: float = 0.0,
    data_quality: float = 1.0,
    odds_movement: float = 0.0,
    liquidity: float = 1.0,
    correlation_load: float = 0.0,
    event_flags: Optional[List[str]] = None,
    bankroll_exposure: float = 0.0,
) -> RiskResult:
    """Compute weighted risk (0..100) from normalized 0..1 inputs.

    Args:
        model_uncertainty: 0..1 (from the value engine / model disagreement).
        data_quality: 0..1 (1 = clean, fresh, complete).
        odds_movement: absolute fractional move since open (0.1 = 10%).
        liquidity: 0..1 (1 = deep market).
        correlation_load: 0..1 (how correlated this is with open exposure).
        event_flags: active flags, e.g. ["lineup_unconfirmed", "weather"].
        bankroll_exposure: 0..1 (fraction of bankroll already at risk).
    """
    comps = {
        "model": _clamp(model_uncertainty * 100.0),
        "data": _clamp((1.0 - max(0.0, min(1.0, data_quality))) * 100.0),
        "market": _clamp(abs(odds_movement) * 100.0),
        "execution": _clamp((1.0 - max(0.0, min(1.0, liquidity))) * 100.0),
        "correlation": _clamp(max(0.0, min(1.0, correlation_load)) * 100.0),
        "event": _clamp(len(event_flags or []) * EVENT_FLAG_WEIGHT),
        "bankroll": _clamp(max(0.0, min(1.0, bankroll_exposure)) * 100.0),
    }
    total = round(sum(comps[k] * _WEIGHTS[k] for k in _WEIGHTS), 4)
    if total < RISK_LOW_MAX:
        level = "low"
    elif total < RISK_MED_MAX:
        level = "medium"
    else:
        level = "high"
    return RiskResult(components=comps, total=total, level=level)


def no_bet_gate(
    value: ValueResult,
    risk: RiskResult,
    *,
    data_quality: float = 1.0,
    stale_odds: bool = False,
    lineup_confirmed: bool = True,
    correlation_ok: bool = True,
    bankroll_ok: bool = True,
) -> GateResult:
    """Final BET / WATCH / NO BET decision. Deny-by-default."""
    reasons: List[str] = []

    # Hard NO BET gates (any one blocks).
    if data_quality < MIN_DATA_QUALITY:
        reasons.append(f"data quality {data_quality:.2f} below {MIN_DATA_QUALITY}")
        return GateResult("NO BET", reasons, risk.level, value.value)
    if stale_odds:
        reasons.append("stale odds")
        return GateResult("NO BET", reasons, risk.level, value.value)
    if not lineup_confirmed:
        reasons.append("lineup not confirmed (can invalidate the prediction)")
        return GateResult("NO BET", reasons, risk.level, value.value)
    if value.value == "none" or value.ev <= 0:
        reasons.append(f"no value (value={value.value}, ev={value.ev})")
        return GateResult("NO BET", reasons, risk.level, value.value)
    if not correlation_ok:
        reasons.append("correlation limit exceeded")
        return GateResult("NO BET", reasons, risk.level, value.value)
    if not bankroll_ok:
        reasons.append("bankroll/stake limit exceeded")
        return GateResult("NO BET", reasons, risk.level, value.value)
    if risk.level == "high":
        reasons.append(f"risk high ({risk.total})")
        return GateResult("NO BET", reasons, risk.level, value.value)

    # BET only on real value with acceptable risk.
    if value.value in ("moderate", "high") and risk.level in ("low", "medium"):
        reasons.append(f"{value.value} value (edge {value.edge_adjusted}) with {risk.level} risk")
        return GateResult("BET", reasons, risk.level, value.value)

    # Everything else: watch.
    reasons.append(f"low value ({value.value}) or elevated risk ({risk.level}) — no action")
    return GateResult("WATCH", reasons, risk.level, value.value)
