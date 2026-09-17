"""KAI Bet — decision API: value + risk + no-bet gate for one candidate.

Advisory/evaluation endpoint. Deterministic KAI maths only.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.kai_betting.value_engine import compute_value
from core.kai_betting.risk_engine import assess_risk, no_bet_gate
from core.kai_betting.real_money import evaluate as real_money_evaluate

decision_router = APIRouter(tags=["Kai Betting — Decision"])


class GateRequest(BaseModel):
    odds: float
    model_probability: float
    uncertainty: float = 0.0
    data_quality: float = 1.0
    stale_odds: bool = False
    lineup_confirmed: bool = True
    correlation_ok: bool = True
    bankroll_ok: bool = True
    odds_movement: float = 0.0
    liquidity: float = 1.0
    correlation_load: float = 0.0
    event_flags: Optional[List[str]] = None
    bankroll_exposure: float = 0.0


@decision_router.post("/gate")
def api_gate(body: GateRequest):
    try:
        value = compute_value(body.model_probability, body.odds, uncertainty=body.uncertainty)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    risk = assess_risk(
        model_uncertainty=body.uncertainty,
        data_quality=body.data_quality,
        odds_movement=body.odds_movement,
        liquidity=body.liquidity,
        correlation_load=body.correlation_load,
        event_flags=body.event_flags,
        bankroll_exposure=body.bankroll_exposure,
    )
    gate = no_bet_gate(
        value, risk,
        data_quality=body.data_quality,
        stale_odds=body.stale_odds,
        lineup_confirmed=body.lineup_confirmed,
        correlation_ok=body.correlation_ok,
        bankroll_ok=body.bankroll_ok,
    )
    return {
        "decision": gate.decision,
        "reasons": gate.reasons,
        "value": {
            "value": value.value, "edge": value.edge, "edge_adjusted": value.edge_adjusted,
            "ev": value.ev, "fair_odds": value.fair_odds, "kelly_fraction": value.kelly_fraction,
            "implied_probability": value.implied_probability,
        },
        "risk": {"total": risk.total, "level": risk.level, "components": risk.components},
    }


@decision_router.get("/real-money")
def api_real_money():
    """§33: real-money execution status. DISABLED by default (never auto-enables)."""
    s = real_money_evaluate({})
    return {"enabled": s.enabled, "ready": s.ready, "checks": s.checks, "note": s.note}


@decision_router.get("/markets")
def api_markets():
    """Market taxonomy learned from SportyBet (§6/§9). Honest about coverage."""
    from core.kai_betting.markets import FAMILIES, modelled_keys
    return {
        "modelled": modelled_keys(),
        "families": [
            {"key": f.key, "name": f.name, "group": f.group, "level": f.level,
             "modelled": f.modelled, "sportybet_ids": f.sportybet_ids, "requires": f.requires}
            for f in FAMILIES.values()
        ],
    }


@decision_router.get("/markets/catalog")
def api_market_catalog(group: str = None, limit: int = 300):
    """The actual SportyBet market catalogue imported for the reference fixture."""
    from core.kai_betting.db import get_db
    q = "SELECT sb_id,name,group_name,specifier,guide FROM market_catalog"
    args = []
    if group:
        q += " WHERE lower(group_name)=lower(?)"
        args.append(group)
    q += " ORDER BY group_name, name LIMIT ?"
    args.append(limit)
    with get_db() as conn:
        rows = [dict(r) for r in conn.execute(q, args).fetchall()]
        total = conn.execute("SELECT COUNT(*) FROM market_catalog").fetchone()[0]
    return {"total": total, "returned": len(rows), "markets": rows}
