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
        updates.append(
            {
                "update_id": msg.get("update_id", 1000 + i),
                "message": {
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
                },
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


def test_route_inbound_reply_no_pending_build():
    result = tb.route_inbound_reply(
        {"text": "hello", "from": {"id": "612786480"}},
        pending_builds=[],
    )

    assert result["routed"] is False
    assert "No build" in result["reply"]


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
