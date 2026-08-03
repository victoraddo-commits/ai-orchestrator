import json

import pytest

import core.telegram_bridge as tb


# ---------------------------------------------------------------------------
# Fake responses for API mocking (same pattern as tests/test_llm_clients.py)
# ---------------------------------------------------------------------------


def _http_resp(status=200, json_body=None, ok=True):
    class FakeResp:
        status_code = status
        text = ""

        def json(self):
            return json_body

        def raise_for_status(self):
            if status >= 400:
                raise Exception(f"HTTP {status}")

    return FakeResp()


_SEND_OK = _http_resp(json_body={"ok": True, "result": {"message_id": 42}})
_SEND_FAIL = _http_resp(json_body={"ok": False, "description": "chat not found"})


def _make_updates(*messages):
    updates = []
    for i, msg in enumerate(messages):
        message = {
            "message_id": 2000 + i,
            "chat": {
                "id": int(msg.get("chat_id", 612786480)),
                "type": "private",
            },
            "from": {
                "id": int(msg.get("from_id", 612786480)),
                "is_bot": False,
                "first_name": msg.get("first_name", "User"),
                "username": msg.get("username", "testuser"),
            },
            "date": 1753810000,
            "text": msg.get("text", ""),
        }

        # Telegram only includes reply_to_message when the user actually
        # used the native reply feature -- mirror that: absent by default.
        if msg.get("reply_to") is not None:
            message["reply_to_message"] = {"message_id": msg["reply_to"]}

        updates.append(
            {
                "update_id": msg.get("update_id", 1000 + i),
                "message": message,
            }
        )

    return _http_resp(json_body={"ok": True, "result": updates})


_EMPTY_UPDATES = _http_resp(json_body={"ok": True, "result": []})
_UPDATES_FAIL = _http_resp(json_body={"ok": False, "description": "unauthorized"})


# ---------------------------------------------------------------------------
# Outbound formatting
# ---------------------------------------------------------------------------

_FAKE_BUILD = {
    "id": "abc123",
    "name": "13Z",
    "status": "GENERATING",
    "description": "Telegram integration",
}


def test_format_state_change_new_build():
    msg = tb.format_state_change(
        {"id": "abc", "name": "13Z", "status": "REQUESTED"},
    )

    assert "13Z" in msg
    assert "Requested" in msg
    # Must be human-readable, not raw JSON.
    assert "{" not in msg


def test_format_state_change_with_previous_status():
    msg = tb.format_state_change(
        {"id": "abc", "name": "13Z", "status": "GENERATING"},
        previous_status="PLANNING",
    )

    assert "13Z" in msg
    assert "Generating" in msg
    assert "Previous: Planning" in msg


def test_format_state_change_waiting_for_user_input_with_question():
    msg = tb.format_state_change(
        {
            "id": "abc",
            "name": "13X",
            "status": "WAITING_FOR_USER_INPUT",
            "pending_question": "SQLite or PostgreSQL?",
        },
        previous_status="PLANNING",
    )

    assert "Waiting for User Input" in msg
    assert "SQLite or PostgreSQL?" in msg
    assert "Action needed" in msg


def test_format_state_change_waiting_for_architecture_approval():
    msg = tb.format_state_change(
        {
            "id": "abc",
            "name": "13Z",
            "status": "WAITING_FOR_ARCHITECTURE_APPROVAL",
        },
    )

    assert "Waiting for Architecture Approval" in msg
    assert "Action needed" in msg
    assert "13Z" in msg


def test_format_state_change_waiting_for_deploy_approval():
    msg = tb.format_state_change(
        {
            "id": "abc",
            "name": "13Y",
            "status": "WAITING_FOR_DEPLOY_APPROVAL",
        },
    )

    assert "Waiting for Deploy Approval" in msg
    assert "Action needed" in msg


def test_format_state_change_architecture_approval_includes_plan_and_reply_hint():
    msg = tb.format_state_change(
        {
            "id": "abc",
            "name": "13Z",
            "status": "WAITING_FOR_ARCHITECTURE_APPROVAL",
            "plan": "Add a new /widgets endpoint backed by a JSON file store.",
        },
    )

    assert "Add a new /widgets endpoint" in msg
    assert 'Reply "yes" to approve or "no" to reject.' in msg


def test_format_state_change_architecture_approval_truncates_long_plan():
    long_plan = "x" * (tb._PLAN_EXCERPT_CHARS + 500)

    msg = tb.format_state_change(
        {
            "id": "abc",
            "name": "13Z",
            "status": "WAITING_FOR_ARCHITECTURE_APPROVAL",
            "plan": long_plan,
        },
    )

    assert "x" * tb._PLAN_EXCERPT_CHARS in msg
    assert "500 more chars" in msg
    assert len(msg) < len(long_plan) + 500  # excerpt, not the full plan


def test_format_state_change_deploy_approval_includes_security_and_review_summary():
    msg = tb.format_state_change(
        {
            "id": "abc",
            "name": "13Y",
            "status": "WAITING_FOR_DEPLOY_APPROVAL",
            "security_report": {"total_findings": 2, "highest_severity": "low"},
            "code_review": {"reviewer": "gemini", "findings": "Looks fine, one nit."},
        },
    )

    assert "2 finding(s)" in msg
    assert "highest severity: low" in msg
    assert "Looks fine, one nit." in msg
    assert 'Reply "yes" to approve or "no" to reject.' in msg


