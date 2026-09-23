import pytest

import core.telegram_poller as poller


def test_poll_once_routes_and_replies_to_each_message(monkeypatch):
    messages = [{"update_id": 1, "text": "hi"}, {"update_id": 2, "text": "status"}]
    monkeypatch.setattr(poller, "poll_updates", lambda poll_timeout: messages)

    routed = []
    monkeypatch.setattr(poller, "route_inbound_reply", lambda msg: routed.append(msg) or {"reply": f"echo:{msg['text']}"})

    sent = []
    monkeypatch.setattr(poller, "send_message", sent.append)

    count = poller.poll_once()

    assert count == 2
    assert routed == messages
    assert sent == ["echo:hi", "echo:status"]


def test_poll_once_uses_the_long_poll_timeout(monkeypatch):
    captured = {}

    def fake_poll_updates(poll_timeout):
        captured["poll_timeout"] = poll_timeout
        return []

    monkeypatch.setattr(poller, "poll_updates", fake_poll_updates)

    poller.poll_once()

    assert captured["poll_timeout"] == poller.POLL_TIMEOUT


def test_poll_once_does_not_send_when_there_is_no_reply(monkeypatch):
    monkeypatch.setattr(poller, "poll_updates", lambda poll_timeout: [{"update_id": 1, "text": "hi"}])
    monkeypatch.setattr(poller, "route_inbound_reply", lambda msg: {"routed": True, "reply": None})

    sent = []
    monkeypatch.setattr(poller, "send_message", sent.append)

    poller.poll_once()

    assert sent == []


def test_poll_once_continues_after_a_routing_error(monkeypatch):
    messages = [{"update_id": 1, "text": "bad"}, {"update_id": 2, "text": "good"}]
    monkeypatch.setattr(poller, "poll_updates", lambda poll_timeout: messages)

    def fake_route(msg):
        if msg["text"] == "bad":
            raise RuntimeError("boom")
        return {"reply": "ok"}

    monkeypatch.setattr(poller, "route_inbound_reply", fake_route)

    sent = []
    monkeypatch.setattr(poller, "send_message", sent.append)

    count = poller.poll_once()

    assert count == 2
    assert sent == ["ok"]


def test_poll_once_a_failed_send_does_not_raise(monkeypatch):
    monkeypatch.setattr(poller, "poll_updates", lambda poll_timeout: [{"update_id": 1, "text": "hi"}])
    monkeypatch.setattr(poller, "route_inbound_reply", lambda msg: {"reply": "hello"})

    def failing_send(text):
        raise RuntimeError("network down")

    monkeypatch.setattr(poller, "send_message", failing_send)

    count = poller.poll_once()

    assert count == 1


def test_poll_once_returns_zero_when_nothing_arrived(monkeypatch):
    monkeypatch.setattr(poller, "poll_updates", lambda poll_timeout: [])

    assert poller.poll_once() == 0


def test_run_forever_backs_off_and_keeps_going_after_a_poll_failure(monkeypatch):
    # run_forever clears any stale webhook before polling; stub the network
    # call so this test stays hermetic.
    monkeypatch.setattr(poller, "delete_webhook", lambda: True)

    # SystemExit (not Exception) is the escape hatch here -- run_forever's
    # own except Exception must NOT swallow it, or this test would hang.
    calls = {"count": 0}

    def fake_poll_once():
        calls["count"] += 1
        raise RuntimeError("network blip")

    def fake_sleep(seconds):
        if calls["count"] >= 2:
            raise SystemExit
        assert seconds == poller.ERROR_BACKOFF_SECONDS

    monkeypatch.setattr(poller, "poll_once", fake_poll_once)
    monkeypatch.setattr(poller.time, "sleep", fake_sleep)

    try:
        poller.run_forever()
    except SystemExit:
        pass

    assert calls["count"] == 2


def test_run_forever_uses_longer_backoff_on_409_conflict(monkeypatch):
    monkeypatch.setattr(poller, "delete_webhook", lambda: True)

    calls = {"count": 0}
    logged = []

    def fake_poll_once():
        calls["count"] += 1
        raise poller.TelegramConflictError("duplicate getUpdates consumer")

    def fake_sleep(seconds):
        logged.append(seconds)
        if calls["count"] >= 2:
            raise SystemExit

    monkeypatch.setattr(poller, "poll_once", fake_poll_once)
    monkeypatch.setattr(poller.time, "sleep", fake_sleep)

    try:
        poller.run_forever()
    except SystemExit:
        pass

    assert calls["count"] == 2
    assert set(logged) == {poller.CONFLICT_BACKOFF_SECONDS}
    assert poller.CONFLICT_BACKOFF_SECONDS > poller.ERROR_BACKOFF_SECONDS


