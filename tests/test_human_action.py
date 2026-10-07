"""core.notify.human_action — lifecycle, dedup, expiry, OTP auto-complete.

Telegram is stubbed everywhere (assert text, assert no secret); the event bus
and audit logger are spied. All persistence is isolated to a tmp memory dir.
"""

import json
from pathlib import Path

import pytest

from core.notify import human_action
from core.notify.human_action import ActionStatus, ActionType


@pytest.fixture(autouse=True)
def isolated_memory(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORCHESTRATOR_MEMORY_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def clock(monkeypatch):
    fake = {"t": 1000.0}
    monkeypatch.setattr(human_action, "_clock", lambda: fake["t"])
    return fake


@pytest.fixture
def telegram(monkeypatch):
    sent = []

    def _send(text):
        sent.append(text)
        return {"ok": True, "result": {"message_id": len(sent)}}

    monkeypatch.setattr(human_action, "_send_telegram", _send)
    return sent


@pytest.fixture
def bus_spy(monkeypatch):
    events = []

    def _publish(topic, payload, *a, **k):
        events.append((topic, payload))
        return 0

    monkeypatch.setattr("core.kai_event_bus.publish", _publish)
    return events


@pytest.fixture
def audit_spy(monkeypatch):
    events = []

    def _audit(**kwargs):
        events.append(kwargs)

    monkeypatch.setattr("core.audit_logger.log_audit_event", _audit)
    return events


def test_request_creates_pending_and_emits(telegram, bus_spy, audit_spy, clock):
    aid = human_action.request_human_action(
        ActionType.CONNECT_PHONE_FOR_SMS, "mis-1", provider="mtn")
    assert aid.startswith("hact-")

    pending = human_action.list_pending(mission_id="mis-1")
    assert len(pending) == 1
    assert pending[0]["status"] == ActionStatus.PENDING.value
    assert pending[0]["action_type"] == "CONNECT_PHONE_FOR_SMS"

    assert "human.action.required" in [t for t, _ in bus_spy]
    assert any(e["event_type"] == "human.action.requested" for e in audit_spy)
    assert len(telegram) == 1


def test_idempotent_one_open_per_mission_and_type(telegram, clock):
    a1 = human_action.request_human_action(ActionType.CONNECT_PHONE_FOR_SMS, "mis-1")
    a2 = human_action.request_human_action(ActionType.CONNECT_PHONE_FOR_SMS, "mis-1")
    assert a1 == a2
    assert len(telegram) == 1  # no spam

    a3 = human_action.request_human_action(ActionType.CAPTCHA, "mis-1")
    assert a3 != a1

    a4 = human_action.request_human_action(ActionType.CONNECT_PHONE_FOR_SMS, "mis-2")
    assert a4 != a1

    assert len(human_action.list_pending()) == 3


def test_renotify_only_after_interval(telegram, clock):
    a1 = human_action.request_human_action(ActionType.CONNECT_PHONE_FOR_SMS, "mis-1")
    clock["t"] += 10  # inside the 10-min window
    assert human_action.request_human_action(
        ActionType.CONNECT_PHONE_FOR_SMS, "mis-1") == a1
    assert len(telegram) == 1

    clock["t"] += 601  # past the interval
    assert human_action.request_human_action(
        ActionType.CONNECT_PHONE_FOR_SMS, "mis-1") == a1
    assert len(telegram) == 2
    assert human_action.get(a1)["notify_count"] == 2


def test_complete_lifecycle_is_idempotent(telegram, bus_spy, audit_spy):
    aid = human_action.request_human_action(ActionType.MFA, "mis-1")
    done = human_action.complete(aid, reason="operator approved")
    assert done["status"] == ActionStatus.COMPLETED.value
    assert human_action.list_pending(mission_id="mis-1") == []
    assert "human.action.completed" in [t for t, _ in bus_spy]

    before = len(bus_spy)
    human_action.complete(aid, reason="again")
    assert len(bus_spy) == before  # no duplicate event


def test_mark_available_keeps_open_then_completes(telegram, bus_spy):
    aid = human_action.request_human_action(ActionType.CONNECT_PHONE_FOR_SMS, "mis-1")
    rec = human_action.mark_available(aid, note="phone connected")
    assert rec["status"] == ActionStatus.AVAILABLE.value
    assert "human.action.available" in [t for t, _ in bus_spy]
    assert len(human_action.list_pending(mission_id="mis-1")) == 1

    human_action.complete(aid)
    assert human_action.list_pending(mission_id="mis-1") == []


def test_expire_stale(telegram, bus_spy, clock):
    aid = human_action.request_human_action(
        ActionType.PAYMENT, "mis-1", expires_s=10)
    clock["t"] += 11
    expired = human_action.expire_stale()
    assert aid in expired
    assert human_action.get(aid)["status"] == ActionStatus.EXPIRED.value
    assert "human.action.expired" in [t for t, _ in bus_spy]
    # an expired request no longer blocks a fresh one
    aid2 = human_action.request_human_action(ActionType.PAYMENT, "mis-1")
    assert aid2 != aid


def test_regression_creates_new_request_after_expiry_within_request(telegram, clock):
    aid = human_action.request_human_action(
        ActionType.CAPTCHA, "mis-1", expires_s=5)
    clock["t"] += 6
    aid2 = human_action.request_human_action(ActionType.CAPTCHA, "mis-1")
    assert aid2 != aid


def test_message_text_says_what_to_do_and_has_no_secret(telegram, isolated_memory):
    human_action.request_human_action(
        ActionType.CONNECT_PHONE_FOR_SMS, "mis-1", provider="mtn")
    message = telegram[0]
    assert "Tailscale" in message
    assert "KAI SMS Forwarder" in message
    assert "mtn" in message
    # no OTP / token / secret value ever paged
    lowered = message.lower()
    assert "otp" not in lowered
    assert "token" not in lowered
    assert "secret" not in lowered

    raw = (isolated_memory / "human_actions.json").read_text()
    assert "token" not in raw.lower()
    assert "password" not in raw.lower()
    assert "secret" not in raw.lower()


def test_unknown_action_type_falls_back_to_other(telegram):
    aid = human_action.request_human_action("SOMETHING_NEW", "mis-1")
    assert human_action.get(aid)["action_type"] == ActionType.OTHER.value


def test_otp_arrival_auto_completes_connect_phone_request(telegram, bus_spy, audit_spy):
    from core.accounts import AccountCreate, create_account
    from core.sms import manager
    from core.sms.adapter import RawSms

    phone = "+233248077604"
    mission_id = "mis-otp-auto"
    create_account(AccountCreate(
        provider="mtn", phone=phone, account_type="sms",
        mission_id=mission_id, verification_status="PENDING"))

    aid = human_action.request_human_action(
        ActionType.CONNECT_PHONE_FOR_SMS, mission_id, provider="mtn")
    assert len(human_action.list_pending(mission_id=mission_id)) == 1

    manager.ingest_raw(RawSms(
        from_number="12345", to_number=phone,
        body="Your verification code is 482913",
        timestamp="2026-09-30T10:00:00Z"))

    assert human_action.get(aid)["status"] == ActionStatus.COMPLETED.value
    assert human_action.list_pending(mission_id=mission_id) == []
    assert "human.action.completed" in [t for t, _ in bus_spy]
    assert any(e["event_type"] == "human.action.completed" for e in audit_spy)


def test_otp_for_other_mission_does_not_complete(telegram):
    from core.accounts import AccountCreate, create_account
    from core.sms import manager
    from core.sms.adapter import RawSms

    phone = "+233248077605"
    create_account(AccountCreate(
        provider="mtn", phone=phone, account_type="sms",
        mission_id="mis-other", verification_status="PENDING"))
    aid = human_action.request_human_action(
        ActionType.CONNECT_PHONE_FOR_SMS, "mis-waiting", provider="mtn")

    manager.ingest_raw(RawSms(from_number="12345", to_number=phone,
                              body="Your verification code is 111222",
                              timestamp="2026-09-30T10:01:00Z"))

    assert human_action.get(aid)["status"] == ActionStatus.PENDING.value
