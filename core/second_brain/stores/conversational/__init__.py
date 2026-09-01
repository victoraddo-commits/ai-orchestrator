"""Conversational store — Conversation, Working, Short-Term memory types."""
from core.second_brain.base_store import AppendOnlyStore
from core.second_brain.types import MemoryType

SUPPORTED_TYPES = [
    MemoryType.CONVERSATION,
    MemoryType.WORKING_MEMORY,
    MemoryType.SHORT_TERM,
]

store = AppendOnlyStore("core/second_brain/stores/conversational")