def test_ensure_no_webhook_calls_the_bridge(monkeypatch):
    called = []
    monkeypatch.setattr(poller, "delete_webhook", lambda: called.append(True) or True)

    poller.ensure_no_webhook()

    assert called == [True]


def test_ensure_no_webhook_swallows_errors(monkeypatch):
    def boom():
        raise RuntimeError("telegram unreachable")

    monkeypatch.setattr(poller, "delete_webhook", boom)

    # Must not raise: a failed cleanup cannot stop the poller from starting.
    poller.ensure_no_webhook()


def test_run_forever_disables_webhooks_before_polling(monkeypatch):
    order = []

    monkeypatch.setattr(poller, "ensure_no_webhook", lambda: order.append("webhook"))

    def fake_poll_once():
        order.append("poll")
        raise SystemExit

    monkeypatch.setattr(poller, "poll_once", fake_poll_once)

    try:
        poller.run_forever()
    except SystemExit:
        pass

    assert order == ["webhook", "poll"]


# ── getUpdates retry / backoff resilience ───────────────────────────────────

def test_poll_once_retries_a_transient_502_then_succeeds(monkeypatch):
    attempts = {"n": 0}

    def flaky_poll(poll_timeout):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("Telegram getUpdates failed: HTTPError: 502")
        return [{"update_id": 1, "text": "hi"}]

    monkeypatch.setattr(poller, "poll_updates", flaky_poll)
    monkeypatch.setattr(poller, "route_inbound_reply", lambda msg: {"reply": "ok"})
    monkeypatch.setattr(poller, "send_message", lambda text: None)

    slept = []
    monkeypatch.setattr(poller.time, "sleep", slept.append)

    count = poller.poll_once()

    assert count == 1
    assert attempts["n"] == 2, "a transient 502 must be retried, not dropped"
    assert len(slept) == 1 and slept[0] > 0, "backoff must be observed"


def test_poll_once_gives_up_after_bounded_attempts(monkeypatch):
    attempts = {"n": 0}

    def always_fail(poll_timeout):
        attempts["n"] += 1
        raise RuntimeError("Telegram getUpdates failed: HTTPError: 502")

    monkeypatch.setattr(poller, "poll_updates", always_fail)

    slept = []
    monkeypatch.setattr(poller.time, "sleep", slept.append)

    with pytest.raises(RuntimeError, match="502"):
        poller.poll_once()

    assert attempts["n"] == poller.MAX_POLL_ATTEMPTS
    assert len(slept) == poller.MAX_POLL_ATTEMPTS - 1


def test_poll_once_does_not_retry_a_409_conflict(monkeypatch):
    attempts = {"n": 0}

    def conflict(poll_timeout):
        attempts["n"] += 1
        raise poller.TelegramConflictError("duplicate getUpdates consumer")

    monkeypatch.setattr(poller, "poll_updates", conflict)

    slept = []
    monkeypatch.setattr(poller.time, "sleep", slept.append)

    with pytest.raises(poller.TelegramConflictError):
        poller.poll_once()

    assert attempts["n"] == 1, "409 is not transient; run_forever owns its backoff"
    assert slept == []


class _FakeTime:
    def __init__(self, t):
        self.t = t
        self.slept = []

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.slept.append(seconds)


def test_poll_failure_logging_is_throttled(monkeypatch):
    poller._consecutive_poll_failures = 0
    poller._last_failure_log_at = 0.0

    logs = []
    monkeypatch.setattr(poller, "info", logs.append)

    fake = _FakeTime(1000.0)
    monkeypatch.setattr(poller, "time", fake)

    poller._log_poll_failure(RuntimeError("502"), poller.MAX_POLL_ATTEMPTS)
    poller._log_poll_failure(RuntimeError("502"), poller.MAX_POLL_ATTEMPTS)
    assert len(logs) == 1, "repeated failures inside the window must log once"

    fake.t += poller.FAILURE_LOG_THROTTLE_SECONDS + 1
    poller._log_poll_failure(RuntimeError("502"), poller.MAX_POLL_ATTEMPTS)
    assert len(logs) == 2


