"""core.sms manager — the SMS worker's governance facade.

Inbound SMS is untrusted data. Everything funnels through here:

* normalize + persist (idempotent by a content-derived ``message_id``);
* detect + classify (OTP / security-alert / suspicious / promotional / other);
* validate sender plausibility (short-code / mobile / alphanumeric/lookalike);
* **redact the OTP from the stored body before it is ever written** and hand the
  code to the onboarding flow in memory only (short TTL, single use, zeroized);
* correlate to the Account Registry (by recipient phone) and the Mission Engine;
* emit events + audit with **no code value** anywhere.

The OTP value never touches a store, an event, an audit entry, a log, Telegram,
or the Account Registry.
"""

from __future__ import annotations

import hashlib
from typing import Optional, Union

from core import audit_logger, kai_event_bus
from core.id_generator import generate_id

from core.sms import otp, store
from core.sms.adapter import GatewayAdapter, RawSms, WebhookAdapter
from core.sms.detections import classify, detect_otp
from core.sms.normalize import (
    normalize_number,
    normalize_sender,
    numbers_match,
    redact_otp,
    sender_kind,
    to_e164,
)
from core.sms.schema import (
    Carrier,
    NormalizedSms,
    SmsClassification,
    SmsLine,
    now_iso,
    sms_vault_reference,
)
from core.sms.security import validate_sender

SOURCE = "sms_worker"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _new_line_id() -> str:
    return f"sms-line-{generate_id()}"


def _model(record: dict) -> NormalizedSms:
    return NormalizedSms(**record)


def _line_model(record: dict) -> SmsLine:
    return SmsLine(**record)


def _publish(topic: str, payload: dict, severity: str = "informational") -> None:
    kai_event_bus.publish(topic, payload, source=SOURCE, severity=severity)


def _audit(event_type: str, endpoint: str, method: str, details: Optional[dict] = None) -> None:
    audit_logger.log_audit_event(
        event_type=event_type, operator=SOURCE, endpoint=endpoint,
        method=method, status_code=200, details=details or {})


def _message_key(from_number: str, to_number: Optional[str], body: str,
                 timestamp: Optional[str]) -> str:
    raw = "|".join((from_number or "", to_number or "", body or "", timestamp or ""))
    return f"sms-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]}"


# ---------------------------------------------------------------------------
# line registry + official senders
# ---------------------------------------------------------------------------

def register_line(number: str, carrier: Union[Carrier, str] = Carrier.UNKNOWN,
                  *, label: str = "", vault_reference: Optional[str] = None,
                  enabled: bool = True, line_id: Optional[str] = None) -> SmsLine:
    line_id = line_id or _new_line_id()
    normalized = normalize_number(number)
    try:
        carrier_enum = Carrier(carrier) if not isinstance(carrier, Carrier) else carrier
    except ValueError:
        carrier_enum = Carrier.UNKNOWN
    now = now_iso()
    record = SmsLine(
        line_id=line_id, number=normalized, carrier=carrier_enum, label=label,
        vault_reference=vault_reference or sms_vault_reference(normalized),
        enabled=enabled, created_at=now, updated_at=now,
    ).model_dump(mode="json")

    def _mutate(records: list[dict]) -> list[dict]:
        records = [r for r in records if r.get("line_id") != line_id]
        records.append(record)
        return records

    store.update_lines(_mutate)
    _publish("sms.line.registered", {"line_id": line_id, "number": normalized,
                                     "carrier": carrier_enum.value})
    _audit("sms.line.register", f"sms/line/{line_id}", "CREATE",
           {"number": normalized, "carrier": carrier_enum.value})
    return _line_model(record)


def get_line(line_id: str) -> Optional[SmsLine]:
    for record in store.read_lines():
        if record.get("line_id") == line_id:
            return _line_model(record)
    return None


def list_lines() -> list[SmsLine]:
    return [_line_model(r) for r in store.read_lines()]


def _resolve_line(to_number: Optional[str]) -> Optional[str]:
    """Resolve *to_number* to a registered line's canonical number by
    normalized-E.164 equality; fall back to the normalized number itself.

    An inbound destination like ``+0013805003090`` resolves to the registered
    line ``+13805003090`` so correlation is exact, not fuzzy.
    """
    if not to_number:
        return None
    target = to_e164(to_number)
    for line in list_lines():
        if to_e164(line.number) == target:
            return line.number
    return to_number


def resolve_carrier(number: Optional[str]) -> Carrier:
    if not number:
        return Carrier.UNKNOWN
    for line in list_lines():
        if numbers_match(line.number, number):
            return line.carrier
    return Carrier.UNKNOWN


