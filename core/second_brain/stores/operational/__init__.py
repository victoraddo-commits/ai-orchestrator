"""Operational store — Infrastructure, Incident, Operational, Decision memory types (newest_wins)."""
from core.second_brain.base_store import AppendOnlyStore
from core.second_brain.types import MergePolicy, MemoryType

SUPPORTED_TYPES: set[MemoryType] = {
    MemoryType.INFRASTRUCTURE,
    MemoryType.INCIDENT,
    MemoryType.OPERATIONAL,
    MemoryType.DECISION,
}


class OperationalStore(AppendOnlyStore):
    STORE_NAME = "operational"
    MERGE_POLICY = MergePolicy.NEWEST_WINS


# Default singleton — will use memory/stores/operational if it exists
try:
    store = OperationalStore("memory/stores/operational")
    store.ensure_exists()
except Exception:
    # Path may not exist yet; lazy initialization defers to writer
    store = None
