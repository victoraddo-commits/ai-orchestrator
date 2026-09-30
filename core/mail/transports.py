"""core.mail transports — SMTP (outbound) + IMAP (inbound), stdlib only.

Providers of note:

* **Proton Bridge** exposes local IMAP (``127.0.0.1:1143`` STARTTLS) and SMTP
  (``127.0.0.1:1025`` STARTTLS) once the interactive Bridge login is done. The
  worker talks to those localhost ports; the Bridge password lives in the vault.
* A **FixtureTransport** reads ``*.eml`` from a directory and a **StubTransport**
  holds messages in memory, so the whole pipeline is testable offline before the
  Bridge is configured.

Every transport logs nothing about credentials. ``fetch``/``send`` are lazy so
construction never touches the network.
"""

from __future__ import annotations

import email.policy
import imaplib
import smtplib
from abc import ABC
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path
from typing import Callable, Optional, Sequence
from uuid import uuid4

from core.mail.schema import MailSendResult


@dataclass
class RawMessage:
    uid: str
    raw: bytes


class MailTransport(ABC):
    """Uniform transport surface. Direction-specific methods are concrete and
    raise ``NotImplementedError`` so a transport may implement only one side."""

    def fetch_messages(self, limit: int = 50) -> list[RawMessage]:
        raise NotImplementedError("this transport does not support inbound fetch")

    def send(self, from_address: str, to: Sequence[str], subject: str,
             body_text: str, body_html: Optional[str] = None) -> MailSendResult:
        raise NotImplementedError("this transport does not support outbound send")

    def health(self) -> dict:
        return {"component": type(self).__name__, "ok": None, "detail": "not checked"}


class StubTransport(MailTransport):
    """In-memory transport for tests and pre-Bridge operation."""

    def __init__(self, messages: Optional[list[bytes]] = None):
        self._messages = list(messages or [])
        self.sent: list[dict] = []

    def fetch_messages(self, limit: int = 50) -> list[RawMessage]:
        return [RawMessage(uid=f"stub-{i}", raw=raw) for i, raw in enumerate(self._messages[:limit])]

    def send(self, from_address, to, subject, body_text, body_html=None) -> MailSendResult:
        self.sent.append({"from": from_address, "to": list(to), "subject": subject})
        return MailSendResult(ok=True, message_id=f"<stub-{uuid4().hex[:8]}@local>")


class FixtureTransport(MailTransport):
    """Reads a filesystem mailbox (a directory of ``*.eml`` files)."""

    def __init__(self, directory):
        self.directory = Path(directory)

    def fetch_messages(self, limit: int = 50) -> list[RawMessage]:
        out: list[RawMessage] = []
        if not self.directory.exists():
            return out
        for path in sorted(self.directory.glob("*.eml"))[:limit]:
            out.append(RawMessage(uid=path.name, raw=path.read_bytes()))
        return out

    def send(self, from_address, to, subject, body_text, body_html=None) -> MailSendResult:
        return MailSendResult(ok=False, error="fixture transport cannot send")


class SMTPTransport(MailTransport):
    def __init__(self, host: str, port: int, *, tls: bool = True, user: str,
                 password: str, timeout: int = 30,
                 smtp_factory: Optional[Callable] = None):
        self.host, self.port, self.tls, self.timeout = host, port, tls, timeout
        self._user, self._password = user, password
        self._factory = smtp_factory or (lambda h, p: smtplib.SMTP(h, p, timeout=timeout))

    def send(self, from_address, to, subject, body_text, body_html=None) -> MailSendResult:
        msg = EmailMessage(policy=email.policy.default)
        msg["From"] = from_address
        msg["To"] = ", ".join(to)
        msg["Subject"] = subject
        msg["Message-ID"] = f"<{uuid4().hex[:16]}@kai.local>"
        msg.set_content(body_text or "")
        if body_html:
            msg.add_alternative(body_html, subtype="html")
        try:
            with self._factory(self.host, self.port) as smtp:
                if self.tls and hasattr(smtp, "starttls"):
                    smtp.starttls()
                smtp.login(self._user, self._password)
                smtp.send_message(msg)
            return MailSendResult(ok=True, message_id=msg["Message-ID"])
        except Exception as exc:  # never leak credentials in the error
            return MailSendResult(ok=False, error=f"smtp send failed: {type(exc).__name__}")

    def health(self) -> dict:
        return {"component": "smtp", "ok": None, "detail": f"{self.host}:{self.port}",
                "tls": self.tls}


class IMAPTransport(MailTransport):
    def __init__(self, host: str, port: int, *, ssl: bool = False, user: str,
                 password: str, folder: str = "INBOX", timeout: int = 30,
                 imap_factory: Optional[Callable] = None):
        self.host, self.port, self.ssl, self.folder, self.timeout = host, port, ssl, folder, timeout
        self._user, self._password = user, password
        self._factory = imap_factory or self._default_factory

    def _default_factory(self, host, port):
        if self.ssl:
            return imaplib.IMAP4_SSL(host, port, timeout=self.timeout)
        conn = imaplib.IMAP4(host, port)
        conn.starttls()
        return conn

    def fetch_messages(self, limit: int = 50) -> list[RawMessage]:
        conn = self._factory(self.host, self.port)
        conn.login(self._user, self._password)
        conn.select(self.folder)
        status, data = conn.search(None, "UNSEEN")
        if status != "OK" or not data or not data[0]:
            status, data = conn.search(None, "ALL")
        uids = (data[0] or b"").split() if data else []
        out: list[RawMessage] = []
        for uid in uids[-limit:]:
            status, fetched = conn.fetch(uid, "(RFC822)")
            if status != "OK":
                continue
            for part in fetched or []:
                if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], bytes):
                    out.append(RawMessage(uid=uid.decode(errors="replace"), raw=part[1]))
                    break
        try:
            conn.logout()
        except Exception:
            pass
        return out

    def send(self, from_address, to, subject, body_text, body_html=None) -> MailSendResult:
        return MailSendResult(ok=False, error="imap transport cannot send")

    def health(self) -> dict:
        return {"component": "imap", "ok": None, "detail": f"{self.host}:{self.port}",
                "ssl": self.ssl, "folder": self.folder}


__all__ = ["RawMessage", "MailTransport", "StubTransport", "FixtureTransport",
           "SMTPTransport", "IMAPTransport"]