def test_format_state_change_deploy_approval_notes_skipped_code_review():
    msg = tb.format_state_change(
        {
            "id": "abc",
            "name": "13Y",
            "status": "WAITING_FOR_DEPLOY_APPROVAL",
            "security_report": {"total_findings": 0},
            "code_review": {"skipped": True, "reason": "no reviewer configured"},
        },
    )

    assert "0 finding(s)" in msg
    assert "Code review skipped: no reviewer configured." in msg


def test_format_state_change_failed_with_reason():
    msg = tb.format_state_change(
        {
            "id": "abc",
            "name": "13Z",
            "status": "FAILED",
            "failure_reason": "Tests did not pass",
        },
        previous_status="GENERATING",
    )

    assert "Failed" in msg
    assert "Tests did not pass" in msg


def test_format_state_change_completed():
    msg = tb.format_state_change(
        {"id": "abc", "name": "13Z", "status": "COMPLETED"},
    )

    assert "Completed" in msg
    assert "Action needed" not in msg


# ---------------------------------------------------------------------------
# State-change detection
# ---------------------------------------------------------------------------


def test_detect_state_changes_new_build():
    before = []
    after = [{"id": "b1", "name": "13Z", "status": "REQUESTED"}]

    changes = tb.detect_state_changes(before, after)

    assert len(changes) == 1
    assert "13Z" in changes[0]
    assert "Requested" in changes[0]


def test_detect_state_changes_actual_change():
    before = [{"id": "b1", "name": "13Z", "status": "PLANNING"}]
    after = [{"id": "b1", "name": "13Z", "status": "WAITING_FOR_USER_INPUT", "pending_question": "DB?"}]

    changes = tb.detect_state_changes(before, after)

    assert len(changes) == 1
    assert "Waiting for User Input" in changes[0]
    assert "DB?" in changes[0]


def test_detect_state_changes_no_change_produces_empty_list():
    before = [{"id": "b1", "name": "13Z", "status": "PLANNING"}]
    after = [{"id": "b1", "name": "13Z", "status": "PLANNING"}]

    changes = tb.detect_state_changes(before, after)

    assert changes == []


def test_detect_state_changes_multiple_builds():
    before = [
        {"id": "b1", "name": "13Z", "status": "PLANNING"},
        {"id": "b2", "name": "13Y", "status": "GENERATING"},
    ]
    after = [
        {"id": "b1", "name": "13Z", "status": "WAITING_FOR_ARCHITECTURE_APPROVAL"},
        {"id": "b2", "name": "13Y", "status": "GENERATING"},
    ]

    changes = tb.detect_state_changes(before, after)

    assert len(changes) == 1
    assert "13Z" in changes[0]


def test_detect_state_changes_no_spam_on_identical_cycles():
    builds = [{"id": "b1", "name": "13Z", "status": "WAITING_FOR_ARCHITECTURE_APPROVAL"}]

    # Same builds passed in two cycles: zero messages.
    first = tb.detect_state_changes(builds, list(builds))
    assert first == []

    second = tb.detect_state_changes(builds, list(builds))
    assert second == []


# ---------------------------------------------------------------------------
# Inbound reply routing
# ---------------------------------------------------------------------------


def test_route_inbound_reply_submit_answer(monkeypatch):
    builds = [
        {
            "id": "b1",
            "name": "13X",
            "status": "WAITING_FOR_USER_INPUT",
            "pending_question": "SQLite or Postgres?",
        }
    ]
    monkeypatch.setattr(tb, "_find_pending_build", lambda: list(builds))

    calls = {}

    def fake_submit_answer(build_id, answer):
        calls["build_id"] = build_id
        calls["answer"] = answer
        return {"id": build_id, "name": "13X", "status": "PLANNING"}

    import core.build_manager as bm
    monkeypatch.setattr(bm, "submit_answer", fake_submit_answer)

    result = tb.route_inbound_reply(
        {
            "text": "PostgreSQL please",
            "from": {"id": "612786480", "first_name": "Dev", "username": "dev"},
        },
        pending_builds=builds,
    )

    assert result["routed"] is True
    assert result["action"] == "submit_answer"
    assert calls["answer"] == "PostgreSQL please"
    assert calls["build_id"] == "b1"
    assert "dev" in result["operator"].lower()


def test_route_inbound_reply_approve_architecture(monkeypatch):
    builds = [
        {"id": "b1", "name": "13Z", "status": "WAITING_FOR_ARCHITECTURE_APPROVAL"}
    ]
    monkeypatch.setattr(tb, "_find_pending_build", lambda: list(builds))

    calls = {}

    def fake_approve_architecture(build_id, operator=None):
        calls["build_id"] = build_id
        calls["operator"] = operator
        return {"id": build_id, "status": "ARCHITECTURE_APPROVED"}

    import core.build_manager as bm
    monkeypatch.setattr(bm, "approve_architecture", fake_approve_architecture)

    result = tb.route_inbound_reply(
        {"text": "approve", "from": {"id": "612786480", "first_name": "Dev"}},
        pending_builds=builds,
    )

    assert result["routed"] is True
    assert result["action"] == "approve_architecture"
    assert calls["build_id"] == "b1"
    assert calls["operator"] is not None
    assert "tg:612786480" in calls["operator"]


def test_route_inbound_reply_reject_architecture(monkeypatch):
    builds = [
        {"id": "b1", "name": "13Z", "status": "WAITING_FOR_ARCHITECTURE_APPROVAL"}
    ]
    monkeypatch.setattr(tb, "_find_pending_build", lambda: list(builds))

    calls = {}

    def fake_reject_architecture(build_id, operator=None):
        calls["build_id"] = build_id
        calls["operator"] = operator
        return {"id": build_id, "status": "FAILED"}

    import core.build_manager as bm
    monkeypatch.setattr(bm, "reject_architecture", fake_reject_architecture)

    result = tb.route_inbound_reply(
        {"text": "no", "from": {"id": "612786480", "first_name": "Dev"}},
        pending_builds=builds,
    )

    assert result["routed"] is True
    assert result["action"] == "reject_architecture"
    assert calls["build_id"] == "b1"


