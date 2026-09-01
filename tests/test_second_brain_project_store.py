"""Tests for the project store."""
from __future__ import annotations

import json
import os
import tempfile
import shutil
from datetime import datetime, timedelta, timezone

import pytest

from core.second_brain.base_store import AppendOnlyStore
from core.second_brain.stores.project import store, SUPPORTED_TYPES
from core.second_brain.types import (
    MemoryType,
    ChangeType,
    Confidence,
    SourceAuthority,
    SecondBrainRecord,
)


@pytest.fixture
def store_dir():
    """Create a temporary project store directory, clean up after."""
    tmp = tempfile.mkdtemp()
    yield tmp
    shutil.rmtree(tmp)


@pytest.fixture
def project_store(store_dir):
    """Create an AppendOnlyStore on the temp directory."""
    return AppendOnlyStore(store_dir)


class TestStoreInitializes:
    def test_store_exists(self):
        from core.second_brain.stores.project import store
        assert store is not None

    def test_supported_types_correct(self):
        assert MemoryType.PROJECT in SUPPORTED_TYPES
        assert MemoryType.BUSINESS in SUPPORTED_TYPES
        assert MemoryType.PERSONAL_CONTEXT in SUPPORTED_TYPES
        assert MemoryType.PROCEDURAL in SUPPORTED_TYPES
        assert len(SUPPORTED_TYPES) == 4


class TestAppendAndGetCurrent:
    def test_append_and_get_current(self, project_store):
        record = SecondBrainRecord(
            entity="project-alpha",
            entity_type="project",
            memory_type=MemoryType.PROJECT,
            fact={"name": "Alpha", "status": "active"},
            change_type=ChangeType.CREATED,
            confidence=Confidence.CONFIRMED,
            source_authority=SourceAuthority.SECOND_BRAIN,
        )
        project_store.append(record)
        result = project_store.get_current("project-alpha")
        assert result is not None
        assert result.entity == "project-alpha"
        assert result.fact["name"] == "Alpha"
        assert result.fact["status"] == "active"

    def test_append_and_get_current_business(self, project_store):
        record = SecondBrainRecord(
            entity="deerude-revenue",
            entity_type="business_metric",
            memory_type=MemoryType.BUSINESS,
            fact={" ARR": 120000, "growth": "15%"},
            change_type=ChangeType.CREATED,
        )
        project_store.append(record)
        result = project_store.get_current("deerude-revenue")
        assert result is not None
        assert result.entity == "deerude-revenue"
        assert result.fact[" ARR"] == 120000

    def test_append_and_get_current_personal_context(self, project_store):
        record = SecondBrainRecord(
            entity="operator-prefs",
            entity_type="personal_context",
            memory_type=MemoryType.PERSONAL_CONTEXT,
            fact={"timezone": "America/New_York", "language": "en"},
            change_type=ChangeType.CREATED,
        )
        project_store.append(record)
        result = project_store.get_current("operator-prefs")
        assert result is not None
        assert result.entity == "operator-prefs"
        assert result.fact["timezone"] == "America/New_York"

    def test_append_and_get_current_procedural(self, project_store):
        record = SecondBrainRecord(
            entity="deploy-procedure",
            entity_type="procedure",
            memory_type=MemoryType.PROCEDURAL,
            fact={"step": "build", "command": "systemctl restart ai-orchestrator"},
            change_type=ChangeType.CREATED,
        )
        project_store.append(record)
        result = project_store.get_current("deploy-procedure")
        assert result is not None
        assert result.entity == "deploy-procedure"
        assert result.fact["step"] == "build"


class TestScanByMemoryType:
    def test_scan_by_memory_type(self, project_store):
        record_project = SecondBrainRecord(
            entity="proj-1",
            entity_type="project",
            memory_type=MemoryType.PROJECT,
            fact={"name": "Proj 1"},
        )
        record_business = SecondBrainRecord(
            entity="biz-1",
            entity_type="business_fact",
            memory_type=MemoryType.BUSINESS,
            fact={"metric": "revenue"},
        )
        record_personal = SecondBrainRecord(
            entity="pref-1",
            entity_type="personal_context",
            memory_type=MemoryType.PERSONAL_CONTEXT,
            fact={"pref": "dark mode"},
        )
        record_procedural = SecondBrainRecord(
            entity="proc-1",
            entity_type="procedure",
            memory_type=MemoryType.PROCEDURAL,
            fact={"task": "deploy"},
        )
        project_store.append(record_project)
        project_store.append(record_business)
        project_store.append(record_personal)
        project_store.append(record_procedural)

        project_results = project_store.scan(memory_type=MemoryType.PROJECT)
        assert len(project_results) == 1
        assert project_results[0].entity == "proj-1"

        business_results = project_store.scan(memory_type=MemoryType.BUSINESS)
        assert len(business_results) == 1
        assert business_results[0].entity == "biz-1"

        personal_results = project_store.scan(memory_type=MemoryType.PERSONAL_CONTEXT)
        assert len(personal_results) == 1
        assert personal_results[0].entity == "pref-1"

        procedural_results = project_store.scan(memory_type=MemoryType.PROCEDURAL)
        assert len(procedural_results) == 1
        assert procedural_results[0].entity == "proc-1"


class TestHistory:
    def test_history(self, project_store):
        r1 = SecondBrainRecord(
            entity="project-beta",
            entity_type="project",
            memory_type=MemoryType.PROJECT,
            fact={"version": 1},
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat(),
        )
        r2 = SecondBrainRecord(
            entity="project-beta",
            entity_type="project",
            memory_type=MemoryType.PROJECT,
            fact={"version": 2},
            supersedes=r1.id,
            timestamp=datetime(2026, 1, 2, tzinfo=timezone.utc).isoformat(),
        )
        r3 = SecondBrainRecord(
            entity="project-beta",
            entity_type="project",
            memory_type=MemoryType.PROJECT,
            fact={"version": 3},
            supersedes=r2.id,
            timestamp=datetime(2026, 1, 3, tzinfo=timezone.utc).isoformat(),
        )
        project_store.append(r1)
        project_store.append(r2)
        project_store.append(r3)

        hist = project_store.history("project-beta")
        assert len(hist) == 3
        assert hist[0].fact["version"] == 1
        assert hist[1].fact["version"] == 2
        assert hist[2].fact["version"] == 3

    def test_history_empty_for_unknown_entity(self, project_store):
        record = SecondBrainRecord(
            entity="known-entity",
            memory_type=MemoryType.PROJECT,
            fact={"key": "value"},
        )
        project_store.append(record)
        hist = project_store.history("unknown-entity")
        assert hist == []


class TestManifest:
    def test_manifest_correct(self):
        manifest_file = os.path.join(
            os.path.dirname(__file__), "..", "core", "second_brain", "stores", "project", "manifest.json"
        )
        manifest_file = os.path.normpath(manifest_file)
        with open(manifest_file) as f:
            manifest = json.load(f)
        assert manifest["schema_version"] == 1
        assert manifest["store_name"] == "project"
        assert manifest["merge_policy"] == "NEWEST_WINS"
        assert "PROJECT" in manifest["memory_types"]
        assert "BUSINESS" in manifest["memory_types"]
        assert "PERSONAL_CONTEXT" in manifest["memory_types"]
        assert "PROCEDURAL" in manifest["memory_types"]
