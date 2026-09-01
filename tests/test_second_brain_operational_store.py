"""Tests for the operational store."""
from __future__ import annotations

import json
import os
import tempfile
import shutil
from datetime import datetime, timedelta, timezone

import pytest

from core.second_brain.base_store import AppendOnlyStore
from core.second_brain.stores.operational import store, SUPPORTED_TYPES
from core.second_brain.types import (
    MemoryType,
    ChangeType,
    Confidence,
    SourceAuthority,
    SecondBrainRecord,
)


@pytest.fixture
def store_dir():
    """Create a temporary operational store directory, clean up after."""
    tmp = tempfile.mkdtemp()
    yield tmp
    shutil.rmtree(tmp)


@pytest.fixture
def operational_store(store_dir):
    """Create an AppendOnlyStore on the temp directory."""
    return AppendOnlyStore(store_dir)


class TestStoreInitializes:
    def test_store_exists(self):
        from core.second_brain.stores.operational import store
        assert store is not None

    def test_supported_types_correct(self):
        assert MemoryType.INFRASTRUCTURE in SUPPORTED_TYPES
        assert MemoryType.INCIDENT in SUPPORTED_TYPES
        assert MemoryType.OPERATIONAL in SUPPORTED_TYPES
        assert MemoryType.DECISION in SUPPORTED_TYPES
        assert len(SUPPORTED_TYPES) == 4


class TestAppendAndGetCurrent:
    def test_append_and_get_current(self, operational_store):
        record = SecondBrainRecord(
            entity="server-1",
            entity_type="server",
            memory_type=MemoryType.INFRASTRUCTURE,
            fact={"hostname": "server-1", "status": "online"},
            change_type=ChangeType.CREATED,
            confidence=Confidence.CONFIRMED,
            source_authority=SourceAuthority.LIVE_SYSTEM,
        )
        operational_store.append(record)
        result = operational_store.get_current("server-1")
        assert result is not None
        assert result.entity == "server-1"
        assert result.fact["hostname"] == "server-1"
        assert result.fact["status"] == "online"


class TestSupersedesChain:
    def test_supersedes_chain(self, operational_store):
        record1 = SecondBrainRecord(
            entity="server-1",
            entity_type="server",
            memory_type=MemoryType.INFRASTRUCTURE,
            fact={"status": "online"},
            change_type=ChangeType.CREATED,
        )
        operational_store.append(record1)

        record2 = SecondBrainRecord(
            entity="server-1",
            entity_type="server",
            memory_type=MemoryType.INFRASTRUCTURE,
            fact={"status": "degraded"},
            change_type=ChangeType.UPDATED,
            supersedes=record1.id,
        )
        operational_store.append(record2)

        # get_current should return the newer record
        current = operational_store.get_current("server-1")
        assert current is not None
        assert current.fact["status"] == "degraded"
        assert current.supersedes == record1.id

        # history should return both, oldest first
        hist = operational_store.history("server-1")
        assert len(hist) == 2
        assert hist[0].fact["status"] == "online"
        assert hist[1].fact["status"] == "degraded"


class TestScanByMemoryType:
    def test_scan_by_memory_type(self, operational_store):
        record_infra = SecondBrainRecord(
            entity="host-a",
            entity_type="host",
            memory_type=MemoryType.INFRASTRUCTURE,
            fact={"type": "physical"},
        )
        record_incident = SecondBrainRecord(
            entity="incident-1",
            entity_type="incident",
            memory_type=MemoryType.INCIDENT,
            fact={"severity": "high"},
        )
        record_operational = SecondBrainRecord(
            entity="svc-1",
            entity_type="service",
            memory_type=MemoryType.OPERATIONAL,
            fact={"up": True},
        )
        operational_store.append(record_infra)
        operational_store.append(record_incident)
        operational_store.append(record_operational)

        infra_results = operational_store.scan(memory_type=MemoryType.INFRASTRUCTURE)
        assert len(infra_results) == 1
        assert infra_results[0].entity == "host-a"

        incident_results = operational_store.scan(memory_type=MemoryType.INCIDENT)
        assert len(incident_results) == 1
        assert incident_results[0].entity == "incident-1"


class TestScanByEntity:
    def test_scan_by_entity(self, operational_store):
        record_a1 = SecondBrainRecord(
            entity="host-x",
            memory_type=MemoryType.INFRASTRUCTURE,
            fact={"v": 1},
        )
        record_a2 = SecondBrainRecord(
            entity="host-x",
            memory_type=MemoryType.INFRASTRUCTURE,
            fact={"v": 2},
        )
        record_b = SecondBrainRecord(
            entity="host-y",
            memory_type=MemoryType.INFRASTRUCTURE,
            fact={"v": 1},
        )
        operational_store.append(record_a1)
        operational_store.append(record_a2)
        operational_store.append(record_b)

        results = operational_store.scan(entity="host-x")
        assert len(results) == 2


class TestScanTimeRange:
    def test_scan_time_range(self, operational_store):
        base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        record_old = SecondBrainRecord(
            entity="svc-old",
            memory_type=MemoryType.OPERATIONAL,
            fact={"name": "old"},
            timestamp=base.isoformat(),
        )
        record_new = SecondBrainRecord(
            entity="svc-new",
            memory_type=MemoryType.OPERATIONAL,
            fact={"name": "new"},
            timestamp=(base + timedelta(hours=2)).isoformat(),
        )
        operational_store.append(record_old)
        operational_store.append(record_new)

        results = operational_store.scan(
            time_range=(base.isoformat(), (base + timedelta(hours=1)).isoformat())
        )
        assert len(results) == 1
        assert results[0].entity == "svc-old"


class TestHistory:
    def test_history(self, operational_store):
        r1 = SecondBrainRecord(
            entity="entity-1",
            memory_type=MemoryType.DECISION,
            fact={"step": 1},
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat(),
        )
        r2 = SecondBrainRecord(
            entity="entity-1",
            memory_type=MemoryType.DECISION,
            fact={"step": 2},
            supersedes=r1.id,
            timestamp=datetime(2026, 1, 2, tzinfo=timezone.utc).isoformat(),
        )
        r3 = SecondBrainRecord(
            entity="entity-1",
            memory_type=MemoryType.DECISION,
            fact={"step": 3},
            supersedes=r2.id,
            timestamp=datetime(2026, 1, 3, tzinfo=timezone.utc).isoformat(),
        )
        operational_store.append(r1)
        operational_store.append(r2)
        operational_store.append(r3)

        hist = operational_store.history("entity-1")
        assert len(hist) == 3
        assert hist[0].fact["step"] == 1
        assert hist[1].fact["step"] == 2
        assert hist[2].fact["step"] == 3


class TestManifest:
    def test_manifest_correct(self):
        manifest_file = os.path.join(
            os.path.dirname(__file__), "..", "core", "second_brain", "stores", "operational", "manifest.json"
        )
        manifest_file = os.path.normpath(manifest_file)
        with open(manifest_file) as f:
            manifest = json.load(f)
        assert manifest["schema_version"] == 1
        assert manifest["store_name"] == "operational"
        assert manifest["merge_policy"] == "NEWEST_WINS"
        assert "INFRASTRUCTURE" in manifest["memory_types"]
        assert "INCIDENT" in manifest["memory_types"]
        assert "OPERATIONAL" in manifest["memory_types"]
        assert "DECISION" in manifest["memory_types"]
