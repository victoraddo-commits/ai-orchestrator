"""Project store — Project, Business, Personal Context, Procedural memory types (newest_wins)."""
from core.second_brain.base_store import AppendOnlyStore
from core.second_brain.types import MergePolicy, MemoryType

SUPPORTED_TYPES: set[MemoryType] = {
    MemoryType.PROJECT,
    MemoryType.BUSINESS,
    MemoryType.PERSONAL_CONTEXT,
    MemoryType.PROCEDURAL,
}


class ProjectStore(AppendOnlyStore):
    STORE_NAME = "project"
    MERGE_POLICY = MergePolicy.NEWEST_WINS


try:
    store = ProjectStore("memory/stores/project")
    store.ensure_exists()
except Exception:
    store = None
