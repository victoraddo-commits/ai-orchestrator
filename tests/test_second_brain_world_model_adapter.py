"""Tests for world_model_adapter — mirrors core/world_model.py writes to cognitive store."""
from __future__ import annotations

import json

import pytest

from core.second_brain.types import ChangeType, Confidence, MemoryType, SourceAuthority
from core.second_brain.stores.cognitive.adapters.world_model_adapter import (
    _get_writer,
    sync_semantic_to_second_brain,
    sync_episodic_to_second_brain,
)


class TestSyncSemanticWritesRecord:
    def test_sync_semantic_writes_record(self, tmp_path: pytest.fixture) -> None:
        """Calling sync_semantic_to_second_brain creates a semantic record in the cognitive store."""
        from core.second_brain.writer import SecondBrainWriter

        writer = SecondBrainWriter(stores_base=str(tmp_path))

        import core.second_brain.stores.cognitive.adapters.world_model_adapter as adapter

        original_writer = adapter._writer
        adapter._writer = writer

        try:
            record_id = sync_semantic_to_second_brain(
                statement="Kai is an autonomous infrastructure operations platform.",
                category="identity",
                tags=["kai", "identity", "platform"],
                confirmed_by="world_model",
            )
            assert record_id is not None

            store_dir = tmp_path / "cognitive"
            records_file = store_dir / "records.jsonl"
            assert records_file.exists()

            with open(records_file) as f:
                lines = f.readlines()
            assert len(lines) == 1

            record = json.loads(lines[0])
            assert record["entity"] == "Kai is an autonomous infrastructure operations platform."[:256]
            assert record["entity_type"] == "semantic"
            assert record["memory_type"] == MemoryType.SEMANTIC.value
            assert record["fact"]["statement"] == "Kai is an autonomous infrastructure operations platform."
            assert record["fact"]["category"] == "identity"
            assert record["fact"]["tags"] == ["kai", "identity", "platform"]
            assert record["fact"]["confirmed_by"] == "world_model"
            assert record["changed_reason"] == "semantic_knowledge_sync"
            assert record["change_type"] == ChangeType.CREATED.value
            assert record["confidence"] == Confidence.DOCUMENTED.value
            assert record["source_authority"] == SourceAuthority.SECOND_BRAIN.value
        finally:
            adapter._writer = original_writer


class TestSyncEpisodicWritesRecord:
    def test_sync_episodic_writes_record(self, tmp_path: pytest.fixture) -> None:
        """Calling sync_episodic_to_second_brain creates an episodic record in the cognitive store."""
        from core.second_brain.writer import SecondBrainWriter

        writer = SecondBrainWriter(stores_base=str(tmp_path))

        import core.second_brain.stores.cognitive.adapters.world_model_adapter as adapter

        original_writer = adapter._writer
        adapter._writer = writer

        try:
            record_id = sync_episodic_to_second_brain(
                event_type="incident_resolved",
                description="Proxmox B recovered after network outage.",
                participants=["kai", "operator"],
                location="proxmox-b",
            )
            assert record_id is not None

            store_dir = tmp_path / "cognitive"
            records_file = store_dir / "records.jsonl"
            assert records_file.exists()

            with open(records_file) as f:
                lines = f.readlines()
            assert len(lines) == 1

            record = json.loads(lines[0])
            assert record["entity"] == "incident_resolved"
            assert record["entity_type"] == "episodic"
            assert record["memory_type"] == MemoryType.EPISODIC.value
            assert record["fact"]["event_type"] == "incident_resolved"
            assert record["fact"]["description"] == "Proxmox B recovered after network outage."
            assert record["fact"]["participants"] == ["kai", "operator"]
            assert record["fact"]["location"] == "proxmox-b"
            assert record["changed_reason"] == "episodic_event_sync"
            assert record["change_type"] == ChangeType.CREATED.value
            assert record["confidence"] == Confidence.CONFIRMED.value
            assert record["source_authority"] == SourceAuthority.SECOND_BRAIN.value
        finally:
            adapter._writer = original_writer
