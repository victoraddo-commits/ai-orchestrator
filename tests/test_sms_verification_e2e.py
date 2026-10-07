"""Local E2E: webhook POST with an OTP -> correlation to a mission -> in-memory
hand-off consumed -> mission advanced -> OTP absent from every stored/logged
surface. Fully offline."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.sms import manager
from core.sms.server import create_app

TOKEN = "e2e-webhook-secret"
CODE = "482913"


@pytest.fixture(autouse=True)
def isolated_memory(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORCHESTRATOR_MEMORY_DIR", str(tmp_path))
    from core.sms.otp import OtpHandoffStore

    monkeypatch.setattr("core.sms.otp.handoffs", OtpHandoffStore())
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


@pytest.fixture
def checkpoint_spy(monkeypatch):
    calls = []

    def _add(mission_id, note, evidence=None, kind="note"):
        calls.append({"mission_id": mission_id, "note": note,
                      "evidence": evidence or {}, "kind": kind})
        return {"id": mission_id}

    monkeypatch.setattr("core.kai_missions.add_checkpoint", _add)
    return calls


def _account():
    from core.accounts import AccountCreate, create_account

    return create_account(AccountCreate(
        provider="mtn", phone="+233248077604", account_type="sms",
        mission_id="mis-e2e-sms", verification_status="PENDING"))


def _assert_code_absent(tmp_path: Path, bus_spy, audit_spy, caplog):
    surfaces = {
        "sms_inbox.json": (tmp_path / "sms_inbox.json"),
        "sms_lines.json": (tmp_path / "sms_lines.json"),
        "sms_seen.json": (tmp_path / "sms_seen.json"),
        "sms_senders.json": (tmp_path / "sms_senders.json"),
        "account_registry.json": (tmp_path / "account_registry.json"),
    }
    for name, path in surfaces.items():
        if path.exists():
            assert CODE not in path.read_text(), f"OTP leaked into {name}"
    assert CODE not in json.dumps([[t, p] for t, p in bus_spy])
    assert CODE not in json.dumps(audit_spy)


def test_e2e_webhook_otp_to_verified_mission(bus_spy, audit_spy, checkpoint_spy,
                                             tmp_path, caplog):
    caplog.set_level("DEBUG")
    acct = _account()
    client = TestClient(create_app(token=TOKEN))

    resp = client.post("/webhook/sms",
                       json={"from": "12345", "to": "+233248077604",
                             "body": f"Your verification code is {CODE}",
                             "timestamp": "2026-09-30T10:00:00Z"},
                       headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200
    assert resp.json()["otp_present"] is True
    assert CODE not in resp.text

    # correlated to the account + mission
    sms = manager.list_sms(limit=10)[-1]
    assert sms.account_id == acct.account_id
    assert sms.mission_id == "mis-e2e-sms"

    # hand-off is consumed in-memory exactly once
    consumed = manager.consume_otp_for(mission_id="mis-e2e-sms")
    assert consumed == CODE
    assert manager.consume_otp_for(mission_id="mis-e2e-sms") is None

    # mission advanced + account verified + event/audit emitted
    manager.mark_verified(acct.account_id, mission_id="mis-e2e-sms",
                          evidence={"message_id": sms.message_id})

    from core.accounts import VerificationStatus, get_account
    assert get_account(acct.account_id).verification_status == VerificationStatus.VERIFIED

    assert any(c["kind"] == "sms.verified" and c["mission_id"] == "mis-e2e-sms"
               for c in checkpoint_spy)
    assert "sms.verified" in [t for t, _ in bus_spy]
    assert any(e["event_type"] == "sms.verified" for e in audit_spy)

    _assert_code_absent(tmp_path, bus_spy, audit_spy, caplog)
    assert CODE not in caplog.text


def test_e2e_suspicious_sms_no_otp_handoff(bus_spy, audit_spy, checkpoint_spy,
                                           tmp_path, caplog):
    caplog.set_level("DEBUG")
    manager.add_official_sender("MyBank")
    _account()
    client = TestClient(create_app(token=TOKEN))

    resp = client.post("/webhook/sms",
                       json={"from": "MyBankk", "to": "+233248077604",
                             "body": "Security alert: suspicious sign-in"},
                       headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200
    assert resp.json()["suspicious"] is True
    assert "sms.suspicious" in [t for t, _ in bus_spy]
    assert manager.consume_otp_for(mission_id="mis-e2e-sms") is None
