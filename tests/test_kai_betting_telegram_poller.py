"""Webhook self-healing tests for the @Betsportz_bot poller.

Telegram refuses getUpdates while a webhook is active. The poller clears any
webhook at startup and retries once if the 409 still says a webhook is active.
"""

import core.kai_betting.telegram_bot as telegram_bot
import core.kai_betting.telegram_poller as poller


class _FakeBot:
    def handle_update(self, *args, **kwargs):
        return {"text": ""}

    def handle_callback(self, *args, **kwargs):
        return {"text": ""}


def test_delete_webhook_calls_the_api(monkeypatch):
    captured = {}

    def fake_call(method, data=None, timeout=40):
        captured["method"] = method
        captured["data"] = data
        return {"ok": True, "result": True}

    monkeypatch.setattr(poller, "_call", fake_call)

    assert poller._delete_webhook() is True
    assert captured["method"] == "deleteWebhook"
    assert captured["data"] == {"drop_pending_updates": "false"}


def test_delete_webhook_returns_false_when_api_reports_failure(monkeypatch):
    monkeypatch.setattr(poller, "_call", lambda *a, **k: {"ok": False, "error": "boom"})

    assert poller._delete_webhook() is False


def test_run_forever_disables_webhook_before_polling(monkeypatch):
    monkeypatch.setattr(telegram_bot, "BettingTelegramBot", _FakeBot)
    monkeypatch.setattr(poller, "_set_menu", lambda: None)

    order = []
    monkeypatch.setattr(
        poller, "_delete_webhook", lambda: order.append("webhook") or True
    )

    def fake_call(method, data=None, timeout=40):
        if method == "getUpdates":
            order.append("poll")
            raise SystemExit
        return {"ok": True}

    monkeypatch.setattr(poller, "_call", fake_call)

    try:
        poller.run_forever()
    except SystemExit:
        pass

    assert order == ["webhook", "poll"]


def test_run_forever_deletes_webhook_then_retries_on_active_webhook(monkeypatch):
    monkeypatch.setattr(telegram_bot, "BettingTelegramBot", _FakeBot)
    monkeypatch.setattr(poller, "_set_menu", lambda: None)

    calls = []

    def fake_call(method, data=None, timeout=40):
        if method == "getUpdates":
            calls.append("getUpdates")
            if calls.count("getUpdates") == 1:
                return {
                    "ok": False,
                    "description": (
                        "Conflict: can't use getUpdates method while webhook is "
                        "active; use deleteWebhook to delete the webhook first"
                    ),
                }
            raise SystemExit
        if method == "deleteWebhook":
            calls.append("deleteWebhook")
            return {"ok": True, "result": True}
        return {"ok": True}

    monkeypatch.setattr(poller, "_call", fake_call)

    try:
        poller.run_forever()
    except SystemExit:
        pass

    assert calls == ["deleteWebhook", "getUpdates", "deleteWebhook", "getUpdates"]
