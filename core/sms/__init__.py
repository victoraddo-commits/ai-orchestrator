"""core.sms — KAI Universal Account Registration, STEP 5 (SMS / OTP Worker).

Inbound SMS with strong untrusted-data handling and a strict OTP policy: a
verification code is detected, correlated to the mission/account awaiting
verification, and handed to the onboarding flow **in memory only** (short TTL,
single use, zeroized). It is never stored, logged, published, sent to Telegram,
or written to the Account Registry.

Reuses the platform's real primitives: ``core.memory`` (persistence),
``core.kai_event_bus`` (events), ``core.audit_logger`` (audit),
``core.agentguard`` (action guard), the Account Registry, the Mission Engine,
and the kai-vault machine plane (webhook token).

    from core.sms import ingest_webhook, consume_otp_for, mark_verified
"""

from core.sms.schema import (
    Carrier,
    NormalizedSms,
    SenderKind,
    SenderValidation,
    SmsClassification,
    SmsIngestResult,
    SmsLine,
    TrustLevel,
    now_iso,
    sms_vault_reference,
)
from core.sms.detections import classify, detect_otp, has_otp
from core.sms.normalize import (
    normalize_number,
    normalize_sender,
    numbers_match,
    redact_otp,
    sender_kind,
    to_e164,
)
from core.sms.security import validate_sender
from core.sms.otp import (
    DEFAULT_TTL_SECONDS,
    OtpHandoffStore,
    consume_otp,
    handoffs,
    pending_otp,
    stash_otp,
)
from core.sms.adapter import (
    FixtureAdapter,
    GatewayAdapter,
    RawSms,
    WebhookAdapter,
    WebhookAuthError,
    WebhookPayloadError,
    parse_payload,
)
from core.sms.manager import (
    add_official_sender,
    consume_otp_for,
    correlate,
    get_line,
    get_sms,
    health,
    ingest_raw,
    ingest_webhook,
    list_lines,
    list_official_senders,
    list_sms,
    mark_verified,
    pending_otp_for,
    poll_once,
    register_line,
    resolve_carrier,
)

__all__ = [
    # schema
    "NormalizedSms", "SmsLine", "SmsClassification", "SenderKind", "Carrier",
    "TrustLevel", "SenderValidation", "SmsIngestResult", "sms_vault_reference", "now_iso",
    # detections / normalize / security
    "detect_otp", "has_otp", "classify", "to_e164", "normalize_number",
    "normalize_sender", "numbers_match", "redact_otp", "sender_kind", "validate_sender",
    # otp hand-off
    "OtpHandoffStore", "handoffs", "stash_otp", "consume_otp", "pending_otp",
    "DEFAULT_TTL_SECONDS",
    # adapters
    "GatewayAdapter", "WebhookAdapter", "FixtureAdapter", "RawSms", "parse_payload",
    "WebhookAuthError", "WebhookPayloadError",
    # manager
    "register_line", "get_line", "list_lines", "resolve_carrier",
    "add_official_sender", "list_official_senders", "correlate", "ingest_raw",
    "ingest_webhook", "get_sms", "list_sms", "pending_otp_for", "consume_otp_for",
    "mark_verified", "poll_once", "health",
]
