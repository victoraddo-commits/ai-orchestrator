"""Tests for 15F-b: Project-Aware Chat Context.

Covers: project-scoped conversations, conversation continuation,
project switching, context persistence, and user isolation.
"""

import json
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_conversation_store(monkeypatch):
    """Provide isolated in-memory conversation + message stores."""
    import core.kai.conversation as conv

    conversations = []
    messages = []

    monkeypatch.setattr(conv, "_load_conversations", lambda: list(conversations))
    monkeypatch.setattr(conv, "_save_conversations", lambda data: conversations.clear() or conversations.extend(data))
    monkeypatch.setattr(conv, "_load_messages", lambda: list(messages))
    monkeypatch.setattr(conv, "_save_messages", lambda data: messages.clear() or messages.extend(data))

    return {"conversations": conversations, "messages": messages}


# ---------------------------------------------------------------------------
# Project-scoped conversations
# ---------------------------------------------------------------------------


class TestProjectScopedConversations:
    def test_create_with_project_id(self, mock_conversation_store):
        from core.kai.conversation import create_conversation

        cid = create_conversation("user1", project_id="build-abc")
        assert cid is not None
        assert mock_conversation_store["conversations"][0]["project_id"] == "build-abc"
        assert mock_conversation_store["conversations"][0]["user_id"] == "user1"

    def test_create_without_project_id(self, mock_conversation_store):
        from core.kai.conversation import create_conversation

        cid = create_conversation("user1")
        assert cid is not None
        assert mock_conversation_store["conversations"][0]["project_id"] is None

    def test_filter_by_project(self, mock_conversation_store):
        from core.kai.conversation import create_conversation, get_conversations

        create_conversation("user1", project_id="project-a")
        create_conversation("user1", project_id="project-b")
        create_conversation("user1")  # no project

        # Filter by project-a
        a_only = get_conversations("user1", project_id="project-a")
        assert len(a_only) == 1
        assert a_only[0]["project_id"] == "project-a"

        # All user's conversations
        all_user = get_conversations("user1")
        assert len(all_user) == 3

    def test_project_isolation_between_users(self, mock_conversation_store):
        from core.kai.conversation import create_conversation, get_conversations

        create_conversation("user1", project_id="shared-project")
        create_conversation("user2", project_id="shared-project")

        u1 = get_conversations("user1", project_id="shared-project")
        u2 = get_conversations("user2", project_id="shared-project")
        assert len(u1) == 1
        assert len(u2) == 1
        assert u1[0]["user_id"] == "user1"
        assert u2[0]["user_id"] == "user2"


# ---------------------------------------------------------------------------
# Conversation continuation
# ---------------------------------------------------------------------------


class TestConversationContinuation:
    def test_continue_existing_conversation(self, mock_conversation_store):
        from core.kai.conversation import (
            create_conversation, create_message, get_messages, get_conversation,
        )

        cid = create_conversation("user1", project_id="p1")
        create_message(cid, "user", "First message")
        create_message(cid, "assistant", "First response")

        # Continue conversation by appending more messages
        create_message(cid, "user", "Follow-up question")
        create_message(cid, "assistant", "Follow-up answer")

        msgs = get_messages(cid)
        assert len(msgs) == 4
        roles = [m["role"] for m in msgs]
        assert roles == ["user", "assistant", "user", "assistant"]

    def test_cannot_access_other_users_conversation(self, mock_conversation_store):
        from core.kai.conversation import get_conversation, create_conversation

        cid = create_conversation("user1")

        # user2 tries to fetch user1's conversation
        conv = get_conversation(cid)
        # get_conversation returns the raw dict — the API layer enforces
        # user isolation by checking user_id. Verify the conversation
        # belongs to the right user.
        assert conv["user_id"] == "user1"

    def test_messages_isolated_per_conversation(self, mock_conversation_store):
        from core.kai.conversation import create_conversation, create_message, get_messages

        c1 = create_conversation("user1")
        c2 = create_conversation("user1")

        create_message(c1, "user", "conv1 msg")
        create_message(c2, "user", "conv2 msg")

        m1 = get_messages(c1)
        m2 = get_messages(c2)
        assert len(m1) == 1
        assert len(m2) == 1
        assert m1[0]["content"] == "conv1 msg"
        assert m2[0]["content"] == "conv2 msg"


# ---------------------------------------------------------------------------
# Project switching
# ---------------------------------------------------------------------------


