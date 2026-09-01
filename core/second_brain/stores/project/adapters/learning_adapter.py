"""Adapter: core/build_learning.py → project store.

Mirrors learning/learning lesson writes to the project Second Brain store.
ADDITIVE — existing build_learning.py calls continue to work unchanged.
"""
from core.second_brain.writer import SecondBrainWriter
from core.second_brain.types import MemoryType, ChangeType, Confidence, SourceAuthority, SecondBrainRecord

_writer: SecondBrainWriter | None = None


def _get_writer() -> SecondBrainWriter:
    global _writer
    if _writer is None:
        _writer = SecondBrainWriter()
    return _writer


def sync_lesson_to_second_brain(
    action: str,
    classification: str,  # "trusted" | "observe" | "avoid"
    context: dict,
    outcome: str | None = None,
) -> str | None:
    """Mirror a learning lesson to the project store.

    Uses action as the entity so lessons for the same action are grouped.
    """
    writer = _get_writer()

    fact = {
        "action": action,
        "classification": classification,
        "context": context,
        "outcome": outcome,
    }

    try:
        record_id = writer.update(
            store_name="project",
            entity=action,
            entity_type="learning_lesson",
            memory_type=MemoryType.PROJECT,
            fact=fact,
            changed_reason=f"learning::{classification}",
            change_type=ChangeType.CREATED,
            confidence=Confidence.CONFIRMED,
            source_authority=SourceAuthority.SECOND_BRAIN,
        )
        return record_id
    except Exception:
        return None


def sync_build_learn_to_second_brain(
    build_id: str,
    event_type: str,  # "build_started" | "build_completed" | "build_failed"
    details: dict,
) -> str | None:
    """Mirror a build_learning event to the project store as an EPISODIC record."""
    writer = _get_writer()

    fact = {
        "build_id": build_id,
        "event_type": event_type,
        "details": details,
    }

    try:
        record = SecondBrainRecord(
            entity=build_id,
            entity_type="build",
            memory_type=MemoryType.EPISODIC,
            fact=fact,
            changed_reason=f"build_event::{event_type}",
            change_type=ChangeType.CREATED,
            confidence=Confidence.CONFIRMED,
            source_authority=SourceAuthority.SECOND_BRAIN,
        )
        return writer.write(store_name="project", record=record)
    except Exception:
        return None
