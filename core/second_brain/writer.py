"""SecondBrainWriter — atomic write API for Second Brain stores."""
from __future__ import annotations

import os
from typing import Any

from core.second_brain.base_store import AppendOnlyStore
from core.second_brain.types import (
    ChangeType,
    Confidence,
    MemoryType,
    SecondBrainRecord,
    SourceAuthority,
)


class SecondBrainWriter:
    """Writes to Second Brain stores with atomic update (supersedes chain).

    Each store instance is loaded lazily on first write.
    """

    def __init__(self, stores_base: str = "core/second_brain/stores"):
        self.stores_base = stores_base
        self._stores: dict[str, AppendOnlyStore] = {}

    def _get_store(self, store_name: str) -> AppendOnlyStore:
        """Lazily load and cache a store instance."""
        if store_name not in self._stores:
            store_dir = os.path.join(self.stores_base, store_name)
            self._stores[store_name] = AppendOnlyStore(store_dir)
        return self._stores[store_name]

    def write(self, store_name: str, record: SecondBrainRecord) -> str:
        """Write a new record. Returns record ID."""
        store = self._get_store(store_name)
        store.append(record)
        return record.id

    def update(
        self,
        store_name: str,
        entity: str,
        fact: dict[str, Any],
        memory_type: MemoryType,
        changed_reason: str,
        change_type: ChangeType = ChangeType.UPDATED,
        confidence: Confidence = Confidence.CONFIRMED,
        source_authority: SourceAuthority = SourceAuthority.SECOND_BRAIN,
        ttl_seconds: int | None = None,
        entity_type: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Create new record superseding the current head for entity.

        Steps:
        1. Read current head via store.get_current(entity)
        2. Create new record with supersedes = old_record.id (if old exists)
        3. Append new record to JSONL
        4. Update current_index atomically
        5. Return new record ID

        Returns the new record ID.
        """
        store = self._get_store(store_name)

        # Step 1: read current head
        old_record = store.get_current(entity)
        supersedes_id: str | None = old_record.id if old_record is not None else None

        # Step 2: build new record
        new_record = SecondBrainRecord(
            entity=entity,
            entity_type=entity_type,
            memory_type=memory_type,
            fact=fact,
            change_type=change_type,
            changed_reason=changed_reason,
            confidence=confidence,
            source_authority=source_authority,
            supersedes=supersedes_id,
            ttl_seconds=ttl_seconds,
            metadata=metadata or {},
        )

        # Step 3 & 4: append (index update happens inside append())
        store.append(new_record)

        # Step 5: return new record ID
        return new_record.id
