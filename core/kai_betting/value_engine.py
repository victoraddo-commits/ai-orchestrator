"""KAI Bet — value engine (§13) and price sensitivity (§14).

Pure, deterministic, no I/O. KAI's own maths — no external intelligence.
Value = model probability vs the market price, adjusted for uncertainty.
"""
from __future__ import annotations

from dataclasses import dataclass

# Edge thresholds (probability points) for value classification.
VALUE_LOW = 0.02
VALUE_MODERATE = 0.05
VALUE_HIGH = 0.10
DEFAULT_KELLY_CAP = 0.25


@dataclass
class ValueResult:
    model_probability: float
    implied_probability: float
    edge: float                 # model_prob - implied_prob
    edge_adjusted: float        # edge haircut by uncertainty
    ev: float                   # expected value per 1 unit staked
    fair_odds: float
    value: str                  # none|low|moderate|high
    kelly_fraction: float       # capped, uncertainty-adjusted
    price_sensitivity: float    # dEV/d(odds) at the current price


def implied_probability(odds: float | None) -> float | None:
    if odds is None or odds <= 1.0:
        return None
    return 1.0 / odds


def ev_per_unit(model_probability: float, odds: float) -> float:
    """Expected profit per 1 unit staked (decimal odds)."""
    return model_probability * (odds - 1.0) - (1.0 - model_probability)


def kelly_fraction(model_probability: float, odds: float) -> float:
    b = odds - 1.0
    if b <= 0:
        return 0.0
    f = (model_probability * b - (1.0 - model_probability)) / b
    return max(0.0, f)


def compute_value(
    model_probability: float,
    odds: float,
    uncertainty: float = 0.0,
    kelly_cap: float = DEFAULT_KELLY_CAP,
) -> ValueResult:
    """Classify value and size the stake for a single selection.

    Args:
        model_probability: KAI's estimated probability (0..1).
        odds: decimal bookmaker price (> 1.0).
        uncertainty: 0..1 (0 = fully certain). Haircuts edge + Kelly.
        kelly_cap: maximum fraction of bankroll Kelly may suggest.
    """
    if not (0.0 < model_probability < 1.0):
        raise ValueError("model_probability must be in (0,1)")
    if odds is None or odds <= 1.0:
        raise ValueError("odds must be > 1.0")

    ip = 1.0 / odds
    edge = model_probability - ip
    u = min(max(float(uncertainty or 0.0), 0.0), 1.0)
    edge_adj = edge * (1.0 - u)
    ev = ev_per_unit(model_probability, odds)

    if ev <= 0 or edge_adj < VALUE_LOW:
        value = "none"
    elif edge_adj < VALUE_MODERATE:
        value = "low"
    elif edge_adj < VALUE_HIGH:
        value = "moderate"
    else:
        value = "high"

    kelly = min(kelly_fraction(model_probability, odds) * (1.0 - u), kelly_cap)

    # Price sensitivity: for a fixed model probability, d(EV)/d(odds) = model_probability.
    # Reported per 1.00 move in decimal odds.
    sensitivity = model_probability

    return ValueResult(
        model_probability=float(model_probability),
        implied_probability=round(ip, 6),
        edge=round(edge, 6),
        edge_adjusted=round(edge_adj, 6),
        ev=round(ev, 6),
        fair_odds=round(1.0 / model_probability, 4),
        value=value,
        kelly_fraction=round(kelly, 6),
        price_sensitivity=round(sensitivity, 6),
    )
