"""core.sms — schemas (Pydantic v2).

SMS content is treated as **untrusted data** exactly like inbound email: no
field can turn a message into an instruction and no credential value is ever a
field. Unlike email, a verification code (OTP) may appear *inside* the body, so
:class:`NormalizedSms` stores only a **redacted** body plus an ``otp_present``
flag — the code value itself lives exclusively in the in-memory hand-off
(:mod:`core.sms.otp`). ``extra="forbid"`` enforces the boundary.
"""

from __future__ import annotations

import enum
import re
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sms_vault_reference(number: str) -> str:
    """Canonical vault *path* for a line credential (path only, no value)."""
    slug = re.sub(r"[^0-9a-z]+", "_", (number or "").strip().lower()).strip("_")
    return f"secrets/sms/lines/{slug or 'line'}"


class TrustLevel(str, enum.Enum):
    """SMS content is *always* untrusted. Single value today; explicit so the
    invariant is visible at the model boundary."""

    UNTRUSTED = "untrusted"


class SmsClassification(str, enum.Enum):
    OTP = "otp"
    SECURITY_ALERT = "security_alert"
    SUSPICIOUS = "suspicious"
    PROMOTIONAL = "promotional"
    OTHER = "other"


class SenderKind(str, enum.Enum):
    SHORT_CODE = "short_code"
    ALPHANUMERIC = "alphanumeric"
    MOBILE = "mobile"
    UNKNOWN = "unknown"


class Carrier(str, enum.Enum):
    MTN = "mtn"
    VODAFONE = "vodafone"
    AIRTELTIGO = "airteltigo"
    TELLO = "tello"
    UNKNOWN = "unknown"


class SmsLine(BaseModel):
    """A SIM / number KAI owns. Holds a vault *reference*, never a value."""

    model_config = ConfigDict(extra="forbid")

    line_id: str
    number: str
    carrier: Carrier = Carrier.UNKNOWN
    label: str = ""
    vault_reference: Optional[str] = None
    enabled: bool = True
    created_at: str
    updated_at: str


class SenderValidation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_number: str
    kind: SenderKind = SenderKind.UNKNOWN
    aligned: bool = False
    suspicious: bool = False
    notes: list[str] = Field(default_factory=list)


class NormalizedSms(BaseModel):
    """Normalized inbound SMS. ``body`` is always OTP-redacted before storage."""

    model_config = ConfigDict(extra="forbid")

    message_id: str
    from_number: str
    to_number: Optional[str] = None
    line: Optional[str] = None
    body: str = ""
    received_at: str
    carrier: Carrier = Carrier.UNKNOWN
    trust: TrustLevel = TrustLevel.UNTRUSTED
    classification: SmsClassification = SmsClassification.OTHER
    mission_id: Optional[str] = None
    account_id: Optional[str] = None
    otp_present: bool = False
    sender_kind: SenderKind = SenderKind.UNKNOWN
    suspicious: bool = False
    suspicious_reasons: list[str] = Field(default_factory=list)


class SmsIngestResult(BaseModel):
    """Webhook response — metadata only, never the code or the raw body."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    message_id: Optional[str] = None
    classification: Optional[str] = None
    otp_present: bool = False
    suspicious: bool = False
    account_id: Optional[str] = None
    mission_id: Optional[str] = None
    error: Optional[str] = None


__all__ = [
    "now_iso",
    "sms_vault_reference",
    "TrustLevel",
    "SmsClassification",
    "SenderKind",
    "Carrier",
    "SmsLine",
    "SenderValidation",
    "NormalizedSms",
    "SmsIngestResult",
]
