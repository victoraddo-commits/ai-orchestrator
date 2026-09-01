"""Tests for core/second_brain/stores/conversational/adapters/conversation_adapter.py."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from core.second_brain.stores.conversational.adapters.conversation_adapter import (
    sync_session_to_second_brain,
    sync_turn_to_second_brain,
)


def make_writer_mock(return_value="record-test"):
    """Patch _get_writer and return (mock, writer_instance) for assertions."""
    patcher = patch(
        "core.second_brain.stores.conversational.adapters.conversation_adapter._get_writer"
    )
    writer_mock = patcher.start()
    writer_instance = writer_mock.return_value
    writer_instance.write.return_value = return_value
    writer_instance.update.return_value = return_value
    return patcher, writer_instance


class TestSyncSessionWritesRecord:
    def test_sync_session_writes_record(self):
        """Sync a session and verify a record appears in the store."""
        patcher, writer_instance = make_writer_mock("record-abc")
        try:
            record_id = sync_session_to_second_brain(
                session_id="sess-123",
                turns=[
                    {"role": "user", "content": "hello", "timestamp": "2026-09-01T10:00:00Z"},
                    {"role": "assistant", "content": "hi there", "timestamp": "2026-09-01T10:00:01Z"},
                ],
                summary="greeting session",
                tags=["greeting", "test"],
            )

            assert record_id == "record-abc"
            writer_instance.write.assert_called_once()
            call_args = writer_instance.write.call_args
            record = call_args.kwargs["record"]
            assert record.entity == "sess-123"
            assert record.entity_type == "session"
            assert record.fact["turn_count"] == 2
            assert record.fact["summary"] == "greeting session"
            assert record.fact["tags"] == ["greeting", "test"]
        finally:
            patcher.stop()


class TestSyncSessionSetsCorrectFields:
    def test_sync_session_sets_correct_fields(self):
        """Verify session_id, turn_count, and tags are set correctly."""
        patcher, writer_instance = make_writer_mock("record-xyz")
        try:
            record_id = sync_session_to_second_brain(
                session_id="sess-456",
                turns=[
                    {"role": "user", "content": "first", "timestamp": "2026-09-01T10:00:00Z"},
                    {"role": "assistant", "content": "second", "timestamp": "2026-09-01T10:00:01Z"},
                    {"role": "user", "content": "third", "timestamp": "2026-09-01T10:00:02Z"},
                ],
                tags=["multi-turn", "testing"],
            )

            assert record_id == "record-xyz"
            call_args = writer_instance.write.call_args
            record = call_args.kwargs["record"]
            assert record.fact["session_id"] == "sess-456"
            assert record.fact["turn_count"] == 3
            assert record.fact["tags"] == ["multi-turn", "testing"]
            # No summary provided
            assert record.fact["summary"] is None
        finally:
            patcher.stop()

    def test_sync_session_defaults_tags_to_empty_list(self):
        """When tags is None, fact receives an empty list."""
        patcher, writer_instance = make_writer_mock("record-no-tags")
        try:
            sync_session_to_second_brain(
                session_id="sess-no-tags",
                turns=[{"role": "user", "content": "hello", "timestamp": "2026-09-01T10:00:00Z"}],
            )

            call_args = writer_instance.write.call_args
            record = call_args.kwargs["record"]
            assert record.fact["tags"] == []
        finally:
            patcher.stop()


class TestSyncTurnUpdatesExisting:
    def test_sync_turn_updates_existing(self):
        """Adding a turn calls writer.update and returns the new record ID."""
        patcher, writer_instance = make_writer_mock("record-updated-789")
        try:
            record_id = sync_turn_to_second_brain(
                session_id="sess-789",
                role="user",
                content="new message",
                timestamp="2026-09-01T10:05:00Z",
            )

            assert record_id == "record-updated-789"
            writer_instance.update.assert_called_once()
            call_args = writer_instance.update.call_args
            assert call_args.kwargs["store_name"] == "conversational"
            assert call_args.kwargs["entity"] == "sess-789"
            assert call_args.kwargs["entity_type"] == "session"
            assert call_args.kwargs["fact"]["event"] == "turn_added"
            assert call_args.kwargs["fact"]["role"] == "user"
            assert call_args.kwargs["fact"]["content"] == "new message"
        finally:
            patcher.stop()

    def test_sync_turn_returns_none_on_exception(self):
        """If writer.update raises, sync_turn returns None."""
        patcher, writer_instance = make_writer_mock()
        writer_instance.update.side_effect = RuntimeError("store error")
        try:
            record_id = sync_turn_to_second_brain(
                session_id="sess-fail",
                role="assistant",
                content="this will fail",
                timestamp="2026-09-01T10:10:00Z",
            )

            assert record_id is None
        finally:
            patcher.stop()

    def test_sync_session_returns_none_on_exception(self):
        """If writer.write raises, sync_session returns None."""
        patcher, writer_instance = make_writer_mock()
        writer_instance.write.side_effect = RuntimeError("store error")
        try:
            record_id = sync_session_to_second_brain(
                session_id="sess-fail",
                turns=[{"role": "user", "content": "hello", "timestamp": "2026-09-01T10:00:00Z"}],
            )

            assert record_id is None
        finally:
            patcher.stop()