def add_official_sender(name: str) -> list[str]:
    name = (name or "").strip()
    if not name:
        return list_official_senders()

    def _mutate(records: list[dict]) -> list[dict]:
        if not any(r.get("sender") == name for r in records):
            records.append({"sender": name, "created_at": now_iso()})
        return records

    store.update_senders(_mutate)
    return list_official_senders()


def list_official_senders() -> list[str]:
    return [r["sender"] for r in store.read_senders() if r.get("sender")]


# ---------------------------------------------------------------------------
# correlation
# ---------------------------------------------------------------------------

def _find_account_for(to_number: Optional[str], account_id: Optional[str]):
    if account_id:
        try:
            from core.accounts import get_account

            account = get_account(account_id)
            if account is not None:
                return account
        except Exception:
            pass
    if not to_number:
        return None
    try:
        from core.accounts import list_accounts

        for account in list_accounts(limit=1000):
            if getattr(account, "phone", None) and numbers_match(account.phone, to_number):
                return account
    except Exception:
        pass
    return None


def correlate(sms: NormalizedSms, *, mission_id: Optional[str] = None,
              account_id: Optional[str] = None):
    """Resolve the (account, mission_id) an inbound SMS belongs to."""
    account = _find_account_for(sms.to_number, account_id)
    resolved_mission = mission_id or (account.mission_id if account else None)
    return account, resolved_mission


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------

def _store_sms(sms: NormalizedSms) -> tuple[dict, bool]:
    existing: dict = {}

    def _mutate(records: list[dict]) -> list[dict]:
        for rec in records:
            if rec.get("message_id") == sms.message_id:
                existing.update(rec)
                return records
        records.append(sms.model_dump(mode="json"))
        return records

    store.update_inbox(_mutate)
    if existing:
        return existing, False
    return sms.model_dump(mode="json"), True


def ingest_raw(raw: RawSms, *, mission_id: Optional[str] = None,
               account_id: Optional[str] = None) -> NormalizedSms:
    """Normalize → detect → validate → correlate → persist → emit. Idempotent."""
    from_n = normalize_sender(raw.from_number)
    to_n = normalize_number(raw.to_number) if raw.to_number else None
    message_id = _message_key(from_n, to_n, raw.body, raw.timestamp)

    for rec in store.read_inbox():
        if rec.get("message_id") == message_id:
            return _model(rec)

    codes = detect_otp(raw.body)
    otp_present = bool(codes)
    body_safe = redact_otp(raw.body, codes)
    classification = SmsClassification.OTP if otp_present else classify(raw.body)

    validation = validate_sender(raw.from_number, list_official_senders())
    carrier = resolve_carrier(to_n) if to_n else Carrier.UNKNOWN

    sms = NormalizedSms(
        message_id=message_id, from_number=from_n, to_number=to_n,
        line=_resolve_line(to_n),
        body=body_safe, received_at=raw.timestamp or now_iso(), carrier=carrier,
        classification=classification, otp_present=otp_present,
        sender_kind=sender_kind(raw.from_number), suspicious=validation.suspicious,
        suspicious_reasons=list(validation.notes) if validation.suspicious else [],
    )

    account, mission = correlate(sms, mission_id=mission_id, account_id=account_id)
    sms = sms.model_copy(update={
        "account_id": account.account_id if account else account_id,
        "mission_id": mission,
    })

    stored, created = _store_sms(sms)
    result = _model(stored)

    if created:
        payload = {
            "message_id": result.message_id, "from": result.from_number,
            "to": result.to_number, "classification": result.classification.value,
            "carrier": result.carrier.value, "otp_present": result.otp_present,
            "suspicious": result.suspicious, "account_id": result.account_id,
            "mission_id": result.mission_id,
        }
        _publish("sms.received", payload)
        _audit("sms.received", f"sms/{result.message_id}", "CREATE", payload)
        if result.suspicious:
            _publish("sms.suspicious", {**payload, "reasons": result.suspicious_reasons},
                     severity="critical")
            _publish("security.alert", {"kind": "suspicious_sms",
                                        "message_id": result.message_id,
                                        "from": result.from_number,
                                        "reasons": result.suspicious_reasons},
                     severity="critical")
            _audit("sms.suspicious", f"sms/{result.message_id}", "READ",
                   {"reasons": result.suspicious_reasons})
        elif result.otp_present:
            otp.handoffs.stash(codes[0], mission_id=result.mission_id,
                               account_id=result.account_id)
            _publish("sms.otp.detected", {
                "message_id": result.message_id, "from": result.from_number,
                "account_id": result.account_id, "mission_id": result.mission_id})
            _audit("sms.otp.detected", f"sms/{result.message_id}", "READ",
                   {"account_id": result.account_id, "mission_id": result.mission_id})
            # The awaited code has arrived: the human no longer needs to keep
            # the phone connected. Auto-complete the matching request (no-op
            # when there is none). Never blocks ingest.
            try:
                from core.notify import human_action

                human_action.complete_for_sms(mission_id=result.mission_id,
                                              account_id=result.account_id)
            except Exception:
                pass
    return result


