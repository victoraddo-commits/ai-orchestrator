"""Adapter: core/world_model.py → cognitive store.

Mirrors semantic/episodic knowledge updates to the cognitive Second Brain store.
ADDITIVE — existing world_model.py calls continue to work unchanged.
"""
from core.second_brain.writer import SecondBrainWriter
from core.second_brain.types import ChangeType, Confidence, MemoryType, SecondBrainRecord, SourceAuthority

_writer: SecondBrainWriter | None = None


def _get_writer() -> SecondBrainWriter:
    global _writer
    if _writer is None:
        _writer = SecondBrainWriter()
    return _writer


def sync_semantic_to_second_brain(
    statement: str,
    category: str | None = None,
    tags: list[str] | None = None,
    confirmed_by: str | None = None,
) -> str | None:
    """Mirror a semantic knowledge statement to the cognitive store."""
    writer = _get_writer()

    fact = {
        "statement": statement,
        "category": category,
        "tags": tags or [],
        "confirmed_by": confirmed_by,
    }

    try:
        record_id = writer.update(
            store_name="cognitive",
            entity=statement[:256],  # First 256 chars as entity key
            entity_type="semantic",
            memory_type=MemoryType.SEMANTIC,
            fact=fact,
            changed_reason="semantic_knowledge_sync",
            change_type=ChangeType.CREATED,
            confidence=Confidence.DOCUMENTED,  # Semantic knowledge from world model
            source_authority=SourceAuthority.SECOND_BRAIN,
        )
        return record_id
    except Exception:
        return None


def sync_episodic_to_second_brain(
    event_type: str,
    description: str,
    participants: list[str] | None = None,
    location: str | None = None,
) -> str | None:
    """Mirror an episodic event to the cognitive store."""
    writer = _get_writer()

    fact = {
        "event_type": event_type,
        "description": description,
        "participants": participants or [],
        "location": location,
    }

    try:
        record = SecondBrainRecord(
            entity=event_type,
            entity_type="episodic",
            memory_type=MemoryType.EPISODIC,
            fact=fact,
            change_type=ChangeType.CREATED,
            changed_reason="episodic_event_sync",
            confidence=Confidence.CONFIRMED,
            source_authority=SourceAuthority.SECOND_BRAIN,
        )
        record_id = writer.write(store_name="cognitive", record=record)
        return record_id
    except Exception:
        return None
