"""Regression tests for the Juris Kai Telegram bot delivery path.

Covers the 2026-09-19 double-send bug: ``_finalize_stream`` treated
Telegram's benign "message is not modified" no-op as a failure and fell
through to ``send_message``, duplicating an answer that the streaming edits
had already delivered in full.

Also covers getUpdates offset persistence so a bot restart does not
reprocess the last update it already handled.
"""

import os
import tempfile
import types
from pathlib import Path

import pytest

# Mirror test_juris_kai_multitenant.py's DB isolation. accounts.py binds its
# DB path at import time, so this must be set before importing the bot (which
# imports accounts) or the multitenant suite would run against the live DB.
os.environ.setdefault(
    "JURIS_KAI_DB_DIR", str(Path(tempfile.gettempdir()) / "juris_kai_test")
)

import core.juris_kai.bot as bot  # noqa: E402


# ---------------------------------------------------------------------------
# Double-send regression
# ---------------------------------------------------------------------------


def test_edit_is_benign_noop_only_for_not_modified():
    assert bot._edit_is_benign_noop(
        {"ok": False, "description": "Bad Request: message is not modified"}
    )
    assert not bot._edit_is_benign_noop(
        {"ok": False, "description": "Bad Request: can't parse entities"}
    )
    assert not bot._edit_is_benign_noop({"ok": True})


def test_finalize_stream_not_modified_does_not_resend(monkeypatch):
    sent = []
    monkeypatch.setattr(
        bot,
        "_edit_message_text",
        lambda *a, **k: {
            "ok": False,
            "description": (
                "Bad Request: message is not modified: specified new message "
                "content and reply markup are exactly the same"
            ),
        },
    )
    monkeypatch.setattr(bot, "send_message", lambda *a, **k: sent.append(a))

    bot._finalize_stream(1, 2, "hello world")

    assert sent == []


def test_finalize_stream_real_failure_resends_once(monkeypatch):
    sent = []
    monkeypatch.setattr(
        bot,
        "_edit_message_text",
        lambda *a, **k: {"ok": False, "description": "Bad Request: can't parse entities"},
    )
    monkeypatch.setattr(bot, "send_message", lambda *a, **k: sent.append(a))

    bot._finalize_stream(1, 2, "hello world")

    assert len(sent) == 1


def test_finalize_stream_success_does_not_resend(monkeypatch):
    sent = []
    monkeypatch.setattr(bot, "_edit_message_text", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(bot, "send_message", lambda *a, **k: sent.append(a))

    bot._finalize_stream(1, 2, "hello")

    assert sent == []


# ---------------------------------------------------------------------------
# getUpdates offset persistence
# ---------------------------------------------------------------------------


def test_offset_persisted_and_reloaded(tmp_path, monkeypatch):
    monkeypatch.setenv("JURIS_KAI_TELEGRAM_OFFSET_FILE", str(tmp_path / "offset"))

    def fake_api(method, data, timeout=35):
        return {
            "ok": True,
            "result": [
                {
                    "update_id": 1000,
                    "message": {"chat": {"id": 5}, "from": {"id": 5}, "text": "hi"},
                }
            ],
        }

    monkeypatch.setattr(bot, "telegram_api", fake_api)
    monkeypatch.setattr(bot, "send_typing", lambda chat_id: None)
    monkeypatch.setattr(bot, "handle_message", lambda update: {"chat_id": 5, "text": "reply"})
    monkeypatch.setattr(bot, "_send_guarded", lambda chat_id, result: None)

    assert bot._load_offset() is None
    assert bot.poll_updates(offset=None) == 1001
    assert bot._load_offset() == 1001


def test_run_forever_resumes_from_persisted_offset(tmp_path, monkeypatch):
    (tmp_path / "offset").write_text("4242")
    monkeypatch.setenv("JURIS_KAI_TELEGRAM_OFFSET_FILE", str(tmp_path / "offset"))
    monkeypatch.setattr(bot, "_get_bot_token", lambda: "123:abc")
    monkeypatch.setattr(bot, "_TG_BOT", None)
    monkeypatch.setattr(
        bot, "get_account_manager", lambda: types.SimpleNamespace(db=":memory:")
    )
    monkeypatch.setattr("pathlib.Path.touch", lambda self, *a, **k: None)

    seen = []

    def fake_poll(offset):
        seen.append(offset)
        raise SystemExit

    monkeypatch.setattr(bot, "poll_updates", fake_poll)

    with pytest.raises(SystemExit):
        bot.run_forever()

    assert seen == [4242]
