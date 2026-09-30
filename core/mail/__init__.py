"""core.mail — KAI Universal Account Registration, STEP 4 (Email Worker).

Inbound (IMAP) + outbound (SMTP) email with strong untrusted-data handling and
verification-email automation. Reuses the platform's real primitives:
``core.memory`` (persistence), ``core.kai_event_bus`` (events),
``core.audit_logger`` (audit), ``core.agentguard`` (action guard), the Provider
Registry's ``official_domain``, and the CT110 Browser Operator.

    from core.mail import poll_once, verify_email_flow

Email content is **always** untrusted: it is sanitized, sender-validated, and
its links are validated before anything is followed — and only ever through the
Browser Operator, never a raw HTTP GET.
"""

from core.mail.schema import (
    AttachmentMeta,
    LinkRef,
    LinkVerdict,
    MailAccountConfig,
    MailClassification,
    MailCredentials,
    MailDirection,
    MailSendResult,
    NormalizedEmail,
    OutboundMessage,
    SenderAuth,
    SenderAuthResult,
    TrustLevel,
    mail_vault_reference,
    now_iso,
)
from core.mail.parser import extract_links, parse_raw_email, sanitize_html
from core.mail.security import (
    SAFE_SHORTENERS,
    is_suspicious_sender,
    parse_auth_results,
    validate_link,
    validate_sender,
)
from core.mail.transports import (
    FixtureTransport,
    IMAPTransport,
    MailTransport,
    RawMessage,
    SMTPTransport,
    StubTransport,
)
from core.mail.manager import (
    correlate,
    get_account,
    get_email,
    health,
    ingest_raw,
    list_accounts,
    list_emails,
    poll_once,
    register_account,
    resolve_official_domains,
    send_outbound,
    verify_email_flow,
)
from core.mail.vault import load_transport_credentials

__all__ = [
    # schema
    "NormalizedEmail", "LinkRef", "AttachmentMeta", "SenderAuthResult",
    "MailAccountConfig", "MailCredentials", "OutboundMessage", "MailSendResult",
    "MailClassification", "SenderAuth", "LinkVerdict", "TrustLevel",
    "MailDirection", "mail_vault_reference", "now_iso",
    # parser / security
    "parse_raw_email", "sanitize_html", "extract_links",
    "parse_auth_results", "validate_sender", "is_suspicious_sender",
    "validate_link", "SAFE_SHORTENERS",
    # transports / vault
    "MailTransport", "RawMessage", "StubTransport", "FixtureTransport",
    "SMTPTransport", "IMAPTransport", "load_transport_credentials",
    # manager
    "register_account", "get_account", "list_accounts", "ingest_raw",
    "get_email", "list_emails", "correlate", "verify_email_flow", "poll_once",
    "send_outbound", "resolve_official_domains", "health",
]
