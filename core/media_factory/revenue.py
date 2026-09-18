"""Revenue, cost, profitability and attribution (§30/§31/§32).

Only real, recorded amounts are used. Profitability is verified revenue minus
attributable cost; nothing is estimated or projected. With no data the result
is an honest zero with an UNVERIFIED status.
"""
from __future__ import annotations

import logging
from typing import Optional

from core.media_factory import config, db

logger = logging.getLogger(__name__)


def _round(value: float) -> float:
    return round(float(value), 5)


def compute_profitability(
    revenues: list[dict],
    costs: list[dict],
    *,
    currency: str = "USD",
) -> dict:
    """Pure profitability math (unit-tested)."""
    revenue_total = sum(float(r.get("amount") or 0) for r in revenues)
    revenue_verified = sum(
        float(r.get("amount") or 0) for r in revenues if r.get("verified")
    )
    cost_total = sum(float(c.get("amount") or 0) for c in costs)
    profit = revenue_verified - cost_total
    has_data = bool(revenues or costs)
    if not has_data:
        status = config.STATUS_UNVERIFIED
    elif revenue_total > 0 and revenue_verified == revenue_total:
        status = config.STATUS_VERIFIED
    else:
        status = config.STATUS_PARTIALLY_VERIFIED
    return {
        "currency": currency,
        "revenue_total": _round(revenue_total),
        "revenue_verified": _round(revenue_verified),
        "revenue_unverified": _round(revenue_total - revenue_verified),
        "cost_total": _round(cost_total),
        "profit": _round(profit),
        "status": status,
        "has_data": has_data,
    }


def record_revenue(
    amount: float,
    *,
    content_id: Optional[int] = None,
    pattern_id: Optional[int] = None,
    strategy_version: Optional[int] = None,
    platform_id: Optional[int] = None,
    source: str = "manual",
    currency: str = "USD",
    occurred_at: Optional[str] = None,
    evidence: Optional[dict] = None,
    verified: bool = False,
) -> dict:
    evidence = evidence or {}
    if verified and not evidence:
        verified = False
    status = (
        config.STATUS_VERIFIED
        if verified and evidence
        else config.STATUS_UNVERIFIED
    )
    row = db.insert_returning(
        """
        INSERT INTO revenue
            (content_id, pattern_id, strategy_version, platform_id, source,
             amount, currency, occurred_at, evidence, verified, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (content_id, pattern_id, strategy_version, platform_id, source,
         amount, currency, occurred_at, db.jsonb(evidence), verified, status),
    )
    db.audit("revenue.recorded", entity_type="content", entity_id=content_id,
             payload={"revenue_id": row["id"], "amount": amount, "verified": verified})
    return {"id": row["id"], "status": status, "amount": amount,
            "verified": verified, "currency": currency}


def record_cost(
    amount: float,
    *,
    content_id: Optional[int] = None,
    category: str = "compute",
    currency: str = "USD",
    occurred_at: Optional[str] = None,
    evidence: Optional[dict] = None,
) -> dict:
    evidence = evidence or {}
    status = config.STATUS_PARTIALLY_VERIFIED if evidence else config.STATUS_UNVERIFIED
    row = db.insert_returning(
        """
        INSERT INTO costs (content_id, category, amount, currency, occurred_at,
                           evidence, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (content_id, category, amount, currency, occurred_at, db.jsonb(evidence), status),
    )
    db.audit("cost.recorded", entity_type="content", entity_id=content_id,
             payload={"cost_id": row["id"], "amount": amount, "category": category})
    return {"id": row["id"], "status": status, "amount": amount, "currency": currency}


def _filter(scope_type: str, scope_id: Optional[int]) -> tuple[list[dict], list[dict]]:
    rev_sql = "SELECT * FROM revenue"
    cost_sql = "SELECT * FROM costs"
    params: list = []
    if scope_type == "content" and scope_id is not None:
        rev_sql += " WHERE content_id = %s"
        cost_sql += " WHERE content_id = %s"
        params = [scope_id]
    return db.query(rev_sql, params), db.query(cost_sql, params)


def profitability(
    scope_type: str = "global",
    scope_id: Optional[int] = None,
    *,
    persist: bool = True,
) -> dict:
    revenues, costs = _filter(scope_type, scope_id)
    result = compute_profitability(revenues, costs)
    result.update({"scope_type": scope_type, "scope_id": scope_id,
                   "revenue_count": len(revenues), "cost_count": len(costs)})
    if persist:
        db.insert_returning(
            """
            INSERT INTO profitability
                (scope_type, scope_id, revenue, cost, profit, currency, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (scope_type, scope_id, result["revenue_verified"], result["cost_total"],
             result["profit"], result["currency"], result["status"]),
        )
    db.record_event("profitability", result["status"], detail=result)
    return result


def attribution(content_id: Optional[int] = None) -> dict:
    """Map revenue to content / pattern / strategy without inventing links."""
    clauses = []
    params: list = []
    if content_id is not None:
        clauses.append("content_id = %s")
        params.append(content_id)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = db.query(
        f"""
        SELECT content_id, pattern_id, strategy_version, platform_id,
               sum(amount) AS amount, count(*) AS n,
               bool_and(verified) AS all_verified
        FROM revenue{where}
        GROUP BY content_id, pattern_id, strategy_version, platform_id
        ORDER BY amount DESC NULLS LAST
        """,
        params,
    )
    if not rows:
        return {"status": config.STATUS_UNVERIFIED,
                "detail": "no revenue recorded — nothing to attribute",
                "attributions": []}
    return {"status": config.STATUS_VERIFIED, "attributions": rows}


def latest_revenue(limit: int = 50, offset: int = 0) -> list[dict]:
    return db.query(
        "SELECT * FROM revenue ORDER BY occurred_at DESC NULLS LAST, created_at DESC LIMIT %s OFFSET %s",
        (limit, offset),
    )


def latest_costs(limit: int = 50, offset: int = 0) -> list[dict]:
    return db.query(
        "SELECT * FROM costs ORDER BY occurred_at DESC NULLS LAST, created_at DESC LIMIT %s OFFSET %s",
        (limit, offset),
    )
