"""core.mail manager — the email worker's governance facade.

Inbound email is untrusted data. Everything funnels through here:

* normalize + persist (idempotent by ``message_id``);
* sender authentication (SPF/DKIM/DMARC + provider-domain alignment);
* link safety routed through AgentGuard, then the **Browser Operator** (never a
  raw agent HTTP GET);
* classification + routing (verification / password-reset / security-alert /
  login-alert / suspension / suspicious) to events + audit;
* :func:`verify_email_flow` — the idempotent end-to-end verification automation.

No credential value ever passes through this module; accounts reference vault
paths only.
"""

from __future__ import annotations

from typing import Optional, Union

from core import audit_logger, kai_event_bus
from core.agentguard.guard import ActionRequest, ActionType, AgentGuard, Decision
from core.id_generator import generate_id

from core.mail import detections, store
from core.mail.parser import parse_raw_email
from core.mail.security import (
    classify_links,
    is_suspicious_sender,
    validate_sender,
)
from core.mail.schema import (
    MailAccountConfig,
    MailClassification,
    MailDirection,
    MailSendResult,
    NormalizedEmail,
    now_iso,
)

SOURCE = "mail_worker"

#: known mailbox provider domains (aliases of a provider's official domain)
PROVIDER_EMAIL_ALIASES: dict[str, list[str]] = {
    "proton": ["proton.me", "protonmail.com", "pm.me"],
    "google": ["gmail.com", "googlemail.com"],
    "microsoft": ["outlook.com", "hotmail.com", "live.com"],
}

#: page text that confirms a verification landed
_VERIFIED_MARKERS = ("verified", "confirmed", "activated", "success", "welcome")


# ---------------------------------------------------------------------------
# identity / helpers
# ---------------------------------------------------------------------------

def _new_account_id() -> str:
    return f"mail-acct-{generate_id()}"


def _model(record: dict) -> NormalizedEmail:
    return NormalizedEmail(**record)


def _account_model(record: dict) -> MailAccountConfig:
    return MailAccountConfig(**record)


def _publish(topic: str, payload: dict, severity: str = "informational") -> None:
    kai_event_bus.publish(topic, payload, source=SOURCE, severity=severity)


def _audit(event_type: str, endpoint: str, method: str, details: Optional[dict] = None) -> None:
    audit_logger.log_audit_event(
        event_type=event_type, operator=SOURCE, endpoint=endpoint,
        method=method, status_code=200, details=details or {})


def resolve_official_domains(provider_id: Optional[str] = None, account=None) -> list[str]:
    account_domains = getattr(account, "official_domains", None) if account is not None else None
    if account_domains:
        return list(account_domains)
    domains: list[str] = []
    if provider_id:
        try:
            from core.providers import get_provider

            domain = get_provider(provider_id).official_domain
            if domain:
                domains.append(domain)
        except Exception:
            pass
        for alias in PROVIDER_EMAIL_ALIASES.get(provider_id, []):
            if alias not in domains:
                domains.append(alias)
    return domains


# ---------------------------------------------------------------------------
# account registry
# ---------------------------------------------------------------------------

def register_account(address: str, provider_id: str, *, account_id: Optional[str] = None,
                     official_domains: Optional[list[str]] = None,
                     imap_host: str = "127.0.0.1", imap_port: int = 1143,
                     imap_ssl: bool = False, smtp_host: str = "127.0.0.1",
                     smtp_port: int = 1025, smtp_tls: bool = True,
                     folder: str = "INBOX", vault_reference: Optional[str] = None,
                     enabled: bool = True) -> MailAccountConfig:
    account_id = account_id or _new_account_id()
    now = now_iso()
    record = MailAccountConfig(
        account_id=account_id, address=address, provider_id=provider_id,
        official_domains=official_domains or resolve_official_domains(provider_id),
        imap_host=imap_host, imap_port=imap_port, imap_ssl=imap_ssl,
        smtp_host=smtp_host, smtp_port=smtp_port, smtp_tls=smtp_tls,
        folder=folder,
        vault_reference=vault_reference or f"secrets/mail/{address.lower()}",
        enabled=enabled, created_at=now, updated_at=now,
    ).model_dump(mode="json")

    def _mutate(records: list[dict]) -> list[dict]:
        records = [r for r in records if r.get("account_id") != account_id]
        records.append(record)
        return records

    store.update_accounts(_mutate)
    _publish("mail.account.registered", {"account_id": account_id, "provider_id": provider_id})
    _audit("mail.account.register", f"mail/{account_id}", "CREATE",
           {"provider_id": provider_id, "address": address})
    return _account_model(record)


