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
    MergePolicy,
    SecondBrainRecord,
    SourceAuthority,
)

if TYPE_CHECKING:
    pass


class SecondBrainWriter:
    """Single writer for all Second Brain stores."""

    def __init__(self, stores_base: str | Path | None = None):
        if stores_base is None:
            self.root = Path(__file__).parent / "stores"
        else:
            self.root = Path(stores_base)
        self._stores: dict[str, _StoreImpl] = {}

    def _get_store(self, store_name: str) -> _StoreImpl:
        """Lazily create and return a store instance."""
        if store_name not in self._stores:
            # Store dir is directly under root: <root>/<store_name>
            store_dir = self.root / store_name
            policy = STORE_MERGE_POLICIES.get(store_name, MergePolicy.NEWEST_WINS)
            store = _StoreImpl(store_name, store_dir, policy)
            store.ensure_exists()
            self._stores[store_name] = store
        return self._stores[store_name]

    def write(
        self,
        store_name: str,
        record: SecondBrainRecord,
    ) -> str:
        """Direct append to a store. Returns record ID.

        auto_supersedes=False ensures pure append with no chain linkage
        (learning adapter use case: independent events, no supersedes).
        """
        store = self._get_store(store_name)
        return store.append(record, auto_supersedes=False)

    def update(
        self,
        store_name: str,
        entity: str,
        memory_type: MemoryType,
        fact: dict,
        changed_reason: str,
        entity_type: str | None = None,
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
