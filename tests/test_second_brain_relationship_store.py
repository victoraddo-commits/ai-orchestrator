"""Tests for the relationship store."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

import pytest

from core.second_brain.base_store import AppendOnlyStore
from core.second_brain.stores.relationship import SUPPORTED_TYPES, store
from core.second_brain.types import (
    ChangeType,
    Confidence,
    MemoryType,
    SecondBrainRecord,
    SourceAuthority,
)


@pytest.fixture
def fresh_store():
    """Create a temporary store directory for each test."""
    with tempfile.TemporaryDirectory() as tmpdir:
        s = AppendOnlyStore(tmpdir)
        yield s


class TestStoreInitializes:
    def test_supported_types_correct(self):
        assert MemoryType.RELATIONSHIP in SUPPORTED_TYPES
        assert MemoryType.DOCUMENT in SUPPORTED_TYPES
        assert len(SUPPORTED_TYPES) == 2

    def test_store_instantiates(self):
        # Just verify the module-level store can be imported without error.
        assert store is not None
        assert isinstance(store, AppendOnlyStore)


class TestAppendAndGetCurrent:
    def test_get_current_empty_returns_none(self, fresh_store):
        assert fresh_store.get_current("alice") is None

    def test_append_then_get_current(self, fresh_store):
        record = SecondBrainRecord(
            entity="alice",
            entity_type="person",
            memory_type=MemoryType.RELATIONSHIP,
            fact={"relationship": "sister", "related_to": "bob"},
            change_type=ChangeType.CREATED,
            confidence=Confidence.CONFIRMED,
            source_authority=SourceAuthority.SECOND_BRAIN,
        )
        fresh_store.append(record)

        result = fresh_store.get_current("alice")
        assert result is not None
        assert result.id == record.id
        assert result.entity == "alice"
        assert result.fact["relationship"] == "sister"


class TestUnionAllMerge:
    def test_union_all_merge_no_dedup(self, fresh_store):
        """UNION_ALL means every appended record is kept, even for the same entity."""
        r1 = SecondBrainRecord(
            entity="alice",
            entity_type="person",
            memory_type=MemoryType.RELATIONSHIP,
            fact={"relationship": "sister", "related_to": "bob"},
            change_type=ChangeType.CREATED,
        )
        r2 = SecondBrainRecord(
            entity="alice",
            entity_type="person",
            memory_type=MemoryType.RELATIONSHIP,
            fact={"relationship": "colleague", "related_to": "carol"},
            change_type=ChangeType.CREATED,
        )
        r3 = SecondBrainRecord(
            entity="alice",
            entity_type="person",
            memory_type=MemoryType.DOCUMENT,
            fact={"doc_id": "doc-001", "title": "Project Spec"},
            change_type=ChangeType.CREATED,
        )

        fresh_store.append(r1)
        fresh_store.append(r2)
        fresh_store.append(r3)

        # All three records present in history
        history = fresh_store.history("alice")
        assert len(history) == 3

        # scan() returns all records for entity (newest-first by default)
        scanned = fresh_store.scan(entity="alice", limit=10)
        assert len(scanned) == 3

    def test_multiple_entities_all_kept(self, fresh_store):
        """Records for different entities don't interfere."""
        r_alice = SecondBrainRecord(
            entity="alice",
            memory_type=MemoryType.RELATIONSHIP,
            fact={"name": "Alice"},
            change_type=ChangeType.CREATED,
        )
        r_bob = SecondBrainRecord(
            entity="bob",
            memory_type=MemoryType.RELATIONSHIP,
            fact={"name": "Bob"},
            change_type=ChangeType.CREATED,
        )
        r_carol = SecondBrainRecord(
            entity="carol",
            memory_type=MemoryType.DOCUMENT,
            fact={"doc_id": "doc-x"},
            change_type=ChangeType.CREATED,
        )

        fresh_store.append(r_alice)
        fresh_store.append(r_bob)
        fresh_store.append(r_carol)

        assert fresh_store.get_current("alice") is not None
        assert fresh_store.get_current("bob") is not None
        assert fresh_store.get_current("carol") is not None
        assert fresh_store.get_current("david") is None


class TestHistory:
    def test_history_empty_returns_empty_list(self, fresh_store):
        assert fresh_store.history("nobody") == []

    def test_history_returns_all_versions_oldest_first(self, fresh_store):
        ts1 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc).isoformat()
        ts2 = datetime(2026, 1, 2, 10, 0, 0, tzinfo=timezone.utc).isoformat()
        ts3 = datetime(2026, 1, 3, 10, 0, 0, tzinfo=timezone.utc).isoformat()

        r1 = SecondBrainRecord(
            entity="alice",
            memory_type=MemoryType.RELATIONSHIP,
            fact={"role": "intern"},
            change_type=ChangeType.CREATED,
            timestamp=ts1,
        )
        r2 = SecondBrainRecord(
            entity="alice",
            memory_type=MemoryType.RELATIONSHIP,
            fact={"role": "engineer"},
            change_type=ChangeType.UPDATED,
            timestamp=ts2,
        )
        r3 = SecondBrainRecord(
            entity="alice",
            memory_type=MemoryType.RELATIONSHIP,
            fact={"role": "senior engineer"},
            change_type=ChangeType.UPDATED,
            timestamp=ts3,
        )

        fresh_store.append(r1)
        fresh_store.append(r2)
        fresh_store.append(r3)

        hist = fresh_store.history("alice")
        assert len(hist) == 3
        # Oldest first
        assert hist[0].timestamp == ts1
        assert hist[1].timestamp == ts2
        assert hist[2].timestamp == ts3
        # Facts preserved
        assert hist[0].fact["role"] == "intern"
        assert hist[1].fact["role"] == "engineer"
        assert hist[2].fact["role"] == "senior engineer"

    def test_history_only_returns_matching_entity(self, fresh_store):
        r_alice = SecondBrainRecord(
            entity="alice",
            memory_type=MemoryType.RELATIONSHIP,
            fact={"name": "Alice"},
            change_type=ChangeType.CREATED,
        )
        r_bob = SecondBrainRecord(
            entity="bob",
            memory_type=MemoryType.RELATIONSHIP,
            fact={"name": "Bob"},
            change_type=ChangeType.CREATED,
        )
        fresh_store.append(r_alice)
        fresh_store.append(r_bob)

        hist_alice = fresh_store.history("alice")
        assert len(hist_alice) == 1
        assert hist_alice[0].entity == "alice"