def test_route_inbound_reply_approve_deploy(monkeypatch):
    builds = [
        {"id": "b1", "name": "13Y", "status": "WAITING_FOR_DEPLOY_APPROVAL"}
    ]
    monkeypatch.setattr(tb, "_find_pending_build", lambda: list(builds))

    calls = {}

    def fake_approve_deploy(build_id, operator=None):
        calls["build_id"] = build_id
        calls["operator"] = operator
        return {"id": build_id, "status": "DEPLOYING"}

    import core.build_manager as bm
    monkeypatch.setattr(bm, "approve_deploy", fake_approve_deploy)

    result = tb.route_inbound_reply(
        {"text": "yes", "from": {"id": "612786480"}},
        pending_builds=builds,
    )

    assert result["routed"] is True
    assert result["action"] == "approve_deploy"


def test_route_inbound_reply_no_pending_build_forwards_to_kai_chat(monkeypatch):
    """17K: When no build is pending, route_inbound_reply must forward the
    message to handle_kai_chat and return a real chat answer -- NOT the old
    'No build is currently awaiting input.' dead-end."""
    import core.api as api_module

    called = {}

    def fake_handle_kai_chat(text, operator):
        called["text"] = text
        called["operator"] = operator
        return {"matched": False, "response": "Hi! I'm Kai, your orchestrator."}

    monkeypatch.setattr(api_module, "handle_kai_chat", fake_handle_kai_chat)
    # Force lazy import cache to refresh so the bridge picks up the monkeypatched version.
    import core.telegram_bridge as tb_module
    tb_module._handle_kai_chat = fake_handle_kai_chat

    result = tb.route_inbound_reply(
        {"text": "hello", "from": {"id": "612786480", "first_name": "Dev"}},
        pending_builds=[],
    )

    assert result["routed"] is True
    assert result["action"] == "kai_chat"
    assert called["text"] == "hello"
    assert "Hi! I'm Kai" in result["reply"]
    assert "No build" not in result["reply"]


def test_route_inbound_reply_multiple_pending_builds():
    builds = [
        {"id": "b1", "name": "13Z", "status": "WAITING_FOR_ARCHITECTURE_APPROVAL"},
        {"id": "b2", "name": "13Y", "status": "WAITING_FOR_DEPLOY_APPROVAL"},
    ]

    result = tb.route_inbound_reply(
        {"text": "approve", "from": {"id": "612786480"}},
        pending_builds=builds,
    )

    assert result["routed"] is False
    assert "Multiple builds" in result["reply"]


def test_route_inbound_reply_unmatched_message_no_action():
    builds = [
        {"id": "b1", "name": "13Z", "status": "WAITING_FOR_DEPLOY_APPROVAL"}
    ]

    # Not a clear approve/reject word: "maybe" should be treated as
    # unmatched and prompt the user for a clear answer.
    result = tb.route_inbound_reply(
        {"text": "maybe later", "from": {"id": "612786480"}},
        pending_builds=builds,
    )

    assert result["routed"] is True
    assert "reply" in result
    assert "awaiting input" not in result["reply"].lower()
    assert "approve" in result["reply"].lower() or "waiting" in result["reply"].lower()


def test_route_inbound_reply_ignores_irrelevant_text_correctly(monkeypatch):
    builds = [
        {"id": "b1", "name": "13Z", "status": "WAITING_FOR_DEPLOY_APPROVAL"}
    ]
    monkeypatch.setattr(tb, "_find_pending_build", lambda: list(builds))

    called = {"approve_deploy": False, "reject_deploy": False}

    def fake_approve_deploy(*a, **k):
        called["approve_deploy"] = True
        return {}

    def fake_reject_deploy(*a, **k):
        called["reject_deploy"] = True
        return {}

    import core.build_manager as bm
    monkeypatch.setattr(bm, "approve_deploy", fake_approve_deploy)
    monkeypatch.setattr(bm, "reject_deploy", fake_reject_deploy)

    result = tb.route_inbound_reply(
        {"text": "random chat about weather", "from": {"id": "612786480"}},
        pending_builds=builds,
    )

    assert not called["approve_deploy"]
    assert not called["reject_deploy"]
    assert result["routed"] is True


# ---------------------------------------------------------------------------
# No-spam: detect_state_changes returns nothing when nothing changed
# ---------------------------------------------------------------------------


def test_no_spam_zero_changes_on_repeated_identical_snapshot():
    before = [
        {"id": "a", "name": "13Z", "status": "PLANNING"},
        {"id": "b", "name": "13Y", "status": "WAITING_FOR_ARCHITECTURE_APPROVAL"},
    ]

    after = list(before)

    changes = tb.detect_state_changes(before, after)
    assert changes == []

    changes_again = tb.detect_state_changes(before, after)
    assert changes_again == []


# ---------------------------------------------------------------------------
# Outbound sending (mocked HTTP)
# ---------------------------------------------------------------------------


