"""core.mail — schemas (Pydantic v2).

The normalized email model treats every message as **untrusted data**: there is
no field that can turn email content into instructions, and no credential value
is ever a field — accounts reference a vault *path* only. ``extra="forbid"``
enforces both boundaries at the model edge, mirroring ``core.identity`` /
``core.accounts`` / ``core.providers``.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def mail_vault_reference(address: str) -> str:
    """Canonical vault *path* for a mailbox credential (path only, no value)."""
    slug = address.strip().lower().replace("@", "_at_").replace(".", "_")
    return f"secrets/mail/{slug}"


class TrustLevel(str, enum.Enum):
    """Email content is *always* untrusted. Single value today; explicit so the
    invariant is visible at the model boundary."""

    UNTRUSTED = "untrusted"


class MailDirection(str, enum.Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class MailClassification(str, enum.Enum):
    VERIFICATION = "verification"
    PASSWORD_RESET = "password_reset"
    SECURITY_ALERT = "security_alert"
    LOGIN_ALERT = "login_alert"
    SUSPENSION = "suspension"
    SUSPICIOUS = "suspicious"
    NEWSLETTER = "newsletter"
    OTHER = "other"


class SenderAuth(str, enum.Enum):
    PASS = "pass"
    FAIL = "fail"
    SOFTFAIL = "softfail"
    NEUTRAL = "neutral"
    NONE = "none"
    TEMPERROR = "temperror"
    PERMERROR = "permerror"
    UNKNOWN = "unknown"


class LinkVerdict(str, enum.Enum):
    SAFE = "safe"
    SUSPICIOUS = "suspicious"
    REFUSED = "refused"


class LinkRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    anchor: str = ""
    host: str = ""
    verdict: LinkVerdict = LinkVerdict.SUSPICIOUS
    safe: bool = False


class AttachmentMeta(BaseModel):
    """Attachment *metadata* only. Attachments are never auto-opened."""

    model_config = ConfigDict(extra="forbid")

    filename: str
    content_type: str = "application/octet-stream"
    size: int = 0
    disposition: str = "attachment"
    opened: bool = False
    flagged_for_review: bool = True


class SenderAuthResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spf: SenderAuth = SenderAuth.NONE
    dkim: SenderAuth = SenderAuth.NONE
    dmarc: SenderAuth = SenderAuth.NONE
    aligned: bool = False
    from_domain: Optional[str] = None
    official_domains: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @property
    def fail_count(self) -> int:
        failures = (SenderAuth.FAIL, SenderAuth.SOFTFAIL, SenderAuth.PERMERROR)
        return sum(1 for v in (self.spf, self.dkim, self.dmarc) if v in failures)


class NormalizedEmail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email_id: str
    message_id: Optional[str] = None
    thread_id: Optional[str] = None
    direction: MailDirection = MailDirection.INBOUND
    from_address: str
    from_display: str = ""
    reply_to: Optional[str] = None
    return_path: Optional[str] = None
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    subject: str = ""
    date: Optional[str] = None
    body_text: str = ""
    body_html_sanitized: str = ""
    links: list[LinkRef] = Field(default_factory=list)
    attachments: list[AttachmentMeta] = Field(default_factory=list)
    raw_headers_hash: str = ""
    provider_guessed: Optional[str] = None
    classification: MailClassification = MailClassification.OTHER
    trust: TrustLevel = TrustLevel.UNTRUSTED
    sender_auth: SenderAuthResult = Field(default_factory=SenderAuthResult)
    suspicious: bool = False
    suspicious_reasons: list[str] = Field(default_factory=list)
    received_at: str
    new_headers: list[str] = Field(default_factory=list)
    auth_headers: dict[str, str] = Field(default_factory=dict)


class MailAccountConfig(BaseModel):
    """Mailbox transport configuration. Holds a vault *reference*, never a value."""

    model_config = ConfigDict(extra="forbid")

    account_id: str
    address: str = Field(min_length=3)
    provider_id: str
    official_domains: list[str] = Field(default_factory=list)
    imap_host: str = "127.0.0.1"
    imap_port: int = 1143
    imap_ssl: bool = False
    smtp_host: str = "127.0.0.1"
    smtp_port: int = 1025
    smtp_tls: bool = True
    folder: str = "INBOX"
    vault_reference: Optional[str] = None
    enabled: bool = True
    created_at: str
    updated_at: str


class MailCredentials(BaseModel):
    """In-memory credentials resolved from the vault. NEVER persisted or logged."""

    model_config = ConfigDict(extra="forbid")

    user: str
    password: str = Field(repr=False)


class OutboundMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_address: str
    to: list[str]
    subject: str
    body_text: str = ""
    body_html: Optional[str] = None
    reply_to: Optional[str] = None


class MailSendResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    message_id: Optional[str] = None
    error: Optional[str] = None


class LinkVerdictResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    host: str
    verdict: LinkVerdict
    reason: str = ""
    is_shortener: bool = False
    safe: bool = False
