"""core.notify.human_action — mission-linked human-action paging.

The operator's phone is not always on the network. When onboarding reaches a
step a human must perform, we *cannot* just block: we persist a pending action,
page the operator over Telegram with exactly what to do, and let the mission
resume when the signal arrives.

Design rules
------------
* **One open request per ``(mission_id, action_type)``** — a second request for
  the same pair returns the existing ``action_id`` instead of spamming.
* **One Telegram alert per action** — deduped; an optional re-notify fires only
  after ``DEFAULT_RENOTIFY_S`` (configurable via
  ``KAI_HUMAN_ACTION_RENOTIFY_S``).
* **No secret in the message** — an OTP, token, or code is never paged; the
  message says what to *do*, not the value.
* **Auto-complete** — when the matching OTP SMS arrives the
  ``CONNECT_PHONE_FOR_SMS`` request completes itself
  (:func:`complete_for_sms`).
* **Stale requests expire** — past ``expires_at`` (default 1 h).

Everything is observable: each transition publishes a ``human.action.*`` event
on the bus and writes an audit record. The Telegram send is isolated behind
``_send_telegram`` so tests can stub it and never touch the network.
"""

from __future__ import annotations

import enum
import os
import time
from datetime import datetime, timezone
from typing import Optional

from core import audit_logger, kai_event_bus
from core.id_generator import generate_id
from core.notify import store

SOURCE = "human_action"
EVENT_REQUIRED = "human.action.required"

DEFAULT_EXPIRY_S = 3600.0
DEFAULT_RENOTIFY_S = 600.0
OPEN_STATUSES = ("pending", "available")

#: Injectable clock (monotonic wall-clock seconds); tests override.
_clock = time.time


class ActionType(str, enum.Enum):
    CONNECT_PHONE_FOR_SMS = "CONNECT_PHONE_FOR_SMS"
    CAPTCHA = "CAPTCHA"
    MFA = "MFA"
    ID_VERIFICATION = "ID_VERIFICATION"
    PAYMENT = "PAYMENT"
    OTHER = "OTHER"


class ActionStatus(str, enum.Enum):
    PENDING = "pending"
    AVAILABLE = "available"
    COMPLETED = "completed"
    EXPIRED = "expired"


class ActionNotFound(KeyError):
    def __init__(self, action_id: str):
        super().__init__(f"human action not found: {action_id}")
        self.action_id = action_id


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _now() -> float:
    return _clock()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _renotify_seconds() -> float:
    raw = os.environ.get("KAI_HUMAN_ACTION_RENOTIFY_S", DEFAULT_RENOTIFY_S)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_RENOTIFY_S
    return value if value >= 0 else DEFAULT_RENOTIFY_S


def normalize_action_type(action_type) -> ActionType:
    """Coerce a string/enum to an :class:`ActionType` (unknown -> OTHER)."""
    if isinstance(action_type, ActionType):
        return action_type
    text = str(action_type or "").strip().upper()
    for member in ActionType:
        if member.value == text:
            return member
    return ActionType.OTHER


def _label(action_type: ActionType) -> str:
    return {
        ActionType.CONNECT_PHONE_FOR_SMS: "Connect your phone",
        ActionType.CAPTCHA: "Solve a CAPTCHA",
        ActionType.MFA: "Complete multi-factor authentication",
        ActionType.ID_VERIFICATION: "Verify your identity",
        ActionType.PAYMENT: "Authorize a payment",
        ActionType.OTHER: "Human action needed",
    }.get(action_type, "Human action needed")


def _default_instructions(action_type: ActionType, provider: Optional[str]) -> str:
    who = provider or "the provider"
    if action_type == ActionType.CONNECT_PHONE_FOR_SMS:
        return (
            "Join Tailscale on your phone and ensure the KAI SMS Forwarder is "
            f"running — KAI is waiting for an SMS code for {who}."
        )
    if action_type == ActionType.CAPTCHA:
        return "Open the KAI browser session and solve the CAPTCHA so KAI can continue."
    if action_type == ActionType.MFA:
        return f"Approve the multi-factor prompt for {who} on your device."
    if action_type == ActionType.ID_VERIFICATION:
        return f"Complete the identity check for {who} (KAI is waiting)."
    if action_type == ActionType.PAYMENT:
        return f"Authorize the pending payment for {who} (KAI is waiting)."
    return "A step needs you — check the KAI dashboard to continue."


