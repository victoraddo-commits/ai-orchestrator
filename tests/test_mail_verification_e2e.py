"""Local end-to-end: fixture mailbox -> verification email -> safe link follow
(mock browser operator) -> Account Registry VERIFIED. Fully offline."""

import shutil
from pathlib import Path

import pytest

from core.mail import manager
from core.mail.transports import FixtureTransport

FIX = Path(__file__).parent / "fixtures" / "mail"


class FakeBrowserClient:
    def __init__(self, inspect_text="Your email has been verified. Welcome!"):
        self.calls = []
        self.inspect_text = inspect_text

    def open_session(self, identity_id, provider_id, mission_id=None, headless=None):
        self.calls.append(("open_session", identity_id, provider_id, mission_id))
        return {"session_id": "sess-e2e", "identity_id": identity_id,
                "provider_id": provider_id, "mission_id": mission_id}

    def perform(self, session_id, op, params=None):
        self.calls.append((op, params))
        if op == "inspect":
            return {"op": "inspect", "snapshot": {
                "url": "https://account.proton.me/verified",
                "title": "Email verified",
                "text": self.inspect_text, "elements": [], "untrusted": True}}
        return {"op": op, "snapshot": {"url": (params or {}).get("url", ""),
                                       "title": "", "text": "", "elements": [],
                                       "untrusted": True}}

    def end_session(self, session_id):
        self.calls.append(("end_session", session_id))
        return {"session_id": session_id, "status": "ended"}


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


def _mailbox(tmp_path, names):
    box = tmp_path / "mailbox"
    box.mkdir(exist_ok=True)
    for n in names:
        shutil.copy(FIX / n, box / n)
    return box


def _account(address="Kai-Enzoai@protonmail.com"):
    from core.accounts import create_account, AccountCreate

    return create_account(AccountCreate(
        provider="proton", email=address, account_type="email",
        mission_id="mis-e2e-1", verification_status="PENDING"))


def test_e2e_email_to_verified_account(bus_spy, audit_spy, tmp_path, monkeypatch):
    # keep mission checkpoint side-effects off the production missions file
    monkeypatch.setattr(manager, "_mark_mission_checkpoint", lambda *a, **k: None)
    acct = _account()
    browser = FakeBrowserClient()

    box = _mailbox(tmp_path, ["verification_proton.eml"])
    summary = manager.poll_once(FixtureTransport(box), browser_client=browser)

    assert summary["fetched"] == 1
    assert summary["verified"] == 1

    from core.accounts import get_account, VerificationStatus
    assert get_account(acct.account_id).verification_status == VerificationStatus.VERIFIED

    ops = [c[0] for c in browser.calls]
    assert "open_session" in ops and "navigate" in ops and "inspect" in ops
    navigate = next(c for c in browser.calls if c[0] == "navigate")
    assert navigate[1]["url"].startswith("https://account.proton.me/")
    assert navigate[1].get("enforce_domain") is True

    topics = [t for t, _ in bus_spy]
    assert "email.received" in topics
    assert "email.verified" in topics
    assert any(e["event_type"] == "email.verified" for e in audit_spy)


def test_e2e_is_idempotent_second_poll_never_refollows(bus_spy, tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "_mark_mission_checkpoint", lambda *a, **k: None)
    _account()
    box = _mailbox(tmp_path, ["verification_proton.eml"])

    browser1 = FakeBrowserClient()
    s1 = manager.poll_once(FixtureTransport(box), browser_client=browser1)
    assert s1["verified"] == 1

    browser2 = FakeBrowserClient()
    s2 = manager.poll_once(FixtureTransport(box), browser_client=browser2)
    assert s2["verified"] == 0
    assert s2["idempotent"] >= 1
    assert browser2.calls == []  # same link never followed twice


def test_e2e_suspicious_verification_refused(bus_spy, tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "_mark_mission_checkpoint", lambda *a, **k: None)
    acct = _account()
    box = _mailbox(tmp_path, ["phishing_sender.eml"])
    browser = FakeBrowserClient()

    summary = manager.poll_once(FixtureTransport(box), browser_client=browser)
    assert summary["suspicious"] == 1
    assert summary["verified"] == 0
    assert browser.calls == []  # never followed a link from a suspicious email

    from core.accounts import get_account, VerificationStatus
    assert get_account(acct.account_id).verification_status != VerificationStatus.VERIFIED
    assert "security.alert" in [t for t, _ in bus_spy]


def test_e2e_lookalike_link_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "_mark_mission_checkpoint", lambda *a, **k: None)
    _account()
    box = _mailbox(tmp_path, ["lookalike_link.eml"])
    browser = FakeBrowserClient()
    summary = manager.poll_once(FixtureTransport(box), browser_client=browser)
    assert summary["verified"] == 0
    assert browser.calls == []
