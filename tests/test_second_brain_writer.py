"""Tests for SecondBrainWriter."""
from __future__ import annotations

import json
import os
import tempfile
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from core.second_brain.types import (
    ChangeType,
    Confidence,
    MemoryType,
    SecondBrainRecord,
    SourceAuthority,
)
from core.second_brain.writer import SecondBrainWriter


class TestWriteReturnsRecordId:
    def test_write_returns_record_id(self, tmp_path: Any) -> None:
        writer = SecondBrainWriter(stores_base=str(tmp_path))
        record = SecondBrainRecord(
            entity="test-entity",
            memory_type=MemoryType.OPERATIONAL,
            fact={"key": "value"},
            changed_reason="test",
        )

        record_id = writer.write("operational", record)

        assert record_id == record.id
        assert record_id is not None
        # Verify it was actually written to disk
        store_dir = tmp_path / "operational"
        assert (store_dir / "records.jsonl").exists()


class TestUpdateCreatesSupersedesChain:
    def test_update_creates_supersedes_chain(self, tmp_path: Any) -> None:
        writer = SecondBrainWriter(stores_base=str(tmp_path))

        id1 = writer.update(
            store_name="operational",
            entity="server-1",
            fact={"status": "healthy"},
            memory_type=MemoryType.OPERATIONAL,
            changed_reason="initial",
        )

        id2 = writer.update(
            store_name="operational",
            entity="server-1",
            fact={"status": "degraded"},
            memory_type=MemoryType.OPERATIONAL,
            changed_reason="status changed",
        )

        assert id1 != id2

        # Read the JSONL and verify supersedes chain
        records_path = tmp_path / "operational" / "records.jsonl"
        records: dict[str, dict] = {}
        with open(records_path) as f:
            for line in f:
                r = json.loads(line.strip())
                records[r["id"]] = r

        # First record should have no supersedes
        assert records[id1].get("supersedes") is None
        # Second record should supersede the first
        assert records[id2].get("supersedes") == id1


class TestUpdateReadsCurrentViaStore:
    def test_update_reads_current_via_store(self, tmp_path: Any) -> None:
        writer = SecondBrainWriter(stores_base=str(tmp_path))

        # First update creates a record
        writer.update(
            store_name="operational",
            entity="server-2",
            fact={"v": "1"},
            memory_type=MemoryType.OPERATIONAL,
            changed_reason="v1",
        )

        # Spy on get_current
        store = writer._get_store("operational")
        spy = MagicMock(wraps=store.get_current)
        store.get_current = spy

        # Second update
        writer.update(
            store_name="operational",
            entity="server-2",
            fact={"v": "2"},
            memory_type=MemoryType.OPERATIONAL,
            changed_reason="v2",
        )

        spy.assert_called_once_with("server-2")


class TestUpdateSetsCorrectFields:
    def test_update_sets_correct_fields(self, tmp_path: Any) -> None:
        writer = SecondBrainWriter(stores_base=str(tmp_path))

        record_id = writer.update(
            store_name="operational",
            entity="my-entity",
            entity_type="server",
            fact={"status": "running", "ip": "192.168.1.1"},
            memory_type=MemoryType.OPERATIONAL,
            changed_reason="server registered",
            change_type=ChangeType.CREATED,
            confidence=Confidence.CONFIRMED,
            source_authority=SourceAuthority.LIVE_SYSTEM,
            ttl_seconds=3600,
            metadata={"region": "us-east"},
        )

        # Read back from JSONL
        records_path = tmp_path / "operational" / "records.jsonl"
        with open(records_path) as f:
            record = json.loads(f.readline().strip())

        assert record["entity"] == "my-entity"
        assert record["entity_type"] == "server"
        assert record["fact"] == {"status": "running", "ip": "192.168.1.1"}
        assert record["memory_type"] == "operational"
        assert record["change_type"] == "created"
        assert record["changed_reason"] == "server registered"
        assert record["confidence"] == "confirmed"
        assert record["source_authority"] == 1  # LIVE_SYSTEM
        assert record["ttl_seconds"] == 3600
        assert record["metadata"] == {"region": "us-east"}
        assert record["supersedes"] is None


class TestWriteToNonexistentStoreCreatesIt:
    def test_write_to_nonexistent_store_creates_it(self, tmp_path: Any) -> None:
        writer = SecondBrainWriter(stores_base=str(tmp_path))

        # Writing to a store that doesn't exist yet should succeed
        record = SecondBrainRecord(
            entity="e1",
            memory_type=MemoryType.OPERATIONAL,
            fact={"x": 1},
            changed_reason="init",
        )
        writer.write("brand_new_store", record)

        store_dir = tmp_path / "brand_new_store"
        assert store_dir.exists()
        assert (store_dir / "records.jsonl").exists()
        assert (store_dir / "current_index.json").exists()
        assert (store_dir / "manifest.json").exists()


class TestGetStoreLazyLoading:
    def test_get_store_lazy_loading(self, tmp_path: Any) -> None:
        writer = SecondBrainWriter(stores_base=str(tmp_path))

        # Before any write, _stores should be empty
        assert writer._stores == {}

        # After write, store should be cached
        record = SecondBrainRecord(
            entity="e1",
            memory_type=MemoryType.OPERATIONAL,
            fact={"y": 2},
            changed_reason="lazy test",
        )
        writer.write("lazy_store", record)

        assert "lazy_store" in writer._stores
        assert len(writer._stores) == 1

        # Second write to same store reuses cached instance
        record2 = SecondBrainRecord(
            entity="e2",
            memory_type=MemoryType.OPERATIONAL,
            fact={"y": 3},
            changed_reason="lazy test 2",
        )
        writer.write("lazy_store", record2)

        assert len(writer._stores) == 1
        assert writer._stores["lazy_store"] is writer._get_store("lazy_store")
