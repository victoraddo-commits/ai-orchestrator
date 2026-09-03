"""Relationship store — Relationship, Document memory types (union_all)."""
from core.second_brain.base_store import AppendOnlyStore
from core.second_brain.types import MergePolicy, MemoryType

SUPPORTED_TYPES: set[MemoryType] = {
    MemoryType.RELATIONSHIP,
    MemoryType.DOCUMENT,
}


class RelationshipStore(AppendOnlyStore):
    STORE_NAME = "relationship"
    MERGE_POLICY = MergePolicy.UNION_ALL


try:
    store = RelationshipStore("memory/stores/relationship")
    store.ensure_exists()
except Exception:
    store = None
