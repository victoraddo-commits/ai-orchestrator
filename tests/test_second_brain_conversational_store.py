"""Tests for the conversational store."""
from __future__ import annotations

import tempfile
import shutil

import pytest

from core.second_brain.stores.conversational import SUPPORTED_TYPES, store as conversational_store
from core.second_brain.types import MemoryType, SecondBrainRecord


@pytest.fixture
def store():
    """Create a fresh AppendOnlyStore on a temp directory for each test."""
    from core.second_brain.base_store import AppendOnlyStore
    tmp = tempfile.mkdtemp()
    s = AppendOnlyStore(tmp)
    yield s
    shutil.rmtree(tmp)


class TestStoreInitializes:
    def test_supported_types_contains_conversation(self):
        assert MemoryType.CONVERSATION in SUPPORTED_TYPES

    def test_supported_types_contains_working_memory(self):
        assert MemoryType.WORKING_MEMORY in SUPPORTED_TYPES

    def test_supported_types_contains_short_term(self):
        assert MemoryType.SHORT_TERM in SUPPORTED_TYPES

    def test_supported_types_length(self):
        assert len(SUPPORTED_TYPES) == 3


class TestAppendAndGetCurrent:
    def test_append_and_get_current_conversation(self, store):
        record = SecondBrainRecord(
            entity="session-1",
            entity_type="conversation",
            memory_type=MemoryType.CONVERSATION,
            fact={"role": "user", "content": "hello"},
        )
        store.append(record)

        result = store.get_current("session-1")
        assert result is not None
        assert result.id == record.id
        assert result.entity == "session-1"
        assert result.memory_type == MemoryType.CONVERSATION
        assert result.fact == {"role": "user", "content": "hello"}

    def test_append_and_get_current_working_memory(self, store):
        record = SecondBrainRecord(
            entity="wm-operator-1",
            entity_type="working_memory",
            memory_type=MemoryType.WORKING_MEMORY,
            fact={"task": "deploy", "status": "in_progress"},
        )
        store.append(record)

        result = store.get_current("wm-operator-1")
        assert result is not None
        assert result.memory_type == MemoryType.WORKING_MEMORY
        assert result.fact["task"] == "deploy"

    def test_append_and_get_current_short_term(self, store):
        record = SecondBrainRecord(
            entity="st-context-1",
            entity_type="short_term",
            memory_type=MemoryType.SHORT_TERM,
            fact={"context": "reviewing PR", "pr": 123},
        )
        store.append(record)

        result = store.get_current("st-context-1")
        assert result is not None
        assert result.memory_type == MemoryType.SHORT_TERM
        assert result.fact["context"] == "reviewing PR"


class TestScanByMemoryType:
    def test_scan_returns_only_conversational_types(self, store):
        conv = SecondBrainRecord(
            entity="c1",
            memory_type=MemoryType.CONVERSATION,
        )
        wm = SecondBrainRecord(
            entity="w1",
            memory_type=MemoryType.WORKING_MEMORY,
        )
        st = SecondBrainRecord(
            entity="s1",
            memory_type=MemoryType.SHORT_TERM,
        )
        other = SecondBrainRecord(
            entity="o1",
            memory_type=MemoryType.OPERATIONAL,
        )
        store.append(conv)
        store.append(wm)
        store.append(st)
        store.append(other)

        results = store.scan(memory_type=MemoryType.CONVERSATION)
        assert len(results) == 1
        assert results[0].entity == "c1"

    def test_scan_filters_working_memory(self, store):
        conv = SecondBrainRecord(entity="c1", memory_type=MemoryType.CONVERSATION)
        wm = SecondBrainRecord(entity="w1", memory_type=MemoryType.WORKING_MEMORY)
        store.append(conv)
        store.append(wm)

        results = store.scan(memory_type=MemoryType.WORKING_MEMORY)
        assert len(results) == 1
        assert results[0].entity == "w1"

    def test_scan_filters_short_term(self, store):
        st = SecondBrainRecord(entity="s1", memory_type=MemoryType.SHORT_TERM)
        wm = SecondBrainRecord(entity="w1", memory_type=MemoryType.WORKING_MEMORY)
        store.append(st)
        store.append(wm)

        results = store.scan(memory_type=MemoryType.SHORT_TERM)
        assert len(results) == 1
        assert results[0].entity == "s1"


class TestHistory:
    def test_history_returns_all_versions_oldest_first(self, store):
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        ts1 = now.isoformat()
        ts2 = (now + timedelta(seconds=1)).isoformat()
        ts3 = (now + timedelta(seconds=2)).isoformat()

        r1 = SecondBrainRecord(id="id1", entity="session-1", memory_type=MemoryType.CONVERSATION, timestamp=ts1, fact={"v": 1})
        r2 = SecondBrainRecord(id="id2", entity="session-1", memory_type=MemoryType.CONVERSATION, timestamp=ts2, fact={"v": 2})
        r3 = SecondBrainRecord(id="id3", entity="session-1", memory_type=MemoryType.CONVERSATION, timestamp=ts3, fact={"v": 3})
        store.append(r1)
        store.append(r2)
        store.append(r3)

        results = store.history("session-1")
        assert len(results) == 3
        assert [r.id for r in results] == ["id1", "id2", "id3"]

    def test_history_returns_empty_for_unknown_entity(self, store):
        results = store.history("nonexistent")
        assert results == []

    def test_history_only_returns_matching_entity(self, store):
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        ts1 = now.isoformat()
        ts2 = (now + timedelta(seconds=1)).isoformat()

        r1 = SecondBrainRecord(id="id1", entity="target", memory_type=MemoryType.CONVERSATION, timestamp=ts1)
        r2 = SecondBrainRecord(id="id2", entity="other", memory_type=MemoryType.CONVERSATION, timestamp=ts2)
        store.append(r1)
        store.append(r2)

        results = store.history("target")
        assert len(results) == 1
        assert results[0].id == "id1"
