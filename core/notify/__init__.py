"""core.notify — human-in-the-loop action requests (Telegram paging).

When onboarding reaches a step that needs a person (connect the phone so the
SMS Forwarder can relay an OTP, solve a CAPTCHA, present MFA, verify an ID, or
authorize a payment), the mission pauses and the operator is paged over
Telegram with **exactly** what to do. There is exactly one open request per
``(mission_id, action_type)``; the request is auto-completed when the matching
signal arrives (an OTP SMS for ``CONNECT_PHONE_FOR_SMS``, or the browser
takeover resuming).

Reuses the platform primitives: ``core.memory`` (persistence),
``core.kai_event_bus`` (events), ``core.audit_logger`` (audit), and the
existing ``core.telegram_bridge`` send helper. Never pages an OTP or any
secret.

    from core.notify import request_human_action, ActionType
    request_human_action(ActionType.CONNECT_PHONE_FOR_SMS, mission_id,
                         provider="mtn")
"""

from core.notify.human_action import (
    ActionNotFound,
    ActionStatus,
    ActionType,
    DEFAULT_EXPIRY_S,
    DEFAULT_RENOTIFY_S,
    complete,
    complete_for_sms,
    expire,
    expire_stale,
    get,
    health,
    list_pending,
    mark_available,
    normalize_action_type,
    request_human_action,
)

__all__ = [
    "ActionType", "ActionStatus", "ActionNotFound",
    "DEFAULT_EXPIRY_S", "DEFAULT_RENOTIFY_S",
    "request_human_action", "mark_available", "complete", "complete_for_sms",
    "expire", "expire_stale", "list_pending", "get", "health",
    "normalize_action_type",
]
