"""Conversational store — Conversation, Working Memory, Short-term memory types (newest_wins)."""
from core.second_brain.base_store import AppendOnlyStore
from core.second_brain.types import MergePolicy, MemoryType

SUPPORTED_TYPES: set[MemoryType] = {
    MemoryType.CONVERSATION,
    MemoryType.WORKING_MEMORY,
    MemoryType.SHORT_TERM,
}


class ConversationalStore(AppendOnlyStore):
    STORE_NAME = "conversational"
    MERGE_POLICY = MergePolicy.NEWEST_WINS


try:
    store = ConversationalStore("memory/stores/conversational")
    store.ensure_exists()
except Exception:
    store = None