def get_account(account_id: str) -> Optional[MailAccountConfig]:
    for record in store.read_accounts():
        if record.get("account_id") == account_id:
            return _account_model(record)
    return None


def list_accounts() -> list[MailAccountConfig]:
    return [_account_model(r) for r in store.read_accounts()]


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------

def _store_email(email: NormalizedEmail) -> tuple[NormalizedEmail, bool]:
    existing: dict = {}

    def _mutate(records: list[dict]) -> list[dict]:
        for rec in records:
            if (email.message_id and rec.get("message_id") == email.message_id) or \
               (not email.message_id and rec.get("email_id") == email.email_id):
                existing.update(rec)
                return records
        records.append(email.model_dump(mode="json"))
        return records

    store.update_inbox(_mutate)
    if existing:
        return _model(existing), False
    return email, True


def ingest_raw(raw, direction: MailDirection = MailDirection.INBOUND,
               account_id: Optional[str] = None,
               email_id: Optional[str] = None) -> NormalizedEmail:
    """Parse → validate → classify → persist → emit. Idempotent by message_id."""
    account = get_account(account_id) if account_id else None
    email = parse_raw_email(raw, direction=direction, email_id=email_id)

    domains = resolve_official_domains(email.provider_guessed, account)
    email = classify_links(email, domains)
    auth = validate_sender(email, domains)
    email = email.model_copy(update={"sender_auth": auth})

    classification = detections.classify(email.subject, email.body_text,
                                         email.body_html_sanitized)
    reasons: list[str] = []
    if is_suspicious_sender(auth):
        reasons.extend(auth.notes)
    refused = [l for l in email.links if not l.safe]
    if refused and any(l.verdict.value == "refused" for l in refused):
        reasons.extend(f"refused link: {l.host}" for l in refused if l.verdict.value == "refused")
    suspicious = bool(reasons)

    email = email.model_copy(update={
        "classification": MailClassification.SUSPICIOUS if suspicious else classification,
        "suspicious": suspicious,
        "suspicious_reasons": reasons,
    })

    stored, created = _store_email(email)
    stored_email = _model(stored.model_dump(mode="json"))

    if created:
        payload = {
            "email_id": stored_email.email_id, "message_id": stored_email.message_id,
            "classification": stored_email.classification.value,
            "from": stored_email.from_address, "subject": stored_email.subject,
            "provider": stored_email.provider_guessed, "suspicious": stored_email.suspicious,
            "attachments": len(stored_email.attachments),
        }
        _publish("email.received", payload)
        _audit("email.received", f"email/{stored_email.email_id}", "CREATE", payload)
        if stored_email.suspicious:
            _publish("email.suspicious", payload, severity="critical")
            _publish("security.alert", {
                "kind": "suspicious_email", "email_id": stored_email.email_id,
                "from": stored_email.from_address, "reasons": stored_email.suspicious_reasons},
                severity="critical")
            _audit("email.suspicious", f"email/{stored_email.email_id}", "READ",
                   {"reasons": stored_email.suspicious_reasons})
    return stored_email


def get_email(email_id: str) -> Optional[NormalizedEmail]:
    for record in store.read_inbox():
        if record.get("email_id") == email_id:
            return _model(record)
    return None


def list_emails(limit: int = 100, classification: Optional[str] = None) -> list[NormalizedEmail]:
    records = store.read_inbox()
    if classification:
        records = [r for r in records if r.get("classification") == classification]
    return [_model(r) for r in records[-limit:]]


# ---------------------------------------------------------------------------
# correlation
# ---------------------------------------------------------------------------

