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
