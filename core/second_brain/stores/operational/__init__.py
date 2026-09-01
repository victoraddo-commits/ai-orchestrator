"""Operational store — Infrastructure, Incident, Operational, Decision memory types."""
from core.second_brain.base_store import AppendOnlyStore
from core.second_brain.types import MemoryType

# Memory types backed by this store
SUPPORTED_TYPES = [
    MemoryType.INFRASTRUCTURE,
    MemoryType.INCIDENT,
    MemoryType.OPERATIONAL,
    MemoryType.DECISION,
]

store = AppendOnlyStore("core/second_brain/stores/operational")