def _new_record(action_type: ActionType, mission_id: Optional[str],
                instructions: str, provider: Optional[str],
                expires_s: Optional[float], now: float) -> dict:
    horizon = DEFAULT_EXPIRY_S if expires_s is None else float(expires_s)
    expires_ts = (now + horizon) if horizon and horizon > 0 else None
    text = (instructions or "").strip() or _default_instructions(action_type, provider)
    return {
        "action_id": f"hact-{generate_id()}",
        "action_type": action_type.value,
        "mission_id": mission_id,
        "provider": provider,
        "instructions": text,
        "status": ActionStatus.PENDING.value,
        "notify_count": 1,
        "created_at": _iso(now),
        "created_ts": now,
        "updated_at": _iso(now),
        "last_notified_at": _iso(now),
        "last_notified_ts": now,
        "expires_at": _iso(expires_ts) if expires_ts is not None else None,
        "expires_ts": expires_ts,
        "available_at": None,
        "completed_at": None,
        "completed_reason": None,
        "expired_at": None,
        "evidence": {},
    }


def _is_expired(record: dict, now: float) -> bool:
    expires_ts = record.get("expires_ts")
    return expires_ts is not None and now >= float(expires_ts)


def _open_for(records: list[dict], mission_id, action_type: ActionType) -> Optional[dict]:
    for record in records:
        if (record.get("mission_id") == mission_id
                and record.get("action_type") == action_type.value
                and record.get("status") in OPEN_STATUSES):
            return record
    return None


def _message_text(record: dict) -> str:
    action_type = normalize_action_type(record.get("action_type"))
    lines = [f"\U0001F64B KAI — {_label(action_type)}"]
    if record.get("provider"):
        lines.append(f"Provider: {record['provider']}")
    if record.get("mission_id"):
        lines.append(f"Mission: {record['mission_id']}")
    lines.append("")
    lines.append(record.get("instructions") or _default_instructions(action_type, record.get("provider")))
    lines.append("")
    lines.append("KAI will resume automatically once this is done. No code needed here.")
    return "\n".join(lines)


def _event_payload(record: dict) -> dict:
    return {
        "action_id": record.get("action_id"),
        "action_type": record.get("action_type"),
        "mission_id": record.get("mission_id"),
        "provider": record.get("provider"),
        "status": record.get("status"),
    }


def _publish(topic: str, payload: dict, severity: str = "important") -> None:
    kai_event_bus.publish(topic, payload, source=SOURCE, severity=severity)


def _audit(event_type: str, details: dict) -> None:
    audit_logger.log_audit_event(
        event_type=event_type, operator=SOURCE, endpoint="notify/human_action",
        method="CREATE", status_code=200, details=details)


def _send_telegram(text: str):
    """Page the operator via the existing Telegram transport (stubbable)."""
    from core.telegram_bridge import send_telegram_alert

    return send_telegram_alert(text)


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------

def request_human_action(action_type, mission_id: Optional[str], instructions: str = "",
                         provider: Optional[str] = None,
                         expires_s: Optional[float] = None,
                         *, notify: bool = True) -> str:
    """Open (or return) the single pending action for ``(mission_id, type)``.

    Fires one Telegram alert per new action, with an optional re-notify after
    the configured interval. Returns the ``action_id``.
    """
    atype = normalize_action_type(action_type)
    now = _now()
    outcome: dict = {"notify": False}

    def _mutate(records: list[dict]) -> list[dict]:
        for record in records:
            if record.get("status") in OPEN_STATUSES and _is_expired(record, now):
                record["status"] = ActionStatus.EXPIRED.value
                record["expired_at"] = _iso(now)
                record["updated_at"] = _iso(now)
        existing = _open_for(records, mission_id, atype)
        if existing is not None:
            last = float(existing.get("last_notified_ts") or 0.0)
            if (now - last) >= _renotify_seconds():
                existing["notify_count"] = int(existing.get("notify_count") or 0) + 1
                existing["last_notified_at"] = _iso(now)
                existing["last_notified_ts"] = now
                existing["updated_at"] = _iso(now)
                outcome.update(action_id=existing["action_id"], notify=True,
                               created=False, record=dict(existing))
            else:
                outcome.update(action_id=existing["action_id"], notify=False,
                               created=False, record=dict(existing))
            return records

        record = _new_record(atype, mission_id, instructions, provider, expires_s, now)
        records.append(record)
        outcome.update(action_id=record["action_id"], notify=True,
                       created=True, record=dict(record))
        return records

    store.update_actions(_mutate)

    action_id = outcome["action_id"]
    record = outcome["record"]
    if notify and outcome["notify"]:
        text = _message_text(record)
        try:
            _send_telegram(text)
        except Exception:  # noqa: BLE001 - paging must never break the mission
            pass
        if outcome.get("created"):
            _publish(EVENT_REQUIRED, _event_payload(record))
            _audit("human.action.requested", _event_payload(record))
        else:
            _publish("human.action.renotified", _event_payload(record))
            _audit("human.action.renotified", _event_payload(record))
    return action_id


