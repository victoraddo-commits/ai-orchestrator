"""Adapter: core/kai/conversation.py → conversational store.

Mirrors conversation session writes to the conversational Second Brain store.
ADDITIVE — existing conversation.py calls continue to work unchanged.
"""
from __future__ import annotations

from core.second_brain.types import (
    ChangeType,
    Confidence,
    MemoryType,
    SecondBrainRecord,
    SourceAuthority,
)
from core.second_brain.writer import SecondBrainWriter

_writer: SecondBrainWriter | None = None


def _get_writer() -> SecondBrainWriter:
    global _writer
    if _writer is None:
        _writer = SecondBrainWriter()
    return _writer


def sync_session_to_second_brain(
    session_id: str,
    turns: list[dict],
    summary: str | None = None,
    tags: list[str] | None = None,
) -> str | None:
    """Mirror a conversation session to the conversational store.

    Args:
        session_id: unique session identifier
        turns: list of {"role": "user|assistant|system", "content": str, "timestamp": ISO}
        summary: optional session summary
        tags: optional tags

    Returns:
        The record ID, or None if sync failed.
    """
    writer = _get_writer()

    fact = {
        "session_id": session_id,
        "turns": turns,
        "summary": summary,
        "tags": tags or [],
        "turn_count": len(turns),
    }

    record = SecondBrainRecord(
        entity=session_id,
        entity_type="session",
        memory_type=MemoryType.CONVERSATION,
        fact=fact,
        change_type=ChangeType.CREATED,
        changed_reason="conversation_session_sync",
        confidence=Confidence.CONFIRMED,
        source_authority=SourceAuthority.HISTORICAL_CHAT,
    )

    try:
        record_id = writer.write(store_name="conversational", record=record)
        return record_id
    except Exception:
        return None


def sync_turn_to_second_brain(
    session_id: str,
    role: str,
    content: str,
    timestamp: str,
) -> str | None:
    """Append a single turn to an existing conversation session record.

    Uses update() to create a new version with the added turn.
    """
    writer = _get_writer()

    fact = {
        "session_id": session_id,
        "role": role,
        "content": content,
        "timestamp": timestamp,
        "event": "turn_added",
    }

    try:
        record_id = writer.update(
            store_name="conversational",
            entity=session_id,
            entity_type="session",
            memory_type=MemoryType.CONVERSATION,
            fact=fact,
            changed_reason=f"turn_added::{role}",
            change_type=ChangeType.UPDATED,
            confidence=Confidence.CONFIRMED,
            source_authority=SourceAuthority.HISTORICAL_CHAT,
        )
        return record_id
    except Exception:
        return None
