"""Opportunity detection (§8/§26).

Turns a validated, scored trend into a scored content opportunity with an
explicit, auditable formula. Effort is a factory-relative cost estimate;
payoff is the trend score scaled by how well the factory fits the signal.
No external or invented inputs are used.
"""
from __future__ import annotations

import logging
from typing import Optional

from core.media_factory import config, db
from core.media_factory.models import Opportunity

logger = logging.getLogger(__name__)

# Factory-level constants (relative, not currency estimates).
FACTORY_PROFILES: dict[str, dict] = {
    "public_domain_cartoon": {
        "base_effort": 1.0,
        "fit": 1.0,
        "angle": "public-domain source with original commentary/narration",
    },
    "original_ai_drama": {
        "base_effort": 1.4,
        "fit": 1.15,
        "angle": "original episodic drama built on an established universe",
    },
}
DEFAULT_PROFILE = {"base_effort": 1.2, "fit": 1.0, "angle": "original content"}

FORMULA = (
    "effort = base_effort + 0.5*competition + 0.5*(1-momentum); "
    "expected_payoff = trend_score * fit; score = expected_payoff / effort"
)


def compute_opportunity(
    trend_score: float,
    momentum: float,
    competition: float,
    *,
    factory_key: str,
    title: str = "",
) -> Opportunity:
    """Pure scoring function (unit-tested)."""
    profile = FACTORY_PROFILES.get(factory_key, DEFAULT_PROFILE)
    effort = profile["base_effort"] + 0.5 * competition + 0.5 * (1.0 - momentum)
    expected_payoff = trend_score * profile["fit"]
    score = expected_payoff / effort if effort > 0 else 0.0
    angle = f"{profile['angle']}: {title}".strip(": ")
    return Opportunity(
        factory_key=factory_key,
        angle=angle,
        effort=effort,
        expected_payoff=expected_payoff,
        score=score,
        formula=FORMULA,
    )


def _existing_id(trend_id: int, factory_key: str) -> Optional[int]:
    row = db.query_one(
        "SELECT id FROM opportunities WHERE trend_id = %s AND factory_key = %s LIMIT 1",
        (trend_id, factory_key),
    )
    return row["id"] if row else None


def detect(
    trend_rows: list[dict],
    *,
    factory_key: str = "public_domain_cartoon",
    persist: bool = True,
) -> dict:
    """Score every validated trend into an opportunity for *factory_key*."""
    created: list[int] = []
    skipped = 0
    for trend in trend_rows:
        trend_id = trend.get("id")
        score = trend.get("score")
        if trend_id is None or score is None:
            continue
        if persist and _existing_id(trend_id, factory_key) is not None:
            skipped += 1
            continue
        opp = compute_opportunity(
            float(score),
            float(trend.get("momentum") or 0.0),
            float(trend.get("competition") or 0.0),
            factory_key=factory_key,
            title=trend.get("title") or "",
        )
        if not persist:
            created.append(opp.to_dict())  # type: ignore[arg-type]
            continue
        try:
            row = db.insert_returning(
                """
                INSERT INTO opportunities
                    (trend_id, factory_key, angle, effort, expected_payoff, score,
                     formula, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    trend_id,
                    factory_key,
                    opp.angle,
                    opp.effort,
                    opp.expected_payoff,
                    opp.score,
                    opp.formula,
                    config.STATUS_VERIFIED,
                ),
            )
            if row:
                created.append(row["id"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("opportunity store failed (%s)", type(exc).__name__)

    result = {
        "status": config.STATUS_VERIFIED if created or skipped else config.STATUS_UNVERIFIED,
        "factory_key": factory_key,
        "considered": len(trend_rows),
        "created": len(created),
        "skipped_existing": skipped,
        "ids": created,
    }
    db.record_event("opportunity_detection", result["status"], detail=result)
    db.audit("opportunity.detect", payload=result)
    return result


def latest(limit: int = 50, offset: int = 0, factory_key: Optional[str] = None) -> list[dict]:
    if factory_key:
        return db.query(
            """
            SELECT * FROM opportunities WHERE factory_key = %s
            ORDER BY score DESC NULLS LAST, created_at DESC
            LIMIT %s OFFSET %s
            """,
            (factory_key, limit, offset),
        )
    return db.query(
        """
        SELECT * FROM opportunities
        ORDER BY score DESC NULLS LAST, created_at DESC
        LIMIT %s OFFSET %s
        """,
        (limit, offset),
    )