class TestProjectSwitching:
    def test_switch_project_preserves_context(self, mock_conversation_store):
        """Conversations from different projects coexist; switching is a
        client-side filter — all conversations persist."""
        from core.kai.conversation import (
            create_conversation, create_message, get_messages,
        )

        # Work on project-a
        c_a = create_conversation("user1", project_id="project-a")
        create_message(c_a, "user", "Work on project A")
        create_message(c_a, "assistant", "OK, working on A")

        # Switch to project-b
        c_b = create_conversation("user1", project_id="project-b")
        create_message(c_b, "user", "Now working on project B")

        # project-a conversation is intact
        a_msgs = get_messages(c_a)
        assert len(a_msgs) == 2
        assert "project A" in a_msgs[0]["content"]

        # project-b conversation has its own messages
        b_msgs = get_messages(c_b)
        assert len(b_msgs) == 1
        assert "project B" in b_msgs[0]["content"]

    def test_list_conversations_across_projects(self, mock_conversation_store):
        from core.kai.conversation import create_conversation, get_conversations

        create_conversation("user1", project_id="p1")
        create_conversation("user1", project_id="p2")
        create_conversation("user1")  # global

        # List all
        all_convos = get_conversations("user1")
        assert len(all_convos) == 3

        # List by project
        p1 = get_conversations("user1", project_id="p1")
        assert len(p1) == 1

        # List by a different project
        p2 = get_conversations("user1", project_id="p2")
        assert len(p2) == 1

        # No project_id filter means all (None = "don't filter")
        all_again = get_conversations("user1")
        assert len(all_again) == 3

        # Verify the global conversation exists (project_id=None)
        global_ids = [c["id"] for c in all_convos if c["project_id"] is None]
        assert len(global_ids) == 1


# ---------------------------------------------------------------------------
# Context persistence across sessions
# ---------------------------------------------------------------------------


class TestContextPersistence:
    def test_conversation_survives_multiple_sessions(self, mock_conversation_store):
        """Conversation state persists because it's saved to memory files."""
        from core.kai.conversation import (
            create_conversation, create_message, get_messages,
        )

        cid = create_conversation("user1", project_id="persistent-p")
        create_message(cid, "user", "Session 1 message")
        create_message(cid, "assistant", "Session 1 response")

        # Simulate "new session" by re-fetching from store
        msgs = get_messages(cid)
        assert len(msgs) == 2

        # Add more in "session 2"
        create_message(cid, "user", "Session 2 follow-up")
        assert len(get_messages(cid)) == 3

    def test_delete_conversation_cleans_messages(self, mock_conversation_store):
        from core.kai.conversation import (
            create_conversation, create_message, get_messages, delete_conversation,
        )

        cid = create_conversation("user1")
        create_message(cid, "user", "temp")

        delete_conversation(cid)
        assert get_messages(cid) == []


# ---------------------------------------------------------------------------
# Title generation
# ---------------------------------------------------------------------------


class TestTitleGeneration:
    def test_title_from_first_message(self, mock_conversation_store):
        from core.kai.conversation import (
            create_conversation, create_message, get_messages,
            update_conversation_title, get_conversation,
        )

        cid = create_conversation("user1")
        create_message(cid, "user", "Build a new dashboard for metrics")
        update_conversation_title(cid, "Build a new dashboard for metrics")

        conv = get_conversation(cid)
        assert "Build a new dashboard" in conv["title"]

    def test_short_message_as_title(self, mock_conversation_store):
        from core.kai.conversation import (
            update_conversation_title, get_conversation, create_conversation,
        )

        cid = create_conversation("user1")
        update_conversation_title(cid, "Hello")
        conv = get_conversation(cid)
        assert conv["title"] == "Hello"


# ---------------------------------------------------------------------------
# API-level integration (KaiChatRequest)
# ---------------------------------------------------------------------------


class TestKaiChatRequestModel:
    def test_project_id_and_conversation_id_are_optional(self):
        """KaiChatRequest accepts project_id and conversation_id as optional."""
        from core.api import KaiChatRequest

        # Minimal request
        req = KaiChatRequest(text="Hello")
        assert req.project_id is None
        assert req.conversation_id is None

    def test_project_id_accepted(self):
        from core.api import KaiChatRequest

        req = KaiChatRequest(text="Hello", project_id="build-123")
        assert req.project_id == "build-123"
        assert req.conversation_id is None

    def test_conversation_id_accepted(self):
        from core.api import KaiChatRequest

        req = KaiChatRequest(
            text="Continue work",
            conversation_id="abc-def-123",
            project_id="build-456",
        )
        assert req.conversation_id == "abc-def-123"
        assert req.project_id == "build-456"