def correlate(email: NormalizedEmail, *, provider_id: Optional[str] = None,
              account_id: Optional[str] = None,
              mission_id: Optional[str] = None):
    from core.accounts import find_accounts, get_account as get_acct

    account = get_acct(account_id) if account_id else None
    provider = provider_id or email.provider_guessed
    if account is None and provider:
        for recipient in email.to or []:
            found = find_accounts(provider, email=recipient)
            if found:
                account = found[0]
                break
        if account is None:
            found = find_accounts(provider)
            if found:
                account = found[0]
    resolved_mission = mission_id or (account.mission_id if account else None)
    return account, resolved_mission


# ---------------------------------------------------------------------------
# verification automation
# ---------------------------------------------------------------------------

def _safe_provider_link(email: NormalizedEmail, domains: list[str]) -> Optional[object]:
    from core.browser.security import is_same_site

    for link in email.links:
        if link.safe and any(is_same_site(link.host, d) for d in domains):
            return link
    return None


def _page_confirms_verified(snapshot: dict) -> bool:
    snapshot = snapshot or {}
    hay = " ".join(str(snapshot.get(k) or "") for k in ("title", "text", "url")).lower()
    return any(marker in hay for marker in _VERIFIED_MARKERS)


def _mark_mission_checkpoint(mission_id: Optional[str], note: str, evidence: dict) -> None:
    """Best-effort mission-state update (never blocks the flow)."""
    if not mission_id:
        return
    try:
        from core import kai_missions

        kai_missions.add_checkpoint(mission_id, note, evidence=evidence,
                                    kind="email.verified")
    except Exception:
        pass


def verify_email_flow(email: NormalizedEmail, *, account_id: Optional[str] = None,
                      mission_id: Optional[str] = None,
                      browser_client=None,
                      official_domains: Optional[list[str]] = None) -> dict:
    """Detect → validate → follow (Browser Operator) → confirm → update registry.

    Fully idempotent: the same message (and the same link) is never processed
    twice — the claim runs inside an atomic flock critical section.
    """
    if email.classification != MailClassification.VERIFICATION:
        return {"handled": False, "reason": "not_verification",
                "classification": email.classification.value}

    message_key = email.message_id or email.email_id
    if not store.claim_message(f"msg:{message_key}"):
        return {"handled": False, "idempotent": True, "reason": "already_processed"}

    account, mission = correlate(email, account_id=account_id, mission_id=mission_id)
    domains = official_domains or resolve_official_domains(
        email.provider_guessed, account)

    if email.suspicious:
        _publish("email.suspicious", {"email_id": email.email_id,
                                      "reasons": email.suspicious_reasons}, severity="critical")
        _publish("security.alert", {"kind": "verification_email_suspicious",
                                    "email_id": email.email_id,
                                    "reasons": email.suspicious_reasons}, severity="critical")
        _audit("email.suspicious", f"email/{email.email_id}", "READ",
               {"reasons": email.suspicious_reasons})
        return {"handled": False, "suspicious": True, "reason": "sender_not_trusted"}

    link = _safe_provider_link(email, domains)
    if link is None:
        return {"handled": False, "reason": "no_safe_provider_link"}

    if not store.claim_message(f"link:{link.url}"):
        return {"handled": False, "idempotent": True, "reason": "link_already_followed"}

    guard = AgentGuard()
    decision = guard.check_action(ActionRequest(
        agent_id="mail_worker", user_id="kai", action_type=ActionType.EMAIL_FOLLOW_LINK,
        resource=link.url, details=f"follow verification link in {email.email_id}",
        reason="email verification"))
    if decision.decision != Decision.ALLOW:
        return {"handled": False, "reason": f"guard_{decision.decision.value}",
                "risk_level": decision.risk_level.value}

    if account is None:
        return {"handled": False, "reason": "no_correlated_account", "mission_id": mission}

    from core.accounts import VerificationStatus, set_verification_status
    from core.browser.client import BrowserOperatorClient

    client = browser_client or BrowserOperatorClient()
    identity_id = account.digital_identity or "kai"
    provider_id = account.provider or email.provider_guessed or "generic"

    session = client.open_session(identity_id, provider_id, mission_id=mission)
    session_id = session.get("session_id")
    try:
        client.perform(session_id, "navigate",
                       {"url": link.url, "enforce_domain": True})
        inspected = client.perform(session_id, "inspect", {})
        snapshot = inspected.get("snapshot", inspected)
        confirmed = _page_confirms_verified(snapshot)
    finally:
        try:
            client.end_session(session_id)
        except Exception:
            pass

    evidence = {"email_id": email.email_id, "message_id": email.message_id,
                "link": link.url, "session_id": session_id,
                "page_url": (snapshot or {}).get("url"), "confirmed": confirmed}

    if confirmed:
        set_verification_status(account.account_id, VerificationStatus.VERIFIED)
        _mark_mission_checkpoint(mission, f"email verification confirmed for {email.email_id}",
                                 evidence)
        _publish("email.verified", evidence)
        _audit("email.verified", f"email/{email.email_id}", "UPDATE", evidence)
        return {"handled": True, "verified": True, "account_id": account.account_id,
                "mission_id": mission, "link": link.url, "evidence": evidence}

    _publish("email.verification.unconfirmed", evidence)
    _audit("email.verification.unconfirmed", f"email/{email.email_id}", "READ", evidence)
    return {"handled": True, "verified": False, "account_id": account.account_id,
            "mission_id": mission, "link": link.url, "evidence": evidence}


