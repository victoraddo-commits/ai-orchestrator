"""core.sms manager — ingest, classify, OTP policy, correlation, events, audit."""

import json
from pathlib import Path

import pytest

from core.sms import manager
from core.sms.adapter import RawSms
from core.sms.schema import Carrier, SmsClassification

FIX = Path(__file__).parent / "fixtures" / "sms"


@pytest.fixture(autouse=True)
def isolated_memory(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORCHESTRATOR_MEMORY_DIR", str(tmp_path))
    return tmp_path


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


def _account(phone="+233248077604", mission_id="mis-sms-1"):
    from core.accounts import AccountCreate, create_account

    return create_account(AccountCreate(
        provider="mtn", phone=phone, account_type="sms",
        mission_id=mission_id, verification_status="PENDING"))


def test_register_and_list_lines(isolated_memory):
    line = manager.register_line("+233248077604", carrier="mtn", label="MTN SIM")
    assert line.line_id.startswith("sms-line-")
    assert manager.get_line(line.line_id).carrier == Carrier.MTN
    assert len(manager.list_lines()) == 1
    raw = (isolated_memory / "sms_lines.json").read_text().lower()
    assert "password" not in raw and "token" not in raw


def test_resolve_carrier_from_registered_line():
    manager.register_line("+233248077604", carrier="mtn")
    manager.register_line("+13805003090", carrier="tello")
    assert manager.resolve_carrier("+233248077604") == Carrier.MTN
    assert manager.resolve_carrier("+1 (380) 500-3090") == Carrier.TELLO
    assert manager.resolve_carrier("+15551234567") == Carrier.UNKNOWN


def test_ingest_emits_received_event_and_persists(bus_spy, audit_spy, isolated_memory):
    sms = manager.ingest_raw(RawSms(from_number="+15551234567",
                                    to_number="+233248077604",
                                    body="Hello world"))
    topics = [t for t, _ in bus_spy]
    assert "sms.received" in topics
    assert any(e["event_type"] == "sms.received" for e in audit_spy)
    assert (isolated_memory / "sms_inbox.json").exists()
    assert sms.classification == SmsClassification.OTHER


def test_ingest_is_idempotent(bus_spy, isolated_memory):
    raw = RawSms(from_number="+15551234567", to_number="+233248077604",
                 body="Hello world", timestamp="2026-09-30T10:00:00Z")
    manager.ingest_raw(raw)
    manager.ingest_raw(raw)
    loaded = json.loads((isolated_memory / "sms_inbox.json").read_text())
    records = loaded.get("records", loaded)
    assert len(records) == 1


def test_otp_ingest_publishes_detected_and_stashes(bus_spy, audit_spy, isolated_memory):
    manager.ingest_raw(RawSms(from_number="12345", to_number="+233248077604",
                              body="Your verification code is 482913"))
    topics = [t for t, _ in bus_spy]
    assert "sms.otp.detected" in topics
    assert any(e["event_type"] == "sms.otp.detected" for e in audit_spy)
    # the OTP value is NEVER written to the store
    raw = (isolated_memory / "sms_inbox.json").read_text()
    assert "482913" not in raw
    assert "[REDACTED]" in raw


def test_otp_and_body_never_in_events_or_audit(bus_spy, audit_spy):
    manager.ingest_raw(RawSms(from_number="12345", to_number="+233248077604",
                              body="Your verification code is 482913"))
    blob = json.dumps([[t, p] for t, p in bus_spy]) + json.dumps(audit_spy)
    assert "482913" not in blob


def test_suspicious_sms_marked_and_alerted(bus_spy, audit_spy):
    manager.add_official_sender("MyBank")
    sms = manager.ingest_raw(RawSms(from_number="MyBankk", to_number="+233248077604",
                                    body="Security alert: verify your account now"))
    assert sms.suspicious is True
    assert sms.from_number == "MyBankk"
    topics = [t for t, _ in bus_spy]
    assert "sms.suspicious" in topics
    assert "security.alert" in topics
    assert any(e["event_type"] == "sms.suspicious" for e in audit_spy)


def test_correlate_by_recipient_phone():
    acct = _account(phone="+233248077604", mission_id="mis-sms-9")
    sms = manager.ingest_raw(RawSms(from_number="12345", to_number="+233 24 807 7604",
                                    body="Your code is 111222"))
    account, mission = manager.correlate(sms)
    assert account is not None and account.account_id == acct.account_id
    assert mission == "mis-sms-9"
    stored = manager.get_sms(sms.message_id)
    assert stored.account_id == acct.account_id
    assert stored.mission_id == "mis-sms-9"


def test_consume_otp_for_mission_returns_code_then_gone():
    _account(phone="+233248077604", mission_id="mis-sms-consume")
    sms = manager.ingest_raw(RawSms(from_number="12345", to_number="+233248077604",
                                    body="Your code is 482913"))
    assert manager.consume_otp_for(mission_id=sms.mission_id) == "482913"
    assert manager.consume_otp_for(mission_id=sms.mission_id) is None


def test_health(isolated_memory):
    manager.register_line("+233248077604", carrier="mtn")
    h = manager.health()
    assert h["component"] == "sms"
    assert h["lines"] == 1


def test_poll_once_over_fixture_source(bus_spy, isolated_memory):
    from core.sms.adapter import FixtureAdapter

    summary = manager.poll_once(FixtureAdapter(FIX))
    assert summary["fetched"] >= 1
    assert summary["ingested"] == summary["fetched"]


def test_service_run_once_writes_status(isolated_memory):
    from core.sms import service

    summary = service.run_once(fixture_dir=str(FIX))
    assert summary["ingested"] >= 1
    assert (isolated_memory / "sms_watch_status.json").exists()