def test_send_message_posts_correct_payload(monkeypatch):
    monkeypatch.setenv("KAI_TELEGRAM_BOT_TOKEN", "test-token-12345")

    posted = {}

    def fake_post(url, json=None, timeout=None):
        posted["url"] = url
        posted["json"] = json
        return _SEND_OK

    monkeypatch.setattr(tb.requests, "post", fake_post)

    tb.send_message("Hello from Kai", token="test-token-12345", chat_id="612786480")

    assert "test-token-12345" in posted["url"]
    assert "/sendMessage" in posted["url"]
    assert posted["json"]["chat_id"] == "612786480"
    assert posted["json"]["text"] == "Hello from Kai"


def test_send_message_raises_on_api_failure(monkeypatch):
    monkeypatch.setenv("KAI_TELEGRAM_BOT_TOKEN", "test-token")

    monkeypatch.setattr(
        tb.requests, "post",
        lambda *a, **k: _SEND_FAIL,
    )

    with pytest.raises(RuntimeError, match="sendMessage"):
        tb.send_message("test", token="test-token", chat_id="612786480")


# ---------------------------------------------------------------------------
# Inbound polling (mocked HTTP)
# ---------------------------------------------------------------------------


def test_poll_updates_returns_new_messages(monkeypatch):
    monkeypatch.setenv("KAI_TELEGRAM_BOT_TOKEN", "test-token")
    tb.reset_offset()

    monkeypatch.setattr(
        tb.requests, "get",
        lambda url, params=None, timeout=None: _make_updates(
            {"text": "approve"},
        ),
    )

    messages = tb.poll_updates(token="test-token", chat_id="612786480")

    assert len(messages) == 1
    assert messages[0]["text"] == "approve"
    assert messages[0]["from"]["username"] == "testuser"


def test_poll_updates_filters_non_allowed_chat(monkeypatch):
    monkeypatch.setenv("KAI_TELEGRAM_BOT_TOKEN", "test-token")
    tb.reset_offset()

    monkeypatch.setattr(
        tb.requests, "get",
        lambda url, params=None, timeout=None: _make_updates(
            {"text": "hello", "chat_id": "999999999"},
        ),
    )

    messages = tb.poll_updates(token="test-token", chat_id="612786480")

    assert messages == []


def test_poll_updates_skips_empty_text_messages(monkeypatch):
    monkeypatch.setenv("KAI_TELEGRAM_BOT_TOKEN", "test-token")
    tb.reset_offset()

    monkeypatch.setattr(
        tb.requests, "get",
        lambda url, params=None, timeout=None: _make_updates(
            {"text": ""},
        ),
    )

    messages = tb.poll_updates(token="test-token", chat_id="612786480")

    assert messages == []


def test_poll_updates_respects_last_offset(monkeypatch):
    monkeypatch.setenv("KAI_TELEGRAM_BOT_TOKEN", "test-token")
    tb.reset_offset()

    call_count = [0]

    def counting_get(url, params=None, timeout=None):
        call_count[0] += 1

        if call_count[0] == 1:
            return _make_updates(
                {"update_id": 5001, "text": "first"},
            )
        else:
            return _http_resp(json_body={"ok": True, "result": []})

    monkeypatch.setattr(tb.requests, "get", counting_get)

    first = tb.poll_updates(token="test-token", chat_id="612786480")
    assert len(first) == 1
    assert first[0]["text"] == "first"

    second = tb.poll_updates(token="test-token", chat_id="612786480")
    # Offset = 5001 + 1 = 5002 -- no new updates should return.
    assert second == []


def test_poll_updates_raises_on_get_updates_failure(monkeypatch):
    monkeypatch.setenv("KAI_TELEGRAM_BOT_TOKEN", "test-token")
    tb.reset_offset()

    monkeypatch.setattr(
        tb.requests, "get",
        lambda url, params=None, timeout=None: _UPDATES_FAIL,
    )

    with pytest.raises(RuntimeError, match="getUpdates"):
        tb.poll_updates(token="test-token", chat_id="612786480")


# ---------------------------------------------------------------------------
# Operator name formatting
# ---------------------------------------------------------------------------


def test_operator_name_with_full_info():
    name = tb._operator_name(
        {"id": "612786480", "first_name": "Dev", "username": "developer"}
    )
    assert "Dev" in name
    assert "@developer" in name
    assert "tg:612786480" in name


def test_operator_name_without_username():
    name = tb._operator_name({"id": "612786480", "first_name": "Dev"})
    assert "Dev" in name
    assert "tg:612786480" in name


def test_operator_name_id_only():
    name = tb._operator_name({"id": "612786480"})
    assert "tg:612786480" in name


# ---------------------------------------------------------------------------
# Phase 17K: Telegram full Kai chat access
# ---------------------------------------------------------------------------


def test_17k_no_pending_build_calls_handle_kai_chat(monkeypatch):
    """When no build is pending, route_inbound_reply calls handle_kai_chat
    and returns the response -- the old 'No build is currently awaiting
    input.' dead-end must never appear."""
    import core.telegram_bridge as tb_module

    def fake_handle_kai_chat(text, operator):
        return {"matched": False, "response": "Kai says hello from shared handler."}

    tb_module._handle_kai_chat = fake_handle_kai_chat

    result = tb.route_inbound_reply(
        {"text": "What is the roadmap status?", "from": {"id": "612786480"}},
        pending_builds=[],
    )

    assert result["routed"] is True
    assert result["action"] == "kai_chat"
    assert "Kai says hello" in result["reply"]
    assert "No build" not in result["reply"]

    # Restore so other tests aren't affected.
    tb_module._handle_kai_chat = None