def ingest_webhook(payload: dict, *, token: Optional[str] = None,
                   adapter: Optional[GatewayAdapter] = None,
                   mission_id: Optional[str] = None,
                   account_id: Optional[str] = None) -> NormalizedSms:
    """Authenticated webhook entry point. Raises adapter errors to the caller."""
    if adapter is None:
        from core.sms import vault

        adapter = WebhookAdapter(vault.load_webhook_token())
    raw = adapter.ingest_webhook(payload, token=token)
    return ingest_raw(raw, mission_id=mission_id, account_id=account_id)


# ---------------------------------------------------------------------------
# queries
# ---------------------------------------------------------------------------

def get_sms(message_id: str) -> Optional[NormalizedSms]:
    for record in store.read_inbox():
        if record.get("message_id") == message_id:
            return _model(record)
    return None


def list_sms(limit: int = 100, classification: Optional[str] = None) -> list[NormalizedSms]:
    records = store.read_inbox()
    if classification:
        records = [r for r in records if r.get("classification") == classification]
    return [_model(r) for r in records[-limit:]]


# ---------------------------------------------------------------------------
# OTP hand-off (in-memory) + verification
# ---------------------------------------------------------------------------

def pending_otp_for(*, mission_id: Optional[str] = None,
                    account_id: Optional[str] = None) -> Optional[str]:
    """Return the opaque hand-off token (never the code)."""
    return otp.handoffs.pending(mission_id=mission_id, account_id=account_id)


def consume_otp_for(*, mission_id: Optional[str] = None,
                    account_id: Optional[str] = None) -> Optional[str]:
    """Consume the code for the mission/account. Returns the code once, then None."""
    code = otp.handoffs.consume_for(mission_id=mission_id, account_id=account_id)
    if code is not None:
        _audit("sms.otp.consumed", "sms/otp", "READ",
               {"account_id": account_id, "mission_id": mission_id})
    return code


def _mark_mission_checkpoint(mission_id: Optional[str], note: str, evidence: dict) -> None:
    """Best-effort mission-state update (never blocks the flow)."""
    if not mission_id:
        return
    try:
        from core import kai_missions

        kai_missions.add_checkpoint(mission_id, note, evidence=evidence,
                                    kind="sms.verified")
    except Exception:
        pass


def mark_verified(account_id: str, *, mission_id: Optional[str] = None,
                  evidence: Optional[dict] = None):
    """After the onboarding flow used the OTP: mark verified + advance mission."""
    from core.accounts import VerificationStatus, set_verification_status

    account = set_verification_status(account_id, VerificationStatus.VERIFIED)
    mission = mission_id or account.mission_id
    payload = {"account_id": account_id, "mission_id": mission,
               "provider": account.provider}
    _mark_mission_checkpoint(mission, f"sms verification confirmed for {account_id}",
                             {**payload, **(evidence or {})})
    _publish("sms.verified", payload)
    _audit("sms.verified", f"account/{account_id}", "UPDATE", payload)
    return account


# ---------------------------------------------------------------------------
# poll loop (pull sources) + health
# ---------------------------------------------------------------------------

def poll_once(adapter: GatewayAdapter, *, mission_id: Optional[str] = None,
              account_id: Optional[str] = None) -> dict:
    summary = {"fetched": 0, "ingested": 0, "otp": 0, "suspicious": 0, "results": []}
    messages = adapter.receive()
    summary["fetched"] = len(messages)
    for raw in messages:
        sms = ingest_raw(raw, mission_id=mission_id, account_id=account_id)
        summary["ingested"] += 1
        if sms.otp_present:
            summary["otp"] += 1
        if sms.suspicious:
            summary["suspicious"] += 1
        summary["results"].append(sms.message_id)
    return summary


def health() -> dict:
    lines = store.read_lines()
    inbox = store.read_inbox()
    seen = store.read_seen()
    return {
        "component": "sms",
        "ok": True,
        "lines": len(lines),
        "enabled_lines": sum(1 for l in lines if l.get("enabled")),
        "inbox": len(inbox),
        "claimed": len(seen),
        "otp": sum(1 for m in inbox if m.get("otp_present")),
        "suspicious": sum(1 for m in inbox if m.get("suspicious")),
        "official_senders": len(list_official_senders()),
    }


__all__ = [
    "SOURCE",
    "register_line", "get_line", "list_lines", "resolve_carrier",
    "add_official_sender", "list_official_senders",
    "correlate", "ingest_raw", "ingest_webhook", "get_sms", "list_sms",
    "pending_otp_for", "consume_otp_for", "mark_verified",
    "poll_once", "health",
]
