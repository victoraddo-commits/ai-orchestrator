"""core.mail transports + vault credential loading (offline)."""

from pathlib import Path

import pytest

from core.mail.transports import (
    FixtureTransport,
    IMAPTransport,
    SMTPTransport,
    StubTransport,
)
from core.mail.vault import load_transport_credentials

FIX = Path(__file__).parent / "fixtures" / "mail"


def test_stub_transport_fetch_and_send():
    raw = (FIX / "verification_proton.eml").read_bytes()
    t = StubTransport(messages=[raw])
    fetched = t.fetch_messages(limit=10)
    assert len(fetched) == 1
    assert b"verif-001" in fetched[0].raw
    assert t.send("no-reply@proton.me", ["x@y.com"], "hi", "body").ok is True
    assert t.sent[0]["subject"] == "hi"


def test_fixture_transport_reads_directory():
    t = FixtureTransport(FIX)
    msgs = t.fetch_messages(limit=100)
    assert len(msgs) >= 5
    assert all(m.raw for m in msgs)


def test_smtp_transport_construction_is_lazy():
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=30):
            self.host, self.port = host, port
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def starttls(self, *a, **k):
            self.tls = True
        def login(self, user, password):
            sent.append(("login", user))
        def send_message(self, msg):
            sent.append(("send", msg["Subject"]))

    t = SMTPTransport("127.0.0.1", 1025, tls=True, user="bridge@proton.me",
                      password="bridge-app-pw", smtp_factory=lambda h, p, timeout=30: FakeSMTP(h, p))
    assert sent == []  # nothing sent at construction
    res = t.send("no-reply@proton.me", ["kai@proton.me"], "Hello", "body")
    assert res.ok is True
    assert ("login", "bridge@proton.me") in sent


def test_imap_transport_construction_is_lazy():
    class FakeIMAP:
        def __init__(self, host, port):
            self.host, self.port = host, port
        def starttls(self, *a, **k):
            pass
        def login(self, user, password):
            self.user = user
        def select(self, folder):
            return ("OK", [b""])
        def search(self, *a):
            return ("OK", [b"1 2"])
        def fetch(self, uid, spec):
            return ("OK", [(b"1", b"From: a@b.com\r\n\r\nhi")])
        def logout(self):
            pass

    t = IMAPTransport("127.0.0.1", 1143, ssl=False, user="bridge@proton.me",
                      password="pw", folder="INBOX",
                      imap_factory=lambda h, p: FakeIMAP(h, p))
    msgs = t.fetch_messages(limit=5)
    assert len(msgs) == 2
    assert b"From: a@b.com" in msgs[0].raw


def test_load_transport_credentials_parses_json_and_pair():
    calls = []

    def fake_fetch(path):
        calls.append(path)
        return '{"user": "bridge@proton.me", "password": "app-pw"}'

    creds = load_transport_credentials("secrets/external/proton-bridge", fetch=fake_fetch)
    assert creds.user == "bridge@proton.me"
    assert creds.password == "app-pw"
    assert calls == ["secrets/external/proton-bridge"]

    creds2 = load_transport_credentials(
        "secrets/external/proton", fetch=lambda p: "someone@proton.me:rawpw")
    assert creds2.user == "someone@proton.me"
    assert creds2.password == "rawpw"


def test_load_transport_credentials_missing_returns_none():
    creds = load_transport_credentials("secrets/external/nope", fetch=lambda p: None)
    assert creds is None


def test_load_transport_credentials_accepts_structured_vault_value():
    # the kai-vault machine plane returns the secret value as a dict
    creds = load_transport_credentials(
        "secrets/external/proton",
        fetch=lambda p: {"email": "someone@proton.me", "password": "vault-pw", "note": "x"})
    assert creds.user == "someone@proton.me"
    assert creds.password == "vault-pw"