def test_17k_pending_build_still_routes_to_submit_answer(monkeypatch):
    """With a build in WAITING_FOR_USER_INPUT, route_inbound_reply must route
    to submit_answer -- not to the chat handler -- preserving the existing
    build Q&A priority."""
    builds = [
        {
            "id": "b-qa",
            "name": "17K-test",
            "status": "WAITING_FOR_USER_INPUT",
            "pending_question": "SQLite or Postgres?",
        }
    ]
    monkeypatch.setattr(tb, "_find_pending_build", lambda: list(builds))

    called = {}

    def fake_submit_answer(build_id, answer):
        called["build_id"] = build_id
        called["answer"] = answer
        return {"id": build_id, "name": "17K-test", "status": "PLANNING"}

    import core.build_manager as bm
    monkeypatch.setattr(bm, "submit_answer", fake_submit_answer)

    # Ensure handle_kai_chat is NOT called.
    import core.telegram_bridge as tb_module
    chat_called = {"yes": False}

    original = tb_module._handle_kai_chat

    def spy_chat(text, operator):
        chat_called["yes"] = True
        return {"matched": False, "response": "should not be called"}

    tb_module._handle_kai_chat = spy_chat

    try:
        result = tb.route_inbound_reply(
            {"text": "PostgreSQL", "from": {"id": "612786480"}},
            pending_builds=builds,
        )
    finally:
        tb_module._handle_kai_chat = original

    assert result["routed"] is True
    assert result["action"] == "submit_answer"
    assert called["answer"] == "PostgreSQL"
    assert chat_called["yes"] is False, "handle_kai_chat must NOT be called when a build is pending"


def test_17k_pending_build_approve_routes_to_approve_not_chat(monkeypatch):
    """Approval messages ('approve') must still go to approve_architecture
    when a build is waiting, not to the open-ended chat handler."""
    builds = [
        {"id": "b-arch", "name": "17K-arch", "status": "WAITING_FOR_ARCHITECTURE_APPROVAL"}
    ]
    monkeypatch.setattr(tb, "_find_pending_build", lambda: list(builds))

    import core.build_manager as bm
    monkeypatch.setattr(bm, "approve_architecture", lambda bid, operator=None: {"id": bid, "status": "ARCHITECTURE_APPROVED"})

    import core.telegram_bridge as tb_module
    original = tb_module._handle_kai_chat
    chat_called = {"yes": False}

    def spy_chat(text, operator):
        chat_called["yes"] = True
        return {"matched": False, "response": "wrong path"}

    tb_module._handle_kai_chat = spy_chat

    try:
        result = tb.route_inbound_reply(
            {"text": "approve", "from": {"id": "612786480"}},
            pending_builds=builds,
        )
    finally:
        tb_module._handle_kai_chat = original

    assert result["routed"] is True
    assert result["action"] == "approve_architecture"
    assert chat_called["yes"] is False


def test_17k_shared_implementation_behavior_change_visible_from_both_surfaces(monkeypatch):
    """Verify the shared handler contract: a single change to handle_kai_chat
    is immediately visible from both POST /kai/chat (via the API) and the
    Telegram route_inbound_reply path -- there is no parallel copy of logic.

    We monkeypatch handle_kai_chat with a custom implementation and confirm
    that both callers invoke that same function, not independent copies."""
    import core.api as api_module
    import core.telegram_bridge as tb_module

    sentinel_calls = []

    def sentinel_handler(text, operator):
        sentinel_calls.append({"text": text, "operator": operator})
        return {"matched": False, "response": f"sentinel:{text}"}

    # Patch the function in api module.
    monkeypatch.setattr(api_module, "handle_kai_chat", sentinel_handler)
    # Also update the cached reference in telegram_bridge.
    tb_module._handle_kai_chat = sentinel_handler

    try:
        from fastapi.testclient import TestClient
        client = TestClient(api_module.app)

        def _auth_headers():
            return {"Authorization": f"Bearer {api_module._load_api_token()}"}

        # Call via POST /kai/chat.
        resp = client.post("/kai/chat", json={"text": "test from api"}, headers=_auth_headers())
        assert resp.status_code == 200
        assert "sentinel:test from api" in resp.json().get("response", "")

        # Call via Telegram bridge.
        tg_result = tb.route_inbound_reply(
            {"text": "test from telegram", "from": {"id": "612786480"}},
            pending_builds=[],
        )
        assert tg_result["routed"] is True
        assert "sentinel:test from telegram" in tg_result["reply"]

        # Both calls went through the SAME sentinel.
        assert len(sentinel_calls) == 2
        texts = {c["text"] for c in sentinel_calls}
        assert "test from api" in texts
        assert "test from telegram" in texts
    finally:
        tb_module._handle_kai_chat = None


def test_17k_shared_history_across_telegram_and_api(monkeypatch):
    """Conversation history (kai_chat_history.json) is the same file for
    both surfaces: a message written by the API side is visible when the
    Telegram side reads it, because both call the same _load/_save helpers.

    This is a structural/unit test -- it writes a known message to the
    chat history via the API helper and confirms the Telegram-side
    handle_kai_chat call would receive that same history (i.e., the history
    is NOT per-surface; there are no separate files)."""
    import core.api as api_module
    import core.telegram_bridge as tb_module

    # Write a known message to history via the API's own save helper.
    api_module._save_chat_history([
        {"role": "user", "content": "prior API message"},
        {"role": "assistant", "content": "prior API answer"},
    ])

    # Confirm the same history is visible via _load_chat_history (shared store).
    loaded = api_module._load_chat_history()
    assert any(m["content"] == "prior API message" for m in loaded), \
        "API-written message not visible via _load_chat_history"

    # Now simulate a Telegram chat call: handle_kai_chat reads from that same
    # file. We stub the AI so no network call happens.
    history_seen_by_handler = []

    def capturing_handler(text, operator):
        # Read history INSIDE the call to prove it sees the prior API context.
        h = api_module._load_chat_history()
        history_seen_by_handler.extend(h)
        return {"matched": False, "response": "Telegram answer"}

    tb_module._handle_kai_chat = capturing_handler

    try:
        result = tb.route_inbound_reply(
            {"text": "Telegram question", "from": {"id": "612786480"}},
            pending_builds=[],
        )
        assert result["routed"] is True
    finally:
        tb_module._handle_kai_chat = None

    # The Telegram-side handler saw the API's prior message in the shared history.
    contents = [m["content"] for m in history_seen_by_handler]
    assert "prior API message" in contents, \
        "Telegram handler did not see the prior API message in shared history"


