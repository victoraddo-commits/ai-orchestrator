"""Tests for the cognitive store (SEMANTIC, EPISODIC, TEMPORAL)."""
from __future__ import annotations

import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from core.second_brain.base_store import AppendOnlyStore
from core.second_brain.stores.cognitive import SUPPORTED_TYPES, store
from core.second_brain.types import (
    ChangeType,
    Confidence,
    MemoryType,
    SecondBrainRecord,
    SourceAuthority,
)


class TestCognitiveStoreInit:
    """Verify the cognitive store module initializes correctly."""

    def test_supported_types(self):
        """SUPPORTED_TYPES contains SEMANTIC, EPISODIC, TEMPORAL."""
        assert MemoryType.SEMANTIC in SUPPORTED_TYPES
        assert MemoryType.EPISODIC in SUPPORTED_TYPES
        assert MemoryType.TEMPORAL in SUPPORTED_TYPES
        assert len(SUPPORTED_TYPES) == 3

    def test_store_exists(self):
        """store is an AppendOnlyStore pointing at the cognitive directory."""
        assert isinstance(store, AppendOnlyStore)
        assert "cognitive" in store.store_dir


class TestAppendAndGetCurrent:
    """Basic append + get_current operations."""

    def test_append_and_get_current(self, tmp_cognitive_store):
        """Appending a record and retrieving it via get_current returns the same record."""
        record = SecondBrainRecord(
            entity="test-entity-1",
            entity_type="fact",
            memory_type=MemoryType.SEMANTIC,
            fact={"content": "Python is a programming language."},
            change_type=ChangeType.CREATED,
            confidence=Confidence.CONFIRMED,
            source_authority=SourceAuthority.DOCUMENTATION,
        )
        tmp_cognitive_store.append(record)

        retrieved = tmp_cognitive_store.get_current("test-entity-1")
        assert retrieved is not None
        assert retrieved.id == record.id
        assert retrieved.entity == "test-entity-1"
        assert retrieved.fact["content"] == "Python is a programming language."

    def test_get_current_nonexistent(self, tmp_cognitive_store):
        """get_current returns None for an entity that was never stored."""
        assert tmp_cognitive_store.get_current("never-written") is None


class TestScanByMemoryType:
    """Filter records by memory_type via scan()."""

    def test_scan_semantic_only(self, tmp_cognitive_store):
        """scan(memory_type=SEMANTIC) returns only semantic records."""
        sem_record = SecondBrainRecord(
            entity="fact-1",
            entity_type="fact",
            memory_type=MemoryType.SEMANTIC,
            fact={"content": "Water boils at 100C."},
            change_type=ChangeType.CREATED,
        )
        epi_record = SecondBrainRecord(
            entity="event-1",
            entity_type="event",
            memory_type=MemoryType.EPISODIC,
            fact={"content": "System outage occurred."},
            change_type=ChangeType.CREATED,
        )
        tmp_cognitive_store.append(sem_record)
        tmp_cognitive_store.append(epi_record)

        semantic_results = tmp_cognitive_store.scan(memory_type=MemoryType.SEMANTIC)
        episodic_results = tmp_cognitive_store.scan(memory_type=MemoryType.EPISODIC)

        assert len(semantic_results) == 1
        assert semantic_results[0].entity == "fact-1"
        assert semantic_results[0].memory_type == MemoryType.SEMANTIC

        assert len(episodic_results) == 1
        assert episodic_results[0].entity == "event-1"
        assert episodic_results[0].memory_type == MemoryType.EPISODIC

    def test_scan_mixed_memory_types(self, tmp_cognitive_store):
        """Records of all three cognitive types coexist in the same store."""
        records = [
            SecondBrainRecord(
                entity="semantic-fact",
                memory_type=MemoryType.SEMANTIC,
                fact={"content": "Earth orbits the Sun."},
                change_type=ChangeType.CREATED,
            ),
            SecondBrainRecord(
                entity="episodic-event",
                memory_type=MemoryType.EPISODIC,
                fact={"content": "Server crashed at 3am."},
                change_type=ChangeType.CREATED,
            ),
            SecondBrainRecord(
                entity="temporal-pattern",
                memory_type=MemoryType.TEMPORAL,
                fact={"content": "Usage peaks on Mondays."},
                change_type=ChangeType.CREATED,
            ),
        ]
        for r in records:
            tmp_cognitive_store.append(r)

        all_results = tmp_cognitive_store.scan()
        assert len(all_results) == 3

        types_found = {r.memory_type for r in all_results}
        assert MemoryType.SEMANTIC in types_found
        assert MemoryType.EPISODIC in types_found
        assert MemoryType.TEMPORAL in types_found


