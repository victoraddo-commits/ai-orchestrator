"""KAI Bet — calibration & value gate (§13/§19).

Corrects two known biases before any value is claimed:
  * favourite–longshot bias: low-probability selections must show a much larger
    edge before being called value;
  * model over-confidence at probability tails: extreme model probabilities are
    shrunk toward the market, where the model is least reliable.

Pure, deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

BASE_MARGIN = 0.02          # minimum edge at fair odds
LONGSHOT_K = 0.025          # extra required edge per unit of odds above 2.0
TAIL_LO = 0.15              # below this, shrink toward market
TAIL_HI = 0.85              # above this, shrink toward market
TAIL_WEIGHT = 0.35          # how much to shrink at the tails


def longshot_margin(odds: float, base: float = BASE_MARGIN, k: float = LONGSHOT_K) -> float:
    """Required edge grows with price (a 10.0 shot must clear a bigger bar)."""
    return round(base + k * max(0.0, float(odds) - 2.0), 6)


def shrink_toward_market(model_p: float, market_p: float,
                         weight: float = TAIL_WEIGHT) -> float:
    """Pull extreme model probabilities toward the market (tails are unreliable)."""
    if market_p is None:
        return model_p
    if model_p < TAIL_LO or model_p > TAIL_HI:
        return round((1 - weight) * model_p + weight * market_p, 6)
    return model_p


@dataclass
class CalibratedValue:
    model_probability: float
    calibrated_probability: float
    market_probability: float
    edge: float
    required_edge: float
    value: str  # none|low|moderate|high


def calibrated_value(model_p: float, odds: float, market_p: Optional[float] = None,
                     uncertainty: float = 0.0, min_edge: Optional[float] = None) -> CalibratedValue:
    """Value classification with a longshot margin + tail shrinkage applied."""
    if odds is None or odds <= 1.0:
        raise ValueError("odds must be > 1.0")
    ip = 1.0 / odds
    mp = ip if market_p is None else float(market_p)
    p = shrink_toward_market(float(model_p), mp)
    edge = p - ip
    required = float(min_edge) if min_edge is not None else longshot_margin(odds)
    required = required * (1.0 + max(0.0, min(1.0, uncertainty)))  # uncertainty raises the bar
    if edge >= required * 2.5:
        value = "high"
    elif edge >= required * 1.5:
        value = "moderate"
    elif edge >= required:
        value = "low"
    else:
        value = "none"
    return CalibratedValue(round(model_p, 6), round(p, 6), round(mp, 6),
                           round(edge, 6), round(required, 6), value)
