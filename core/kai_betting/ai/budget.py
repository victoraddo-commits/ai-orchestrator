"""Kai Betting — GPU.ai spend budget controller.

Enforces daily/weekly/monthly spend ceilings. When the daily budget is
exhausted, inference stops and callers fall back to the local statistical
engine — the budget is never silently raised.
"""

from __future__ import annotations

import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Defaults are configurable via env. 0 = unlimited.
DAILY_LIMIT = float(os.environ.get("GPUAI_DAILY_LIMIT", "3.0"))
WEEKLY_LIMIT = float(os.environ.get("GPUAI_WEEKLY_LIMIT", "0"))
MONTHLY_LIMIT = float(os.environ.get("GPUAI_MONTHLY_LIMIT", "0"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


class BettingAIBudgetController:
    """Tracks GPU.ai spend and gates inference against configurable limits.

    Spend is persisted in the ``ai_usage`` table so a worker restart does not
    reset the counters.
    """

    def __init__(self, conn, daily: Optional[float] = None,
                 weekly: Optional[float] = None, monthly: Optional[float] = None):
        self._conn = conn
        self.daily_limit = daily if daily is not None else DAILY_LIMIT
        self.weekly_limit = weekly if weekly is not None else WEEKLY_LIMIT
        self.monthly_limit = monthly if monthly is not None else MONTHLY_LIMIT

    # ── Spend queries ────────────────────────────────────────────────────────
    def _spend_since(self, days: float) -> float:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(cost), 0) AS c FROM ai_usage "
            "WHERE created_at >= datetime('now', ?)",
            (f"-{int(days)} days",),
        ).fetchone()
        return float(row["c"])

    def spend_today(self) -> float:
        return self._spend_since(1)

    def spend_week(self) -> float:
        return self._spend_since(7)

    def spend_month(self) -> float:
        return self._spend_since(30)

    # ── Budget gate ──────────────────────────────────────────────────────────
    def over_budget(self) -> bool:
        if self.daily_limit > 0 and self.spend_today() >= self.daily_limit:
            return True
        if self.weekly_limit > 0 and self.spend_week() >= self.weekly_limit:
            return True
        if self.monthly_limit > 0 and self.spend_month() >= self.monthly_limit:
            return True
        return False

    def remaining_daily(self) -> float:
        if self.daily_limit <= 0:
            return float("inf")
        return max(0.0, self.daily_limit - self.spend_today())

    # ── Recording ────────────────────────────────────────────────────────────
    def record(self, model_key: str, cost: float, input_tokens: int,
               output_tokens: int, latency_ms: int, request_id: str) -> None:
        self._conn.execute(
            "INSERT INTO ai_usage (id, model_key, cost, input_tokens, "
            "output_tokens, latency_ms, request_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (request_id, model_key, cost, input_tokens, output_tokens, latency_ms, request_id),
        )
        self._conn.commit()

    def snapshot(self) -> dict:
        return {
            "daily_limit": self.daily_limit,
            "weekly_limit": self.weekly_limit,
            "monthly_limit": self.monthly_limit,
            "spend_today": self.spend_today(),
            "spend_week": self.spend_week(),
            "spend_month": self.spend_month(),
        }


def summarize_daily_spend(conn) -> dict:
    """Summarize today's GPU.ai spend from the ai_usage table.

    Returns {total: {requests, input_tokens, output_tokens, cost, avg_latency_ms},
              by_model: {model_key: {requests, cost}}}.
    """
    rows = conn.execute(
        "SELECT model_key, COUNT(*) AS requests, "
        "COALESCE(SUM(input_tokens),0) AS in_tok, "
        "COALESCE(SUM(output_tokens),0) AS out_tok, "
        "COALESCE(SUM(cost),0) AS cost, "
        "COALESCE(AVG(latency_ms),0) AS avg_latency "
        "FROM ai_usage WHERE created_at >= datetime('now', '-1 day') "
        "GROUP BY model_key"
    ).fetchall()

    total = {"requests": 0, "input_tokens": 0, "output_tokens": 0,
             "cost": 0.0, "avg_latency_ms": 0}
    by_model = {}
    for r in rows:
        by_model[r["model_key"]] = {
            "requests": r["requests"],
            "cost": float(r["cost"]),
        }
        total["requests"] += r["requests"]
        total["input_tokens"] += r["in_tok"]
        total["output_tokens"] += r["out_tok"]
        total["cost"] += float(r["cost"])
        total["avg_latency_ms"] += float(r["avg_latency"])
    if rows:
        total["avg_latency_ms"] = round(total["avg_latency_ms"] / len(rows))
    return {"total": total, "by_model": by_model}
