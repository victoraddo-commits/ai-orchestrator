"""core.mail manager — ingest, classify, security routing, idempotency."""

import importlib
import json
from pathlib import Path

import pytest

from core.mail import manager
from core.mail.schema import MailClassification

FIX = Path(__file__).parent / "fixtures" / "mail"


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


def _raw(name: str) -> bytes:
    return (FIX / name).read_bytes()


def test_register_and_list_accounts(isolated_memory):
    acct = manager.register_account(
        address="Kai-Enzoai@protonmail.com", provider_id="proton",
        imap_host="127.0.0.1", imap_port=1143, smtp_host="127.0.0.1", smtp_port=1025,
        vault_reference="secrets/external/proton-bridge",
    )
    assert acct.account_id.startswith("mail-acct-")
    assert manager.get_account(acct.account_id).provider_id == "proton"
    assert len(manager.list_accounts()) == 1
    # credential value is never persisted — only the vault reference
    raw = (isolated_memory / "mail_accounts.json").read_text().lower()
    assert "password" not in raw


def test_ingest_emits_events_and_persists(bus_spy, audit_spy, isolated_memory):
    email = manager.ingest_raw(_raw("verification_proton.eml"))
    assert email.classification == MailClassification.VERIFICATION
    topics = [t for t, _ in bus_spy]
    assert "email.received" in topics
    assert any(e["event_type"] == "email.received" for e in audit_spy)
    assert (isolated_memory / "mail_inbox.json").exists()


def test_classify_password_reset_and_security_alert():
    assert manager.ingest_raw(_raw("password_reset.eml")).classification == MailClassification.PASSWORD_RESET
    assert manager.ingest_raw(_raw("security_alert.eml")).classification == MailClassification.SECURITY_ALERT


def test_phishing_sender_marked_suspicious_and_alerts(bus_spy, audit_spy):
    email = manager.ingest_raw(_raw("phishing_sender.eml"))
    assert email.suspicious is True
    topics = [t for t, _ in bus_spy]
    assert "email.suspicious" in topics
    assert "security.alert" in topics
    assert any(e["event_type"] == "email.suspicious" for e in audit_spy)


def test_phishing_link_refused_not_followed(bus_spy):
    email = manager.ingest_raw(_raw("phishing_link.eml"))
    assert email.suspicious is True
    assert all(not l.safe for l in email.links) or email.links == []
    assert any(t == "security.alert" for t, _ in bus_spy)


def test_ingest_is_idempotent_by_message_id(bus_spy, isolated_memory):
    manager.ingest_raw(_raw("verification_proton.eml"))
    manager.ingest_raw(_raw("verification_proton.eml"))
    loaded = json.loads((isolated_memory / "mail_inbox.json").read_text())
    records = loaded.get("records", loaded)
    assert len(records) == 1


def test_attachments_flagged(bus_spy):
    email = manager.ingest_raw(_raw("with_attachment.eml"))
    assert len(email.attachments) == 1
    assert email.attachments[0].opened is False
    assert email.attachments[0].flagged_for_review is True


def test_persistence_survives_reload(isolated_memory):
    email = manager.ingest_raw(_raw("password_reset.eml"))
    importlib.reload(manager)
    assert manager.get_email(email.email_id) is not None
