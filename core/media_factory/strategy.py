"""Versioned strategy (§25).

Strategy versions are immutable snapshots; only the ``active`` flag and
``approved_by`` may change. Activation/rollback is reversible and every change
records evidence through the audit ledger.
"""
from __future__ import annotations

import logging
from typing import Optional

from core.media_factory import config, db

logger = logging.getLogger(__name__)


def next_version(existing: list[int]) -> int:
    """Pure version increment (unit-tested)."""
    return (max(existing) + 1) if existing else 1


def create_version(
    strategy: dict,
    *,
    evidence: Optional[dict] = None,
    approved_by: Optional[str] = None,
    parent_version: Optional[int] = None,
) -> dict:
    rows = db.query("SELECT version FROM strategy_versions")
    version = next_version([r["version"] for r in rows])
    row = db.insert_returning(
        """
        INSERT INTO strategy_versions
            (version, parent_version, strategy, evidence, status, active, approved_by)
        VALUES (%s, %s, %s, %s, %s, FALSE, %s)
        RETURNING *
        """,
        (version, parent_version, db.jsonb(strategy), db.jsonb(evidence or {}),
         config.STATUS_PARTIALLY_VERIFIED if evidence else config.STATUS_UNVERIFIED,
         approved_by),
    )
    db.audit("strategy.version_created", entity_type="strategy_version",
             entity_id=row["id"], payload={"version": version,
                                           "approved_by": approved_by})
    return row


def activate(version: int, *, approved_by: Optional[str] = None) -> dict:
    target = db.query_one("SELECT * FROM strategy_versions WHERE version = %s", (version,))
    if not target:
        return {"status": config.STATUS_MISSING, "blocked_reason": "version not found"}
    with db.cursor() as cur:
        cur.execute("UPDATE strategy_versions SET active = FALSE, updated_at = now() WHERE active")
        cur.execute(
            "UPDATE strategy_versions SET active = TRUE, approved_by = COALESCE(%s, approved_by), updated_at = now() WHERE version = %s",
            (approved_by, version),
        )
    db.audit("strategy.activated", entity_type="strategy_version", entity_id=target["id"],
             actor=approved_by or "system", payload={"version": version})
    return {"status": config.STATUS_VERIFIED, "active_version": version}


def rollback(version: int, *, reason: Optional[str] = None) -> dict:
    result = activate(version, approved_by=None)
    if result["status"] == config.STATUS_VERIFIED:
        db.audit("strategy.rollback", entity_type="strategy_version",
                 payload={"version": version, "reason": reason})
        result["reason"] = reason
    return result


def latest(limit: int = 50, offset: int = 0) -> list[dict]:
    return db.query(
        "SELECT * FROM strategy_versions ORDER BY version DESC LIMIT %s OFFSET %s",
        (limit, offset),
    )


def active() -> Optional[dict]:
    return db.query_one("SELECT * FROM strategy_versions WHERE active LIMIT 1")
