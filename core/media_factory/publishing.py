"""Publishing job queue with honest mode gating (§50/§34).

Modes: dry_run | test | canary | live. There are no platform OAuth tokens on
CT111, so only ``dry_run`` can execute. ``test``/``canary``/``live`` are
recorded and marked BLOCKED with the reason. Nothing is ever posted.
"""
from __future__ import annotations

import logging
from typing import Optional

from core.media_factory import config, db
from core.media_factory.models import PublishMode
from core.media_factory import rights as rights_mod

logger = logging.getLogger(__name__)


def resolve_dispatch(
    mode: str,
    *,
    publish_enabled: bool,
    has_token: bool,
    publishable: bool,
) -> dict:
    """Pure decision table for a dispatch attempt (unit-tested)."""
    mode = (mode or "").lower()
    if not publishable:
        return {"action": "blocked", "status": config.STATUS_BLOCKED,
                "reason": "rights gate: content is not publishable"}
    if mode not in {m.value for m in PublishMode}:
        return {"action": "blocked", "status": config.STATUS_FAILED,
                "reason": f"unknown publish mode {mode!r}"}
    if mode == PublishMode.DRY_RUN.value:
        return {"action": "dry_run", "status": config.STATUS_VERIFIED,
                "reason": "dry run executes locally without a platform call"}
    if not publish_enabled:
        return {"action": "blocked", "status": config.STATUS_BLOCKED,
                "reason": "MEDIA_PUBLISH_ENABLED=false"}
    if not has_token:
        return {"action": "blocked", "status": config.STATUS_BLOCKED,
                "reason": config.BLOCKED_PUBLISHING}
    return {"action": "publish", "status": config.STATUS_UNVERIFIED,
            "reason": "token present but publisher not implemented"}


def _has_platform_token(platform_id: Optional[int]) -> bool:
    if platform_id is None:
        return False
    row = db.query_one(
        "SELECT token_ref FROM accounts WHERE platform_id = %s AND token_ref IS NOT NULL LIMIT 1",
        (platform_id,),
    )
    return bool(row and row.get("token_ref"))


def enqueue(
    content_id: int,
    *,
    account_id: Optional[int] = None,
    platform_id: Optional[int] = None,
    mode: str = PublishMode.DRY_RUN.value,
    idempotency_key: Optional[str] = None,
    scheduled_at: Optional[str] = None,
) -> dict:
    mode = (mode or "dry_run").lower()
    if mode not in {m.value for m in PublishMode}:
        raise ValueError(f"invalid publish mode: {mode!r}")
    key = idempotency_key or f"content:{content_id}:platform:{platform_id}:{mode}"

    verdict = rights_mod.check_publishable(content_id)
    publishable = bool(verdict.get("publishable"))
    blocked_reason = None if publishable else verdict.get("reason")
    status = config.STATUS_UNVERIFIED if publishable else config.STATUS_BLOCKED

    row = db.insert_returning(
        """
        INSERT INTO publishing_jobs
            (content_id, account_id, platform_id, mode, status, idempotency_key,
             scheduled_at, blocked_reason)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (idempotency_key) DO UPDATE SET
            updated_at = now()
        RETURNING id, status, blocked_reason
        """,
        (content_id, account_id, platform_id, mode, status, key,
         scheduled_at, blocked_reason),
    )
    db.audit("publishing.enqueued", entity_type="publishing_job", entity_id=row["id"],
             payload={"content_id": content_id, "mode": mode,
                      "publishable": publishable, "blocked_reason": blocked_reason})
    return {"id": row["id"], "status": row["status"], "mode": mode,
            "idempotency_key": key, "publishable": publishable,
            "blocked_reason": row["blocked_reason"]}


def _dry_run(job: dict) -> dict:
    content = db.query_one("SELECT id, title, state FROM content WHERE id = %s",
                           (job["content_id"],))
    return {
        "dry_run": True,
        "would_publish": bool(content),
        "content": content,
        "mode": job["mode"],
        "platform_id": job["platform_id"],
        "note": "no platform call performed",
    }


def dispatch(job_id: int) -> dict:
    job = db.query_one("SELECT * FROM publishing_jobs WHERE id = %s", (job_id,))
    if not job:
        return {"status": config.STATUS_MISSING, "job_id": job_id,
                "blocked_reason": "job not found"}

    verdict = rights_mod.check_publishable(job["content_id"])
    decision = resolve_dispatch(
        job["mode"],
        publish_enabled=config.publish_enabled(),
        has_token=_has_platform_token(job["platform_id"]),
        publishable=bool(verdict.get("publishable")),
    )

    new_status = decision["status"]
    response = {"decision": decision}
    blocked = decision["action"] == "blocked"
    if decision["action"] == "dry_run":
        response.update(_dry_run(job))
    if blocked:
        response["blocked_reason"] = decision["reason"]

    db.execute(
        """
        UPDATE publishing_jobs
        SET status = %s, attempt = attempt + 1, last_error = %s,
            blocked_reason = %s, response = %s, updated_at = now()
        WHERE id = %s
        """,
        (new_status, None if not blocked else decision["reason"],
         decision["reason"] if blocked else None, db.jsonb(response), job_id),
    )
    db.record_event("publishing_dispatch", new_status, detail={"job_id": job_id, **response})
    db.audit("publishing.dispatched", entity_type="publishing_job", entity_id=job_id,
             payload=response)
    return {"status": new_status, "job_id": job_id, **response}


def latest(limit: int = 50, offset: int = 0) -> list[dict]:
    return db.query(
        "SELECT * FROM publishing_jobs ORDER BY created_at DESC LIMIT %s OFFSET %s",
        (limit, offset),
    )
