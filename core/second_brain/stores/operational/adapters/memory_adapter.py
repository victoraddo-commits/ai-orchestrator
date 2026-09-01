"""Adapter: core/memory.py → operational store.

Mirrors memory.save() calls to the operational Second Brain store.
This is ADDITIVE — existing core/memory.py calls continue to work unchanged.
"""
from core.second_brain.writer import SecondBrainWriter
from core.second_brain.types import ChangeType, Confidence, MemoryType, SourceAuthority

# Singleton writer — lazily initialized
_writer: SecondBrainWriter | None = None


def _get_writer() -> SecondBrainWriter:
    global _writer
    if _writer is None:
        _writer = SecondBrainWriter()
    return _writer


def sync_to_second_brain(
    name: str,
    data: dict,
    change_type: ChangeType = ChangeType.CREATED,
) -> None:
    """Mirror a memory.py save() to the operational store.

    This is called by the adapter hook AFTER memory.save() completes.

    Args:
        name: entity name (used as entity if entity not in data)
        data: the full fact dict being saved to memory.py
        change_type: ChangeType enum value (CREATED, UPDATED, etc.)
    """
    writer = _get_writer()

    # Determine entity and entity_type
    entity = data.get("entity") or name
    entity_type = data.get("entity_type", "unknown")

    # Map entity_type → MemoryType
    type_map = {
        "service": MemoryType.INFRASTRUCTURE,
        "host": MemoryType.INFRASTRUCTURE,
        "container": MemoryType.INFRASTRUCTURE,
        "deployment": MemoryType.INFRASTRUCTURE,
        "incident": MemoryType.INCIDENT,
        "decision": MemoryType.DECISION,
    }
    memory_type = type_map.get(entity_type, MemoryType.OPERATIONAL)

    # Build fact — store the full data dict
    fact = dict(data)

    writer.update(
        store_name="operational",
        entity=entity,
        entity_type=entity_type,
        memory_type=memory_type,
        fact=fact,
        changed_reason=data.get("changed_reason", ""),
        change_type=change_type,
        confidence=Confidence.CONFIRMED,
        source_authority=SourceAuthority.SECOND_BRAIN,
    )
