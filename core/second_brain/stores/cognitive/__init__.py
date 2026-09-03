"""Cognitive store — Semantic, Episodic, Temporal memory types (source_authority)."""
from core.second_brain.base_store import AppendOnlyStore
from core.second_brain.types import MergePolicy, MemoryType

SUPPORTED_TYPES: set[MemoryType] = {
    MemoryType.SEMANTIC,
    MemoryType.EPISODIC,
    MemoryType.TEMPORAL,
}


class CognitiveStore(AppendOnlyStore):
    STORE_NAME = "cognitive"
    MERGE_POLICY = MergePolicy.SOURCE_AUTHORITY


try:
    store = CognitiveStore("memory/stores/cognitive")
    store.ensure_exists()
except Exception:
    store = None