class TestScanTimeRange:
    """Filter records by ISO timestamp range via scan()."""

    def test_scan_time_range_filters_correctly(self, tmp_cognitive_store):
        """scan() excludes records whose timestamp falls outside the range."""
        base_time = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)

        old_record = SecondBrainRecord(
            entity="old-fact",
            memory_type=MemoryType.SEMANTIC,
            fact={"content": "Old fact."},
            change_type=ChangeType.CREATED,
            timestamp=(base_time - timedelta(days=10)).isoformat(),
        )
        middle_record = SecondBrainRecord(
            entity="middle-fact",
            memory_type=MemoryType.SEMANTIC,
            fact={"content": "Middle fact."},
            change_type=ChangeType.CREATED,
            timestamp=(base_time - timedelta(days=5)).isoformat(),
        )
        new_record = SecondBrainRecord(
            entity="new-fact",
            memory_type=MemoryType.SEMANTIC,
            fact={"content": "New fact."},
            change_type=ChangeType.CREATED,
            timestamp=(base_time + timedelta(days=1)).isoformat(),
        )

        for r in [old_record, middle_record, new_record]:
            tmp_cognitive_store.append(r)

        # Query only within a 7-day window around the middle record
        start = (base_time - timedelta(days=7)).isoformat()
        end = (base_time + timedelta(days=2)).isoformat()
        results = tmp_cognitive_store.scan(
            memory_type=MemoryType.SEMANTIC,
            time_range=(start, end),
        )

        assert len(results) == 2
        entities = {r.entity for r in results}
        assert "middle-fact" in entities
        assert "new-fact" in entities
        assert "old-fact" not in entities

    def test_scan_time_range_boundary(self, tmp_cognitive_store):
        """Records with timestamps exactly at start or end are included."""
        base_time = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)

        boundary_record = SecondBrainRecord(
            entity="boundary-fact",
            memory_type=MemoryType.TEMPORAL,
            fact={"content": "At the boundary."},
            change_type=ChangeType.CREATED,
            timestamp=base_time.isoformat(),
        )
        tmp_cognitive_store.append(boundary_record)

        # Query with range that includes the exact boundary timestamp
        results = tmp_cognitive_store.scan(
            memory_type=MemoryType.TEMPORAL,
            time_range=(base_time.isoformat(), base_time.isoformat()),
        )
        assert len(results) == 1
        assert results[0].entity == "boundary-fact"


class TestHistory:
    """Multiple versions of the same entity via history()."""

    def test_history_returns_all_versions(self, tmp_cognitive_store):
        """history() returns every stored version of an entity, oldest-first."""
        base_time = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)

        v1 = SecondBrainRecord(
            entity="server-status",
            entity_type="status",
            memory_type=MemoryType.EPISODIC,
            fact={"status": "healthy"},
            change_type=ChangeType.CREATED,
            timestamp=base_time.isoformat(),
        )
        v2 = SecondBrainRecord(
            entity="server-status",
            entity_type="status",
            memory_type=MemoryType.EPISODIC,
            fact={"status": "degraded"},
            change_type=ChangeType.UPDATED,
            supersedes=v1.id,
            timestamp=(base_time + timedelta(hours=1)).isoformat(),
        )
        v3 = SecondBrainRecord(
            entity="server-status",
            entity_type="status",
            memory_type=MemoryType.EPISODIC,
            fact={"status": "recovered"},
            change_type=ChangeType.RECOVERED,
            supersedes=v2.id,
            timestamp=(base_time + timedelta(hours=2)).isoformat(),
        )

        for r in [v1, v2, v3]:
            tmp_cognitive_store.append(r)

        hist = tmp_cognitive_store.history("server-status")
        assert len(hist) == 3
        # Must be oldest-first
        assert hist[0].fact["status"] == "healthy"
        assert hist[1].fact["status"] == "degraded"
        assert hist[2].fact["status"] == "recovered"

    def test_history_empty_for_unknown_entity(self, tmp_cognitive_store):
        """history() returns an empty list for an entity that was never written."""
        assert tmp_cognitive_store.history("unknown-entity") == []

    def test_history_with_different_memory_types(self, tmp_cognitive_store):
        """history() returns all versions regardless of memory_type — same entity key."""
        base_time = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)

        sem_record = SecondBrainRecord(
            entity="knowledge-graph",
            memory_type=MemoryType.SEMANTIC,
            fact={"content": "Initial fact."},
            change_type=ChangeType.CREATED,
            timestamp=base_time.isoformat(),
        )
        epi_record = SecondBrainRecord(
            entity="knowledge-graph",
            memory_type=MemoryType.EPISODIC,
            fact={"content": "Learned via event."},
            change_type=ChangeType.CREATED,
            timestamp=(base_time + timedelta(hours=1)).isoformat(),
        )

        tmp_cognitive_store.append(sem_record)
        tmp_cognitive_store.append(epi_record)

        hist = tmp_cognitive_store.history("knowledge-graph")
        assert len(hist) == 2
        types = {r.memory_type for r in hist}
        assert types == {MemoryType.SEMANTIC, MemoryType.EPISODIC}


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_cognitive_store():
    """Create a temporary cognitive store on disk, torn down after each test."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_dir = os.path.join(tmpdir, "cognitive")
        os.makedirs(store_dir)
        # Write the manifest so AppendOnlyStore doesn't overwrite with the default
        import json

        with open(os.path.join(store_dir, "manifest.json"), "w") as f:
            json.dump(
                {
                    "schema_version": 1,
                    "store_name": "cognitive",
                    "memory_types": ["SEMANTIC", "EPISODIC", "TEMPORAL"],
                    "merge_policy": "SOURCE_AUTHORITY",
                    "description": "Semantic facts, episodic events, temporal knowledge",
                },
                f,
            )
        with open(os.path.join(store_dir, "current_index.json"), "w") as f:
            json.dump({}, f)
        with open(os.path.join(store_dir, "records.jsonl"), "w") as f:
            pass  # empty file

        tmp_store = AppendOnlyStore(store_dir)
        yield tmp_store
