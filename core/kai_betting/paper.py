"""KAI Bet — paper betting (§17) and audit helpers.

Simulated betting ONLY: no real money ever moves here (real-money execution is
separately gated per §33). Records simulated bets, settles them and reports
ROI / win-rate / average odds. Settlement maths is pure and unit-tested.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.kai_betting.db import get_db
from core.kai_betting.value_engine import compute_value

VALID_OUTCOMES = ("won", "lost", "void")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def settle_profit(stake: float, odds: float, outcome: str) -> float:
    """Profit/loss for a settled paper bet (per unit stake on decimal odds)."""
    if outcome == "won":
        return round(float(stake) * (float(odds) - 1.0), 6)
    if outcome == "lost":
        return round(-float(stake), 6)
    if outcome == "void":
        return 0.0
    raise ValueError(f"unknown outcome {outcome!r}")


def compute_performance(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    settled = [r for r in rows if r.get("status") in ("won", "lost", "void")]
    open_ = [r for r in rows if r.get("status") == "open"]
    staked = sum(float(r["stake"]) for r in settled)
    profit = sum(float(r.get("profit_loss") or 0.0) for r in settled)
    won = [r for r in settled if r["status"] == "won"]
    lost = [r for r in settled if r["status"] == "lost"]
    return {
        "bets": len(rows),
        "open": len(open_),
        "settled": len(settled),
        "won": len(won),
        "lost": len(lost),
        "staked": round(staked, 6),
        "profit_loss": round(profit, 6),
        "roi": round(profit / staked, 6) if staked > 0 else None,
        "win_rate": round(len(won) / len(settled), 6) if settled else None,
        "avg_odds": round(sum(float(r["odds"]) for r in settled) / len(settled), 4) if settled else None,
    }


# ── DB operations ─────────────────────────────────────────────────────────────

def place_bet(conn, *, odds: float, stake: Optional[float], selection: str = "",
              market: str = "", event_id: str = "", prediction_id: str = "",
              sport: str = "", competition: str = "", model_probability: Optional[float] = None,
              bankroll: float = 100.0, notes: str = "") -> Dict[str, Any]:
    if odds is None or float(odds) <= 1.0:
        raise ValueError("odds must be > 1.0")
    implied = 1.0 / float(odds)
    value = None
    kelly = None
    edge = None
    if model_probability is not None:
        vr = compute_value(float(model_probability), float(odds))
        value, kelly, edge = vr.value, vr.kelly_fraction, vr.edge
        if stake is None:
            stake = round(bankroll * kelly, 2)
    if stake is None:
        stake = 0.0
    if stake <= 0:
        raise ValueError("stake must be > 0 (no value or not provided)")
    bid = uuid.uuid4().hex
    conn.execute(
        """INSERT INTO paper_bets
           (id, prediction_id, event_id, sport, competition, market, selection, odds, stake,
            model_probability, implied_probability, edge, value, kelly_fraction, status, placed_at, notes)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'open', ?, ?)""",
        (bid, prediction_id, event_id, sport, competition, market, selection, float(odds), float(stake),
         model_probability, round(implied, 6), edge, value, kelly, now_iso(), notes),
    )
    row = conn.execute("SELECT * FROM paper_bets WHERE id=?", (bid,)).fetchone()
    return dict(row)


def settle_bet(conn, bet_id: str, outcome: str) -> Dict[str, Any]:
    if outcome not in VALID_OUTCOMES:
        raise ValueError(f"outcome must be one of {VALID_OUTCOMES}")
    row = conn.execute("SELECT * FROM paper_bets WHERE id=?", (bet_id,)).fetchone()
    if not row:
        raise KeyError("bet not found")
    if row["status"] != "open":
        raise ValueError(f"bet already {row['status']}")
    profit = settle_profit(row["stake"], row["odds"], outcome)
    conn.execute(
        "UPDATE paper_bets SET status=?, profit_loss=?, settled_at=? WHERE id=?",
        (outcome, profit, now_iso(), bet_id),
    )
    # §28: record the settled outcome as a Second Brain lesson (best-effort).
    try:
        from core.kai_betting.second_brain import remember_lesson
        remember_lesson(
            "settle",
            {"market": row["market"], "selection": row["selection"], "odds": row["odds"],
             "model_probability": row["model_probability"], "value": row["value"]},
            {"decision": "BET", "result": outcome, "profit_loss": profit, "notes": "paper"},
        )
    except Exception:
        pass
    return dict(conn.execute("SELECT * FROM paper_bets WHERE id=?", (bet_id,)).fetchone())


def list_bets(conn, status: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    if status:
        rows = conn.execute("SELECT * FROM paper_bets WHERE status=? ORDER BY placed_at DESC LIMIT ?",
                            (status, limit)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM paper_bets ORDER BY placed_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def performance(conn) -> Dict[str, Any]:
    rows = [dict(r) for r in conn.execute("SELECT * FROM paper_bets").fetchall()]
    return compute_performance(rows)


# ── API ───────────────────────────────────────────────────────────────────────

class PaperBetRequest(BaseModel):
    odds: float
    stake: Optional[float] = None
    selection: str = ""
    market: str = ""
    event_id: str = ""
    prediction_id: str = ""
    sport: str = ""
    competition: str = ""
    model_probability: Optional[float] = None
    bankroll: float = 100.0
    notes: str = ""


class PaperSettleRequest(BaseModel):
    outcome: str


paper_router = APIRouter(tags=["Kai Betting — Paper"])


@paper_router.get("/paper/bets")
def api_list_bets(status: Optional[str] = None, limit: int = 200):
    with get_db() as conn:
        return {"bets": list_bets(conn, status, limit)}


@paper_router.post("/paper/bets", status_code=201)
def api_place_bet(body: PaperBetRequest):
    with get_db() as conn:
        try:
            bet = place_bet(conn, **body.model_dump())
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"bet": bet}


@paper_router.post("/paper/bets/{bet_id}/settle")
def api_settle_bet(bet_id: str, body: PaperSettleRequest):
    with get_db() as conn:
        try:
            bet = settle_bet(conn, bet_id, body.outcome)
        except KeyError:
            raise HTTPException(status_code=404, detail="bet not found")
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return {"bet": bet}


@paper_router.get("/paper/performance")
def api_performance():
    with get_db() as conn:
        return performance(conn)
