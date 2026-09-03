"""Relationship store — Relationship, Document memory types (union_all merge).

Stores operator profiles, interaction history, and relationship state.
Reads from existing memory/ files (kai_chat_history, learning_lessons) and
mirrors relationship-relevant facts to records.jsonl.
"""
from core.second_brain.base_store import AppendOnlyStore
from core.second_brain.types import MergePolicy


class RelationshipStore(AppendOnlyStore):
    """Relationship domain store using union_all merge.

    Combines facts from multiple sources without deduplication —
    all interaction records are preserved.
    """

    STORE_NAME = "relationship"
    MERGE_POLICY = MergePolicy.UNION_ALL
