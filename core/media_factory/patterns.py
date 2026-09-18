"""Winning-pattern library + lifecycle (§18/§19).

Lifecycle: HYPOTHESIS → EMERGING → VALIDATED → STRATEGIC, with DECLINED
reachable from any non-terminal state. Every transition is recorded in
``pattern_lifecycle`` with a reason and evidence.
"""
from __future__ import annotations

import logging
from typing import Optional

from core.media_factory import config, db
from core.media_factory.models import PatternState

logger = logging.getLogger(__name__)

_ALLOWED: dict[str, set[str]] = {
    PatternState.HYPOTHESIS.value: {PatternState.EMERGING.value, PatternState.DECLINED.value},
    PatternState.EMERGING.value: {
        PatternState.VALIDATED.value, PatternState.HYPOTHESIS.value,
        PatternState.DECLINED.value,
    },
    PatternState.VALIDATED.value: {
        PatternState.STRATEGIC.value, PatternState.EMERGING.value,
        PatternState.DECLINED.value,
    },
    PatternState.STRATEGIC.value: {PatternState.DECLINED.value},
    PatternState.DECLINED.value: set(),
}


def next_states(state: str) -> list[str]:
    return sorted(_ALLOWED.get(state, set()))


def can_transition(from_state: str, to_state: str) -> bool:
    return to_state in _ALLOWED.get(from_state, set())


def get_or_create(
    key: str,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    evidence: Optional[dict] = None,
) -> dict:
    row = db.query_one("SELECT * FROM patterns WHERE key = %s", (key,))
    if row:
        return row
    row = db.insert_returning(
        """
        INSERT INTO patterns (key, name, description, state, evidence, status)
        VALUES (%s, %s, %s, 'HYPOTHESIS', %s, %s)
        RETURNING *
        """,
        (key, name or key, description, db.jsonb(evidence or {}),
         config.STATUS_UNVERIFIED),
    )
    db.execute(
        """
        INSERT INTO pattern_lifecycle (pattern_id, from_state, to_state, reason, actor)
        VALUES (%s, NULL, 'HYPOTHESIS', 'created', 'system')
        """,
        (row["id"],),
    )
    db.audit("pattern.created", entity_type="pattern", entity_id=row["id"],
             payload={"key": key})
    return row


def advance(
    pattern_id: int,
    to_state: str,
    *,
    reason: Optional[str] = None,
    evidence: Optional[dict] = None,
    actor: str = "system",
) -> dict:
    pattern = db.query_one("SELECT * FROM patterns WHERE id = %s", (pattern_id,))
    if not pattern:
        return {"status": config.STATUS_MISSING, "blocked_reason": "pattern not found"}
    from_state = pattern["state"]
    if not can_transition(from_state, to_state):
        return {
            "status": config.STATUS_FAILED,
            "blocked_reason": f"illegal transition {from_state} -> {to_state}",
            "pattern_id": pattern_id,
            "from_state": from_state,
            "to_state": to_state,
        }
    status = config.STATUS_PARTIALLY_VERIFIED
    if to_state == PatternState.VALIDATED.value:
        status = config.STATUS_PARTIALLY_VERIFIED if not evidence else config.STATUS_VERIFIED
    elif to_state == PatternState.STRATEGIC.value:
        status = config.STATUS_VERIFIED if evidence else config.STATUS_PARTIALLY_VERIFIED
    db.execute(
        "UPDATE patterns SET state = %s, evidence = %s, status = %s, updated_at = now() WHERE id = %s",
        (to_state, db.jsonb(evidence or pattern.get("evidence") or {}), status, pattern_id),
    )
    db.execute(
        """
        INSERT INTO pattern_lifecycle
            (pattern_id, from_state, to_state, reason, evidence, actor)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (pattern_id, from_state, to_state, reason, db.jsonb(evidence or {}), actor),
    )
    db.audit("pattern.transition", entity_type="pattern", entity_id=pattern_id,
             actor=actor, payload={"from": from_state, "to": to_state, "reason": reason})
    return {"status": status, "pattern_id": pattern_id,
            "from_state": from_state, "to_state": to_state, "reason": reason}


def latest(limit: int = 50, offset: int = 0) -> list[dict]:
    return db.query(
        "SELECT * FROM patterns ORDER BY updated_at DESC LIMIT %s OFFSET %s",
        (limit, offset),
    )


def lifecycle(pattern_id: int) -> list[dict]:
    return db.query(
        "SELECT * FROM pattern_lifecycle WHERE pattern_id = %s ORDER BY created_at ASC",
        (pattern_id,),
    )
