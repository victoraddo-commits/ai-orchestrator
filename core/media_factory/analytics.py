"""Analytics ingestion (§42/§8).

Platform APIs (YouTube Analytics, TikTok Business) require OAuth tokens that
do not exist on CT111, so ``ingest`` reports BLOCKED honestly and never
fabricates metrics. A manual-entry path records operator-supplied numbers and
labels them by evidence quality.
"""
from __future__ import annotations

import logging
from typing import Optional

from core.media_factory import config, db

logger = logging.getLogger(__name__)

PLATFORM_SOURCES = {
    "youtube": "youtube_analytics_api",
    "tiktok": "tiktok_business_api",
}


def _token_for(platform_key: str) -> Optional[str]:
    row = db.query_one(
        """
        SELECT a.token_ref FROM accounts a
        JOIN platforms p ON p.id = a.platform_id
        WHERE p.key = %s AND a.token_ref IS NOT NULL
        LIMIT 1
        """,
        (platform_key,),
    )
    return row["token_ref"] if row else None


def availability() -> dict:
    out = {}
    for key, source in PLATFORM_SOURCES.items():
        token = _token_for(key)
        out[key] = {
            "status": config.STATUS_VERIFIED if token else config.STATUS_BLOCKED,
            "source": source,
            "blocked_reason": None if token else config.BLOCKED_ANALYTICS,
        }
    return out


def ingest(content_id: int, platform_key: str, *, metrics: Optional[list[str]] = None) -> dict:
    """Platform API ingestion — BLOCKED without a configured token."""
    if platform_key not in PLATFORM_SOURCES:
        return {"status": config.STATUS_FAILED,
                "blocked_reason": f"unsupported platform {platform_key!r}"}
    token = _token_for(platform_key)
    if not token:
        result = {
            "status": config.STATUS_BLOCKED,
            "content_id": content_id,
            "platform": platform_key,
            "metrics": metrics or [],
            "blocked_reason": config.BLOCKED_ANALYTICS,
            "source": PLATFORM_SOURCES[platform_key],
        }
        db.record_event("analytics_ingest", config.STATUS_BLOCKED, detail=result)
        db.audit("analytics.ingest.blocked", entity_type="content", entity_id=content_id,
                 payload=result)
        return result
    # Token exists but the provider client is not implemented yet.
    result = {
        "status": config.STATUS_UNVERIFIED,
        "content_id": content_id,
        "platform": platform_key,
        "blocked_reason": "provider client not implemented",
        "source": PLATFORM_SOURCES[platform_key],
    }
    db.audit("analytics.ingest.unimplemented", entity_type="content",
             entity_id=content_id, payload=result)
    return result


def manual_entry(
    content_id: int,
    metric: str,
    value: float,
    *,
    platform_id: Optional[int] = None,
    observed_at: Optional[str] = None,
    evidence: Optional[dict] = None,
) -> dict:
    """Record an operator-supplied metric. Never marked VERIFIED without evidence."""
    evidence = evidence or {}
    status = config.STATUS_PARTIALLY_VERIFIED if evidence else config.STATUS_UNVERIFIED
    row = db.insert_returning(
        """
        INSERT INTO analytics
            (content_id, platform_id, source, metric, value, observed_at, raw, status)
        VALUES (%s, %s, 'manual', %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (content_id, platform_id, metric, value, observed_at,
         db.jsonb(evidence), status),
    )
    db.audit("analytics.manual_entry", entity_type="content", entity_id=content_id,
             payload={"metric": metric, "value": value, "evidence": bool(evidence)})
    return {"id": row["id"], "status": status, "metric": metric, "value": value,
            "source": "manual", "verified": False}


def latest(limit: int = 50, offset: int = 0) -> list[dict]:
    return db.query(
        "SELECT * FROM analytics ORDER BY created_at DESC LIMIT %s OFFSET %s",
        (limit, offset),
    )


def for_content(content_id: int) -> list[dict]:
    return db.query(
        "SELECT * FROM analytics WHERE content_id = %s ORDER BY observed_at DESC NULLS LAST",
        (content_id,),
    )