def test_17k_real_shared_history_end_to_end(monkeypatch):
    """End-to-end shared history test using the real handle_kai_chat, with
    AI calls stubbed to return fast.

    A question via POST /kai/chat is saved to history; a subsequent Telegram
    message calls the same handler and thus reads that same history file,
    proving both surfaces share context."""
    import core.api as api_module
    import core.telegram_bridge as tb_module

    # Stub the AI and planner so no real network call is made.
    monkeypatch.setattr(api_module, "ai_chat", lambda history, signals: "AI answer")
    monkeypatch.setattr(api_module, "gather_signals", lambda: {})

    # Stub kai_dispatch to always be unmatched so we reach the AI fallback.
    monkeypatch.setattr(api_module, "kai_dispatch", lambda text: {"matched": False})

    # Make sure the bridge uses the REAL handle_kai_chat (not a stub).
    tb_module._handle_kai_chat = None  # reset lazy cache so it re-imports

    from fastapi.testclient import TestClient
    client = TestClient(api_module.app)

    def _auth():
        return {"Authorization": f"Bearer {api_module._load_api_token()}"}

    # Step 1: Chat via POST /kai/chat.
    resp = client.post("/kai/chat", json={"text": "API question"}, headers=_auth())
    assert resp.status_code == 200

    # Step 2: Confirm history was written.
    history_before_tg = api_module._load_chat_history()
    user_msgs = [m for m in history_before_tg if m["role"] == "user"]
    assert any(m["content"] == "API question" for m in user_msgs), \
        "API question not persisted to kai_chat_history.json"

    # Step 3: Send a message via Telegram (no pending build).
    # Re-import to get the real handle_kai_chat now that _handle_kai_chat=None.
    tb_module._import_kai_chat()
    result = tb.route_inbound_reply(
        {"text": "Telegram question", "from": {"id": "612786480"}},
        pending_builds=[],
    )
    assert result["routed"] is True
    assert result["action"] == "kai_chat"
    assert "AI answer" in result["reply"]

    # Step 4: The history now has BOTH the API message and the Telegram message.
    history_after = api_module._load_chat_history()
    all_user_msgs = [m["content"] for m in history_after if m["role"] == "user"]
    assert "API question" in all_user_msgs, "API question vanished from history after Telegram message"
    assert "Telegram question" in all_user_msgs, "Telegram question not saved to shared history"


# ---------------------------------------------------------------------------
# Native reply-to disambiguation: replying to a specific Kai notification
# resolves directly to that build, even with multiple builds pending
# ---------------------------------------------------------------------------


def test_poll_updates_extracts_reply_to_message_id_when_present(monkeypatch):
    monkeypatch.setenv("KAI_TELEGRAM_BOT_TOKEN", "test-token")
    tb.reset_offset()

    monkeypatch.setattr(
        tb.requests, "get",
        lambda url, params=None, timeout=None: _make_updates(
            {"text": "approve", "reply_to": 4242},
        ),
    )

    messages = tb.poll_updates(token="test-token", chat_id="612786480")

    assert len(messages) == 1
    assert messages[0]["reply_to_message_id"] == 4242


def test_poll_updates_reply_to_message_id_is_none_when_absent(monkeypatch):
    monkeypatch.setenv("KAI_TELEGRAM_BOT_TOKEN", "test-token")
    tb.reset_offset()

    monkeypatch.setattr(
        tb.requests, "get",
        lambda url, params=None, timeout=None: _make_updates(
            {"text": "approve"},
        ),
    )

    messages = tb.poll_updates(token="test-token", chat_id="612786480")

    assert len(messages) == 1
    assert messages[0]["reply_to_message_id"] is None


def test_record_sent_build_message_round_trip():
    tb.record_sent_build_message(42, "build-abc")

    # message_id arrives as int from Telegram but the JSON store keys are
    # strings -- the round-trip must work with the int the caller has.
    assert tb._build_id_for_message(42) == "build-abc"


def test_build_id_for_message_returns_none_when_unknown():
    assert tb._build_id_for_message(999999) is None


def test_record_sent_build_message_evicts_oldest_beyond_cap():
    for i in range(tb._MESSAGE_BUILD_MAP_LIMIT + 5):
        tb.record_sent_build_message(i, f"build-{i}")

    # Oldest 5 evicted, newest survivors intact.
    assert tb._build_id_for_message(0) is None
    assert tb._build_id_for_message(4) is None
    assert tb._build_id_for_message(5) == "build-5"
    assert (
        tb._build_id_for_message(tb._MESSAGE_BUILD_MAP_LIMIT + 4)
        == f"build-{tb._MESSAGE_BUILD_MAP_LIMIT + 4}"
    )


