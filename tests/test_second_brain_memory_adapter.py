"""Tests for memory_adapter — mirrors core/memory.py writes to operational store."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest

from core.second_brain.types import ChangeType, MemoryType
from core.second_brain.stores.operational.adapters.memory_adapter import (
    _get_writer,
    sync_to_second_brain,
)


class TestSyncWritesToOperationalStore:
    def test_sync_writes_to_operational_store(self, tmp_path: Any) -> None:
        """Calling sync_to_second_brain creates a record in the operational store."""
        from core.second_brain.writer import SecondBrainWriter

        writer = SecondBrainWriter(stores_base=str(tmp_path))

        # Patch the writer singleton so we use tmp_path
        import core.second_brain.stores.operational.adapters.memory_adapter as adapter

        original_writer = adapter._writer
        adapter._writer = writer

        try:
            sync_to_second_brain(
                name="test-entity",
                data={"entity": "test-entity", "status": "ok"},
            )

            store_dir = tmp_path / "operational"
            records_file = store_dir / "records.jsonl"
            assert records_file.exists()

            with open(records_file) as f:
                lines = f.readlines()
            assert len(lines) == 1

            record = json.loads(lines[0])
            assert record["entity"] == "test-entity"
            assert record["fact"]["status"] == "ok"
        finally:
            adapter._writer = original_writer


class TestSyncMapsEntityTypeToMemoryType:
    @pytest.mark.parametrize(
        "entity_type,expected_memory_type",
        [
            ("service", MemoryType.INFRASTRUCTURE),
            ("host", MemoryType.INFRASTRUCTURE),
            ("container", MemoryType.INFRASTRUCTURE),
            ("deployment", MemoryType.INFRASTRUCTURE),
            ("incident", MemoryType.INCIDENT),
            ("decision", MemoryType.DECISION),
        ],
    )
    def test_sync_maps_entity_type_to_memory_type(
        self,
        tmp_path: Any,
        entity_type: str,
        expected_memory_type: MemoryType,
    ) -> None:
        """Known entity types map to the correct MemoryType."""
        from core.second_brain.writer import SecondBrainWriter

        writer = SecondBrainWriter(stores_base=str(tmp_path))
        import core.second_brain.stores.operational.adapters.memory_adapter as adapter

        original_writer = adapter._writer
        adapter._writer = writer

        try:
            sync_to_second_brain(
                name="test-entity",
                data={"entity_type": entity_type, "entity": "test-entity"},
            )

            store_dir = tmp_path / "operational"
            records_file = store_dir / "records.jsonl"

            with open(records_file) as f:
                lines = f.readlines()
            assert len(lines) == 1

            record = json.loads(lines[0])
            assert record["memory_type"] == expected_memory_type.value
        finally:
            adapter._writer = original_writer

    def test_sync_unknown_entity_type_defaults_to_operational(self, tmp_path: Any) -> None:
        """Unknown entity_type falls back to OPERATIONAL."""
        from core.second_brain.writer import SecondBrainWriter

        writer = SecondBrainWriter(stores_base=str(tmp_path))
        import core.second_brain.stores.operational.adapters.memory_adapter as adapter

        original_writer = adapter._writer
        adapter._writer = writer

        try:
            sync_to_second_brain(
                name="unknown-entity",
                data={"entity_type": "some_weird_type", "entity": "unknown-entity"},
            )

            store_dir = tmp_path / "operational"
            records_file = store_dir / "records.jsonl"

            with open(records_file) as f:
                lines = f.readlines()
            record = json.loads(lines[0])
            assert record["memory_type"] == MemoryType.OPERATIONAL.value
        finally:
            adapter._writer = original_writer


class TestSyncUsesEntityFromDataOrName:
    def test_sync_entity_from_data_takes_precedence(self, tmp_path: Any) -> None:
        """When data contains 'entity', it overrides the name parameter."""
        from core.second_brain.writer import SecondBrainWriter

        writer = SecondBrainWriter(stores_base=str(tmp_path))
        import core.second_brain.stores.operational.adapters.memory_adapter as adapter

        original_writer = adapter._writer
        adapter._writer = writer

        try:
            sync_to_second_brain(
                name="name-param",
                data={"entity": "data-entity", "entity_type": "service"},
            )

            store_dir = tmp_path / "operational"
            records_file = store_dir / "records.jsonl"

            with open(records_file) as f:
                lines = f.readlines()
            record = json.loads(lines[0])
            assert record["entity"] == "data-entity"
        finally:
            adapter._writer = original_writer

    def test_sync_name_used_when_no_entity_in_data(self, tmp_path: Any) -> None:
        """When data has no 'entity' key, name parameter becomes entity."""
        from core.second_brain.writer import SecondBrainWriter

        writer = SecondBrainWriter(stores_base=str(tmp_path))
        import core.second_brain.stores.operational.adapters.memory_adapter as adapter

        original_writer = adapter._writer
        adapter._writer = writer

        try:
            sync_to_second_brain(
                name="fallback-name",
                data={"entity_type": "service"},
            )

            store_dir = tmp_path / "operational"
            records_file = store_dir / "records.jsonl"

            with open(records_file) as f:
                lines = f.readlines()
            record = json.loads(lines[0])
            assert record["entity"] == "fallback-name"
        finally:
            adapter._writer = original_writer


class TestSyncIsIdempotentForSameFact:
    def test_sync_same_fact_twice_creates_supersedes_chain(
        self, tmp_path: Any
    ) -> None:
        """Two writes for same entity create a supersedes chain, two records."""
        from core.second_brain.writer import SecondBrainWriter

        writer = SecondBrainWriter(stores_base=str(tmp_path))
        import core.second_brain.stores.operational.adapters.memory_adapter as adapter

        original_writer = adapter._writer
        adapter._writer = writer

        try:
            sync_to_second_brain(
                name="server-1",
                data={"entity": "server-1", "entity_type": "host", "status": "healthy"},
                change_type=ChangeType.CREATED,
            )

            sync_to_second_brain(
                name="server-1",
                data={"entity": "server-1", "entity_type": "host", "status": "degraded"},
                change_type=ChangeType.UPDATED,
            )

            store_dir = tmp_path / "operational"
            records_file = store_dir / "records.jsonl"

            with open(records_file) as f:
                lines = f.readlines()

            assert len(lines) == 2

            first_record = json.loads(lines[0])
            second_record = json.loads(lines[1])

            # New record supersedes old record
            assert second_record["supersedes"] == first_record["id"]
            # Old record's superseded_by is not backfilled (append-only semantics)
            assert first_record["superseded_by"] is None
            assert first_record["fact"]["status"] == "healthy"
            assert second_record["fact"]["status"] == "degraded"
        finally:
            adapter._writer = original_writer