def get(action_id: str) -> Optional[dict]:
    for record in store.read_actions():
        if record.get("action_id") == action_id:
            return record
    return None


def list_pending(*, mission_id: Optional[str] = None,
                 action_type=None) -> list[dict]:
    atype = normalize_action_type(action_type).value if action_type is not None else None
    rows = [r for r in store.read_actions() if r.get("status") in OPEN_STATUSES]
    if mission_id is not None:
        rows = [r for r in rows if r.get("mission_id") == mission_id]
    if atype is not None:
        rows = [r for r in rows if r.get("action_type") == atype]
    return sorted(rows, key=lambda r: r.get("created_ts") or 0)


def _transition(action_id: str, *, status: ActionStatus, extra: dict) -> Optional[dict]:
    result: dict = {}

    def _mutate(records: list[dict]) -> list[dict]:
        for record in records:
            if record.get("action_id") == action_id:
                record.update(extra)
                record["status"] = status.value
                record["updated_at"] = _iso(_now())
                result.update(record)
                return records
        return records

    store.update_actions(_mutate)
    return result or None


def mark_available(action_id: str, *, note: str = "") -> Optional[dict]:
    """The human is now present (e.g. phone connected) but not yet done."""
    now = _now()
    record = _transition(action_id, status=ActionStatus.AVAILABLE,
                         extra={"available_at": _iso(now)})
    if record:
        payload = {**_event_payload(record), "note": note}
        _publish("human.action.available", payload)
        _audit("human.action.available", payload)
    return record


def complete(action_id: str, *, reason: str = "", evidence: Optional[dict] = None) -> Optional[dict]:
    """Mark the action done exactly once (idempotent)."""
    existing = get(action_id)
    if existing is None:
        return None
    if existing.get("status") == ActionStatus.COMPLETED.value:
        return existing
    now = _now()
    record = _transition(action_id, status=ActionStatus.COMPLETED,
                         extra={"completed_at": _iso(now),
                                "completed_reason": reason,
                                "evidence": evidence or {}})
    if record:
        payload = {**_event_payload(record), "reason": reason}
        _publish("human.action.completed", payload)
        _audit("human.action.completed", payload)
    return record


def expire(action_id: str) -> Optional[dict]:
    existing = get(action_id)
    if existing is None:
        return None
    if existing.get("status") == ActionStatus.EXPIRED.value:
        return existing
    now = _now()
    record = _transition(action_id, status=ActionStatus.EXPIRED,
                         extra={"expired_at": _iso(now)})
    if record:
        payload = _event_payload(record)
        _publish("human.action.expired", payload, severity="informational")
        _audit("human.action.expired", payload)
    return record


def complete_for_sms(*, mission_id: Optional[str] = None,
                     account_id: Optional[str] = None,
                     reason: str = "otp_received") -> list[str]:
    """Auto-complete the open CONNECT_PHONE_FOR_SMS action for a mission.

    Called by the SMS worker the moment an OTP for the awaited mission lands.
    """
    if not mission_id:
        return []
    completed: list[str] = []
    for record in list_pending(mission_id=mission_id,
                               action_type=ActionType.CONNECT_PHONE_FOR_SMS):
        done = complete(record["action_id"], reason=reason,
                        evidence={"trigger": "otp_received"})
        if done:
            completed.append(record["action_id"])
    return completed


def expire_stale() -> list[str]:
    now = _now()
    expired: list[str] = []
    for record in list_pending():
        if _is_expired(record, now):
            if expire(record["action_id"]):
                expired.append(record["action_id"])
    return expired


def health() -> dict:
    records = store.read_actions()
    pending = [r for r in records if r.get("status") in OPEN_STATUSES]
    return {
        "component": "human_action",
        "ok": True,
        "total": len(records),
        "pending": len(pending),
        "by_type": {
            member.value: sum(1 for r in pending if r.get("action_type") == member.value)
            for member in ActionType
        },
    }


__all__ = [
    "SOURCE", "EVENT_REQUIRED", "DEFAULT_EXPIRY_S", "DEFAULT_RENOTIFY_S",
    "ActionType", "ActionStatus", "ActionNotFound",
    "normalize_action_type", "request_human_action", "mark_available",
    "complete", "complete_for_sms", "expire", "expire_stale", "list_pending",
    "get", "health",
]