def test_route_inbound_reply_reply_to_disambiguates_among_multiple_pending(monkeypatch):
    """Core scenario: 2+ builds pending, plain 'approve' would be ambiguous,
    but a native reply-to on the deploy build's notification must route to
    exactly that build -- bypassing the 'Multiple builds' branch."""
    arch_build = {"id": "b-arch", "name": "13Z", "status": "WAITING_FOR_ARCHITECTURE_APPROVAL"}
    deploy_build = {"id": "b-deploy", "name": "13Y", "status": "WAITING_FOR_DEPLOY_APPROVAL"}

    monkeypatch.setattr(tb, "_find_pending_build", lambda: [arch_build, deploy_build])

    import core.build_manager as bm
    monkeypatch.setattr(
        bm, "get_build",
        lambda build_id: deploy_build if build_id == "b-deploy" else arch_build,
    )

    calls = {}

    def fake_approve_deploy(build_id, operator=None):
        calls["build_id"] = build_id
        return {"id": build_id, "status": "DEPLOYING"}

    monkeypatch.setattr(bm, "approve_deploy", fake_approve_deploy)

    # Kai announced the deploy approval as Telegram message 42.
    tb.record_sent_build_message(42, "b-deploy")

    result = tb.route_inbound_reply(
        {
            "text": "approve",
            "reply_to_message_id": 42,
            "from": {"id": "612786480", "first_name": "Dev"},
        },
    )

    assert result["routed"] is True
    assert result["action"] == "approve_deploy"
    assert calls["build_id"] == "b-deploy"
    assert "Multiple builds" not in result.get("reply", "")


def test_route_inbound_reply_falls_back_to_ambiguity_without_reply_to(monkeypatch):
    builds = [
        {"id": "b1", "name": "13Z", "status": "WAITING_FOR_ARCHITECTURE_APPROVAL"},
        {"id": "b2", "name": "13Y", "status": "WAITING_FOR_DEPLOY_APPROVAL"},
    ]
    monkeypatch.setattr(tb, "_find_pending_build", lambda: list(builds))

    result = tb.route_inbound_reply(
        {"text": "approve", "from": {"id": "612786480"}},
    )

    assert result["routed"] is False
    assert "Multiple builds" in result["reply"]


def test_route_inbound_reply_falls_back_when_reply_to_unknown(monkeypatch):
    builds = [
        {"id": "b1", "name": "13Z", "status": "WAITING_FOR_ARCHITECTURE_APPROVAL"},
        {"id": "b2", "name": "13Y", "status": "WAITING_FOR_DEPLOY_APPROVAL"},
    ]
    monkeypatch.setattr(tb, "_find_pending_build", lambda: list(builds))

    # Nothing was ever recorded for message 777 -- e.g. a reply to an old
    # message that fell off the capped map. Must not error, must fall
    # through to the existing multi-pending behavior.
    result = tb.route_inbound_reply(
        {"text": "approve", "reply_to_message_id": 777, "from": {"id": "612786480"}},
    )

    assert result["routed"] is False
    assert "Multiple builds" in result["reply"]


def test_route_inbound_reply_falls_back_when_replied_build_already_resolved(monkeypatch):
    """A reply to a notification for a build that has since been approved via
    another surface (dashboard/CLI) must not hijack routing -- fall back to
    the normal pending-build matching."""
    resolved_build = {"id": "b-done", "name": "13X", "status": "COMPLETED"}
    builds = [
        {"id": "b1", "name": "13Z", "status": "WAITING_FOR_ARCHITECTURE_APPROVAL"},
        {"id": "b2", "name": "13Y", "status": "WAITING_FOR_DEPLOY_APPROVAL"},
    ]
    monkeypatch.setattr(tb, "_find_pending_build", lambda: list(builds))

    import core.build_manager as bm
    monkeypatch.setattr(bm, "get_build", lambda build_id: resolved_build)

    tb.record_sent_build_message(43, "b-done")

    result = tb.route_inbound_reply(
        {"text": "approve", "reply_to_message_id": 43, "from": {"id": "612786480"}},
    )

    assert result["routed"] is False
    assert "Multiple builds" in result["reply"]


def test_route_inbound_reply_falls_back_when_replied_build_missing(monkeypatch):
    builds = [
        {"id": "b1", "name": "13Z", "status": "WAITING_FOR_ARCHITECTURE_APPROVAL"},
        {"id": "b2", "name": "13Y", "status": "WAITING_FOR_DEPLOY_APPROVAL"},
    ]
    monkeypatch.setattr(tb, "_find_pending_build", lambda: list(builds))

    import core.build_manager as bm
    monkeypatch.setattr(bm, "get_build", lambda build_id: None)

    # Mapping exists but the build itself was deleted since.
    tb.record_sent_build_message(44, "b-vanished")

    result = tb.route_inbound_reply(
        {"text": "approve", "reply_to_message_id": 44, "from": {"id": "612786480"}},
    )

    assert result["routed"] is False
    assert "Multiple builds" in result["reply"]


def test_detect_state_changes_with_build_ids_returns_pairs():
    before = [
        {"id": "b1", "name": "13Z", "status": "PLANNING"},
        {"id": "b2", "name": "13Y", "status": "GENERATING"},
    ]
    after = [
        {"id": "b1", "name": "13Z", "status": "WAITING_FOR_ARCHITECTURE_APPROVAL"},
        {"id": "b2", "name": "13Y", "status": "GENERATING"},
        {"id": "b3", "name": "13X", "status": "REQUESTED"},
    ]

    pairs = tb.detect_state_changes_with_build_ids(before, after)

    assert [(bid, "13Z" in text) for bid, text in pairs if bid == "b1"] == [("b1", True)]
    assert {bid for bid, _ in pairs} == {"b1", "b3"}

    # Message texts must be identical to what detect_state_changes emits --
    # same helper underneath, just paired with the build id.
    assert [text for _, text in pairs] == tb.detect_state_changes(before, after)


