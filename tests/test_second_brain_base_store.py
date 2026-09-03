"""Tests for AppendOnlyStore."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from core.second_brain.base_store import AppendOnlyStore
from core.second_brain.types import (
    MemoryType,
    ChangeType,
    Confidence,
    SourceAuthority,
    SecondBrainRecord,
)


@pytest.fixture
def store_dir():
    """Create a temporary store directory, clean up after."""
    tmp = tempfile.mkdtemp()
    yield tmp
    shutil.rmtree(tmp)


@pytest.fixture
def store(store_dir):
    """Create an AppendOnlyStore on the temp directory."""
    return AppendOnlyStore(store_dir)


class TestEnsureExists:
    def test_creates_store_dir(self, store_dir):
        subdir = os.path.join(store_dir, "sub", "nested")
        AppendOnlyStore(subdir)
        assert os.path.isdir(subdir)

    def test_creates_manifest(self, store_dir):
        AppendOnlyStore(store_dir)
        manifest_file = os.path.join(store_dir, "manifest.json")
        assert os.path.exists(manifest_file)
        with open(manifest_file) as f:
            manifest = json.load(f)
        assert manifest["schema_version"] == 1
        assert manifest["record_count"] == 0
        assert manifest["merge_policy"] == "newest_wins"

    def test_creates_current_index(self, store_dir):
        AppendOnlyStore(store_dir)
        idx_file = os.path.join(store_dir, "current_index.json")
        assert os.path.exists(idx_file)
        with open(idx_file) as f:
            idx = json.load(f)
        assert idx == {}

    def test_creates_records_file(self, store_dir):
        AppendOnlyStore(store_dir)
        rec_file = os.path.join(store_dir, "records.jsonl")
        assert os.path.exists(rec_file)

    def test_idempotent(self, store):
        # Calling twice should not raise
        store.ensure_exists()
        store.ensure_exists()


class TestAppend:
    def test_append_writes_jsonl(self, store):
        record = SecondBrainRecord(
            entity="entity-1",
            entity_type="test",
            memory_type=MemoryType.OPERATIONAL,
            fact={"key": "value"},
        )
        store.append(record)

        with open(store.records_file) as f:
            lines = [l for l in f if l.strip()]
        assert len(lines) == 1
        rec = SecondBrainRecord.from_dict(json.loads(lines[0]))
        assert rec.id == record.id
        assert rec.entity == "entity-1"

    def test_append_updates_index(self, store):
        record = SecondBrainRecord(
            entity="entity-1",
            entity_type="test",
            memory_type=MemoryType.OPERATIONAL,
        )
        store.append(record)

        index = store._read_index()
        assert index["entity-1"] == record.id

    def test_append_increments_manifest_count(self, store):
        record = SecondBrainRecord(entity="e1", entity_type="t")
        store.append(record)
        manifest = store._read_manifest()
        assert manifest["record_count"] == 1

        record2 = SecondBrainRecord(entity="e2", entity_type="t")
        store.append(record2)
        manifest = store._read_manifest()
        assert manifest["record_count"] == 2

    def test_multiple_entities_separate_index_entries(self, store):
        r1 = SecondBrainRecord(entity="a", entity_type="t")
        r2 = SecondBrainRecord(entity="b", entity_type="t")
        store.append(r1)
        store.append(r2)

        index = store._read_index()
        assert index["a"] == r1.id
        assert index["b"] == r2.id

    def test_same_entity_multiple_records(self, store):
        now = datetime.now(timezone.utc)
        ts1 = now.isoformat()
        ts2 = (now + timedelta(seconds=1)).isoformat()
        ts3 = (now + timedelta(seconds=2)).isoformat()

        r1 = SecondBrainRecord(id="id1", entity="same", entity_type="t", timestamp=ts1)
        r2 = SecondBrainRecord(id="id2", entity="same", entity_type="t", timestamp=ts2)
        r3 = SecondBrainRecord(id="id3", entity="same", entity_type="t", timestamp=ts3)
        store.append(r1)
        store.append(r2)
        store.append(r3)

        index = store._read_index()
        # Latest timestamp should be in index
        assert index["same"] == "id3"


class TestGetCurrent:
    def test_returns_record_for_known_entity(self, store):
        record = SecondBrainRecord(
            entity="entity-1",
            entity_type="test",
            memory_type=MemoryType.OPERATIONAL,
            fact={"key": "value"},
        )
        store.append(record)

        result = store.get_current("entity-1")
        assert result is not None
        assert result.id == record.id
        assert result.entity == "entity-1"
        assert result.fact == {"key": "value"}

    def test_returns_none_for_unknown_entity(self, store):
        result = store.get_current("nonexistent")
        assert result is None

    def test_returns_latest_for_entity(self, store):
        now = datetime.now(timezone.utc)
        ts1 = now.isoformat()
        ts2 = (now + timedelta(seconds=1)).isoformat()

        r1 = SecondBrainRecord(id="id1", entity="same", timestamp=ts1, fact={"v": 1})
        r2 = SecondBrainRecord(id="id2", entity="same", timestamp=ts2, fact={"v": 2})
        store.append(r1)
        store.append(r2)

        result = store.get_current("same")
        assert result is not None
        assert result.id == "id2"
        assert result.fact == {"v": 2}


class TestScan:
    def setup_method(self):
        self._now = datetime.now(timezone.utc)

    def _ts(self, offset_seconds: int) -> str:
        return (self._now + timedelta(seconds=offset_seconds)).replace(microsecond=0).isoformat()

    def test_scan_returns_all_records_newest_first(self, store):
        r1 = SecondBrainRecord(entity="e1", memory_type=MemoryType.OPERATIONAL, timestamp=self._ts(0))
        r2 = SecondBrainRecord(entity="e2", memory_type=MemoryType.OPERATIONAL, timestamp=self._ts(1))
        r3 = SecondBrainRecord(entity="e3", memory_type=MemoryType.OPERATIONAL, timestamp=self._ts(2))
        store.append(r1)
        store.append(r2)
        store.append(r3)

        results = store.scan()
        assert len(results) == 3
        assert results[0].id == r3.id  # newest first
        assert results[1].id == r2.id
        assert results[2].id == r1.id

    def test_scan_filters_by_memory_type(self, store):
        r1 = SecondBrainRecord(entity="e1", memory_type=MemoryType.OPERATIONAL, timestamp=self._ts(0))
        r2 = SecondBrainRecord(entity="e2", memory_type=MemoryType.INCIDENT, timestamp=self._ts(1))
        r3 = SecondBrainRecord(entity="e3", memory_type=MemoryType.OPERATIONAL, timestamp=self._ts(2))
        store.append(r1)
        store.append(r2)
        store.append(r3)

        results = store.scan(memory_type=MemoryType.OPERATIONAL)
        assert len(results) == 2
        assert all(r.memory_type == MemoryType.OPERATIONAL for r in results)

    def test_scan_filters_by_entity(self, store):
        r1 = SecondBrainRecord(entity="target", memory_type=MemoryType.OPERATIONAL, timestamp=self._ts(0))
        r2 = SecondBrainRecord(entity="other", memory_type=MemoryType.OPERATIONAL, timestamp=self._ts(1))
        store.append(r1)
        store.append(r2)

        results = store.scan(entity="target")
        assert len(results) == 1
        assert results[0].entity == "target"

    def test_scan_filters_by_time_range(self, store):
        r1 = SecondBrainRecord(entity="e", memory_type=MemoryType.OPERATIONAL, timestamp=self._ts(0))
        r2 = SecondBrainRecord(entity="e", memory_type=MemoryType.OPERATIONAL, timestamp=self._ts(10))
        r3 = SecondBrainRecord(entity="e", memory_type=MemoryType.OPERATIONAL, timestamp=self._ts(20))
        store.append(r1)
        store.append(r2)
        store.append(r3)

        start = self._ts(5)
        end = self._ts(15)
        results = store.scan(time_range=(start, end))
        assert len(results) == 1
        assert results[0].id == r2.id

    def test_scan_respects_limit(self, store):
        for i in range(10):
            store.append(
                SecondBrainRecord(
                    entity=f"e{i}",
                    memory_type=MemoryType.OPERATIONAL,
                    timestamp=self._ts(i),
                )
            )

        results = store.scan(limit=3)
        assert len(results) == 3

    def test_scan_empty_store(self, store):
        results = store.scan()
        assert results == []


class TestHistory:
    def setup_method(self):
        self._now = datetime.now(timezone.utc)

    def _ts(self, offset_seconds: int) -> str:
        return (self._now + timedelta(seconds=offset_seconds)).replace(microsecond=0).isoformat()

    def test_history_returns_all_versions_oldest_first(self, store):
        r1 = SecondBrainRecord(id="id1", entity="same", timestamp=self._ts(0))
        r2 = SecondBrainRecord(id="id2", entity="same", timestamp=self._ts(1))
        r3 = SecondBrainRecord(id="id3", entity="same", timestamp=self._ts(2))
        store.append(r1)
        store.append(r2)
        store.append(r3)

        results = store.history("same")
        assert len(results) == 3
        assert [r.id for r in results] == ["id1", "id2", "id3"]  # oldest first

    def test_history_returns_empty_for_unknown_entity(self, store):
        results = store.history("nonexistent")
        assert results == []

    def test_history_only_returns_matching_entity(self, store):
        r1 = SecondBrainRecord(id="id1", entity="target", timestamp=self._ts(0))
        r2 = SecondBrainRecord(id="id2", entity="other", timestamp=self._ts(1))
        store.append(r1)
        store.append(r2)

        results = store.history("target")
        assert len(results) == 1
        assert results[0].id == "id1"


class TestRebuildIndex:
    def setup_method(self):
        self._now = datetime.now(timezone.utc)

    def _ts(self, offset_seconds: int) -> str:
        return (self._now + timedelta(seconds=offset_seconds)).replace(microsecond=0).isoformat()

    def test_rebuild_index_reconstructs_from_jsonl(self, store):
        r1 = SecondBrainRecord(id="id1", entity="e1", timestamp=self._ts(0))
        r2 = SecondBrainRecord(id="id2", entity="e2", timestamp=self._ts(1))
        store.append(r1)
        store.append(r2)

        # Simulate missing/corrupt index
        store._write_index({})

        store._rebuild_index()
        index = store._read_index()
        assert index["e1"] == "id1"
        assert index["e2"] == "id2"

    def test_rebuild_index_keeps_latest_per_entity(self, store):
        r1 = SecondBrainRecord(id="id1", entity="same", timestamp=self._ts(0))
        r2 = SecondBrainRecord(id="id2", entity="same", timestamp=self._ts(1))
        store.append(r1)
        store.append(r2)

        store._write_index({})
        store._rebuild_index()

        index = store._read_index()
        assert index["same"] == "id2"


class TestMarkSuperseded:
    def test_mark_superseded_is_noop(self, store):
        record = SecondBrainRecord(entity="e1", entity_type="t")
        store.append(record)

        # Should not raise
        store._mark_superseded(record.id, "new-id")

        # Record should still be findable
        assert store.get_current("e1") is not None