# ---------------------------------------------------------------------------
# poll loop
# ---------------------------------------------------------------------------

def poll_once(transport, *, browser_client=None, account_id: Optional[str] = None,
              official_domains: Optional[list[str]] = None) -> dict:
    summary = {"fetched": 0, "ingested": 0, "verified": 0, "suspicious": 0,
               "followed": 0, "idempotent": 0, "results": []}
    messages = transport.fetch_messages()
    summary["fetched"] = len(messages)
    for message in messages:
        email = ingest_raw(message.raw, account_id=account_id)
        summary["ingested"] += 1
        if email.suspicious:
            summary["suspicious"] += 1
            continue
        if email.classification == MailClassification.VERIFICATION:
            result = verify_email_flow(email, account_id=account_id,
                                       browser_client=browser_client,
                                       official_domains=official_domains)
            if result.get("idempotent"):
                summary["idempotent"] += 1
            if result.get("verified"):
                summary["verified"] += 1
                summary["followed"] += 1
            summary["results"].append(result)
    return summary


# ---------------------------------------------------------------------------
# outbound
# ---------------------------------------------------------------------------

def send_outbound(account: MailAccountConfig, to: list[str], subject: str,
                  body_text: str, body_html: Optional[str] = None,
                  transport=None) -> MailSendResult:
    """Resolve creds from the vault into memory and send via SMTP."""
    if transport is None:
        from core.mail.transports import SMTPTransport
        from core.mail.vault import load_transport_credentials

        creds = load_transport_credentials(account.vault_reference) if account.vault_reference else None
        if creds is None:
            return MailSendResult(ok=False, error="no credentials available")
        transport = SMTPTransport(account.smtp_host, account.smtp_port, tls=account.smtp_tls,
                                  user=creds.user or account.address, password=creds.password)
    result = transport.send(account.address, to, subject, body_text, body_html)
    _audit("email.sent", f"mail/{account.account_id}", "CREATE",
           {"to": list(to), "subject": subject, "ok": result.ok})
    if result.ok:
        _publish("email.sent", {"account_id": account.account_id, "to": list(to),
                                "subject": subject, "message_id": result.message_id})
    return result


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------

def health() -> dict:
    accounts = store.read_accounts()
    inbox = store.read_inbox()
    seen = store.read_seen()
    return {
        "component": "mail",
        "ok": True,
        "accounts": len(accounts),
        "enabled_accounts": sum(1 for a in accounts if a.get("enabled")),
        "inbox": len(inbox),
        "claimed": len(seen),
        "suspicious": sum(1 for e in inbox if e.get("suspicious")),
    }


__all__ = [
    "SOURCE", "PROVIDER_EMAIL_ALIASES", "resolve_official_domains",
    "register_account", "get_account", "list_accounts",
    "ingest_raw", "get_email", "list_emails", "correlate",
    "verify_email_flow", "poll_once", "send_outbound", "health",
]