def test_route_inbound_reply_build_selection_by_number(monkeypatch):
    """Test selecting a build by number"""
    builds = [
        {"id": "b1", "name": "telegra-approval-responder", "status": "WAITING_FOR_ARCHITECTURE_APPROVAL", "project_path": "/project/telegra-approval-responder"},
        {"id": "b2", "name": "telegra-approval-responder", "status": "WAITING_FOR_DEPLOY_APPROVAL", "project_path": "/project/telegra-approval-responder-2"},
    ]
    monkeypatch.setattr(tb, "_find_pending_build", lambda: list(builds))

    # Test with numeric input to select first build - should not auto-approve
    result = tb.route_inbound_reply(
        {"text": "1", "from": {"id": "612786480", "first_name": "Dev"}},
        pending_builds=builds,
    )

    assert result["routed"] is True
    assert "action" not in result  # Should not auto-approve
    assert "waiting for architecture approval" in result["reply"]  # Should prompt for approval


def test_route_inbound_reply_build_selection_by_name(monkeypatch):
    """Test selecting a build by name"""
    builds = [
        {"id": "b1", "name": "telegra-approval-responder", "status": "WAITING_FOR_ARCHITECTURE_APPROVAL", "project_path": "/project/telegra-approval-responder"},
        {"id": "b2", "name": "telegra-approval-responder", "status": "WAITING_FOR_DEPLOY_APPROVAL", "project_path": "/project/telegra-approval-responder-2"},
    ]
    monkeypatch.setattr(tb, "_find_pending_build", lambda: list(builds))

    # Test with name input to select first build - should not auto-approve
    result = tb.route_inbound_reply(
        {"text": "telegra-approval-responder", "from": {"id": "612786480", "first_name": "Dev"}},
        pending_builds=builds,
    )

    assert result["routed"] is True
    assert "action" not in result  # Should not auto-approve
    assert "waiting for architecture approval" in result["reply"]  # Should prompt for approval


def test_route_inbound_reply_build_selection_by_number_and_name(monkeypatch):
    """Test selecting a build by number and name combination"""
    builds = [
        {"id": "b1", "name": "telegra-approval-responder", "status": "WAITING_FOR_ARCHITECTURE_APPROVAL", "project_path": "/project/telegra-approval-responder"},
        {"id": "b2", "name": "telegra-approval-responder", "status": "WAITING_FOR_DEPLOY_APPROVAL", "project_path": "/project/telegra-approval-responder-2"},
    ]
    monkeypatch.setattr(tb, "_find_pending_build", lambda: list(builds))

    # Test with combination input to select first build - should not auto-approve
    # We'll test the case with explicit intent to show the combined functionality works
    result = tb.route_inbound_reply(
        {"text": "1 telegra-approval-responder approve", "from": {"id": "612786480", "first_name": "Dev"}},
        pending_builds=builds,
    )

    assert result["routed"] is True
    assert "action" in result  # Should auto-approve because intent is provided
    assert result["action"] == "approve_architecture"


def test_route_inbound_reply_build_selection_disambiguation_message(monkeypatch):
    """Test that when multiple builds exist, a disambiguation message is shown"""
    builds = [
        {"id": "b1", "name": "telegra-approval-responder", "status": "WAITING_FOR_ARCHITECTURE_APPROVAL", "project_path": "/project/telegra-approval-responder"},
        {"id": "b2", "name": "telegra-approval-responder", "status": "WAITING_FOR_DEPLOY_APPROVAL", "project_path": "/project/telegra-approval-responder-2"},
    ]
    monkeypatch.setattr(tb, "_find_pending_build", lambda: list(builds))

    # Test that a proper disambiguation message is generated
    result = tb.route_inbound_reply(
        {"text": "approve", "from": {"id": "612786480", "first_name": "Dev"}},
        pending_builds=builds,
    )

    assert result["routed"] is False
    assert "Multiple builds are awaiting input" in result["reply"]
    assert "1. telegra-approval-responder" in result["reply"]
    assert "2. telegra-approval-responder" in result["reply"]
    assert "ID:" in result["reply"]


def test_route_inbound_reply_build_selection_with_same_names(monkeypatch):
    """Test build selection when multiple builds have the same name (issue scenario)"""
    builds = [
        {"id": "b1", "name": "telegra-approval-responder", "status": "WAITING_FOR_ARCHITECTURE_APPROVAL", "project_path": "/project/telegra-approval-responder"},
        {"id": "b2", "name": "telegra-approval-responder", "status": "WAITING_FOR_DEPLOY_APPROVAL", "project_path": "/project/telegra-approval-responder-2"},
    ]
    monkeypatch.setattr(tb, "_find_pending_build", lambda: list(builds))

    # Test selection by number (index) - should not auto-approve
    result = tb.route_inbound_reply(
        {"text": "2", "from": {"id": "612786480", "first_name": "Dev"}},
        pending_builds=builds,
    )

    assert result["routed"] is True
    assert "action" not in result  # Should not auto-approve
    assert result["build_id"] == "b2"  # Should indicate build b2 was selected
    assert "waiting for deploy approval" in result["reply"]  # Should prompt for approval
