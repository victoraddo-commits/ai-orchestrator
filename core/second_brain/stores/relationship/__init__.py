"""Relationship store — Relationship, Document memory types."""
from core.second_brain.base_store import AppendOnlyStore
from core.second_brain.types import MemoryType

SUPPORTED_TYPES = [
    MemoryType.RELATIONSHIP,
    MemoryType.DOCUMENT,
]

store = AppendOnlyStore("core/second_brain/stores/relationship")
