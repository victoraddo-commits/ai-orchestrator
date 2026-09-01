"""Project store — Project, Business, Personal Context, Procedural memory types."""
from __future__ import annotations

from core.second_brain.base_store import AppendOnlyStore
from core.second_brain.types import MemoryType

SUPPORTED_TYPES = [
    MemoryType.PROJECT,
    MemoryType.BUSINESS,
    MemoryType.PERSONAL_CONTEXT,
    MemoryType.PROCEDURAL,
]

store = AppendOnlyStore("core/second_brain/stores/project")
