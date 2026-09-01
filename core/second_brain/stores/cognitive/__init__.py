"""Cognitive store — Semantic, Episodic, Temporal memory types."""
from __future__ import annotations

from core.second_brain.base_store import AppendOnlyStore
from core.second_brain.types import MemoryType

SUPPORTED_TYPES = [
    MemoryType.SEMANTIC,
    MemoryType.EPISODIC,
    MemoryType.TEMPORAL,
]

store = AppendOnlyStore("core/second_brain/stores/cognitive")
