"""SecondBrainWriter — atomic write API for the Second Brain.

Write flow for update():
  1. Read current head: current_index[entity] → old_record_id
  2. Write new record to append-only JSONL
  3. Mark old record's superseded_by field
  4. Update current_index[entity] = new_record_id
  5. Return new record ID

All in-process; no distributed locking.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from core.second_brain.base_store import AppendOnlyStore
from core.second_brain.registry import STORE_MERGE_POLICIES
from core.second_brain.types import (
    ChangeType,
    Confidence,
    MemoryType,
    SecondBrainRecord,
    SourceAuthority,
)

if TYPE_CHECKING:
    pass


class SecondBrainWriter:
    """Single writer for all Second Brain stores."""

    def __init__(self, second_brain_root: str | Path | None = None):
        if second_brain_root is None:
            self.root = Path(__file__).parent / "stores"
        else:
            self.root = Path(second_brain_root)
        self._stores: dict[str, _StoreImpl] = {}

    def _get_store(self, store_name: str) -> _StoreImpl:
        """Lazily create and return a store instance."""
        if store_name not in self._stores:
            store_dir = self.root / store_name
            policy = STORE_MERGE_POLICIES.get(store_name)
            store = _StoreImpl(store_name, store_dir, policy)
            store.ensure_exists()
            self._stores[store_name] = store
        return self._stores[store_name]

    def write(
        self,
        store_name: str,
        entity: str | None,
        entity_type: str | None,
        memory_type: MemoryType,
        fact: dict,
        change_type: ChangeType = ChangeType.CREATED,
        changed_reason: str = "",
        confidence: Confidence = Confidence.CONFIRMED,
        source_authority: SourceAuthority = SourceAuthority.SECOND_BRAIN,
        ttl_seconds: int | None = None,
        metadata: dict | None = None,
        supersedes: str | None = None,
    ) -> str:
        """Direct append to a store. Returns record ID."""
        record = SecondBrainRecord(
            entity=entity,
            entity_type=entity_type,
            memory_type=memory_type,
            fact=fact,
            change_type=change_type,
            changed_reason=changed_reason,
            confidence=confidence,
            source_authority=source_authority,
            superseded_by=None,
            ttl_seconds=ttl_seconds,
            metadata=metadata or {},
            supersedes=supersedes,
        )
        store = self._get_store(store_name)
        return store.append(record)

    def update(
        self,
        store_name: str,
        entity: str,
        entity_type: str | None,
        memory_type: MemoryType,
        fact: dict,
        changed_reason: str,
        change_type: ChangeType = ChangeType.UPDATED,
        confidence: Confidence = Confidence.CONFIRMED,
        source_authority: SourceAuthority = SourceAuthority.SECOND_BRAIN,
        ttl_seconds: int | None = None,
        metadata: dict | None = None,
    ) -> str:
        """Update a record: creates new record, marks previous as superseded.

        If no previous record exists for this entity, creates a new record.
        Returns the new record ID.
        """
        store = self._get_store(store_name)
        # Get current head
        current = store.get_current(entity)
        old_id = current.id if current else None

        record = SecondBrainRecord(
            entity=entity,
            entity_type=entity_type,
            memory_type=memory_type,
            fact=fact,
            change_type=change_type,
            changed_reason=changed_reason,
            confidence=confidence,
            source_authority=source_authority,
            superseded_by=None,
            ttl_seconds=ttl_seconds,
            metadata=metadata or {},
            supersedes=old_id,
        )
        new_id = store.append(record)
        return new_id


class _StoreImpl(AppendOnlyStore):
    """Store implementation with configurable merge policy."""

    def __init__(self, name: str, store_dir: Path, merge_policy):
        super().__init__(store_dir)
        self.STORE_NAME = name
        self.MERGE_POLICY = merge_policy
