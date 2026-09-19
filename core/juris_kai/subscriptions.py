"""Keep local Juris Kai subscription state in sync with Paystack webhooks.

Handles the subscription lifecycle events Paystack emits alongside
``charge.success``:

  * ``subscription.create``      → activate/extend the local tier
  * ``invoice.payment_failed``   → mark ``attention`` and lapse access
  * ``subscription.disable``     → mark ``cancelled`` and lapse access
  * ``subscription.not_renew``   → mark ``non-renewing`` (access kept to term)
  * ``invoice.payment_success``  → mark/extend active

The account is linked by the existing local subscription row, else by the
customer email. Everything is idempotent by ``subscription_code``.

Security: NO imports of ``core.build_manager``, ``core.approval`` or
``core.deployment_manager``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict

logger = logging.getLogger("juris_kai.subscriptions")

SUBSCRIPTION_EVENTS = {
    "subscription.create",
    "subscription.disable",
    "subscription.not_renew",
    "invoice.payment_failed",
    "invoice.payment_success",
}

_ACTIVATE_EVENTS = {"subscription.create", "invoice.payment_success"}
_DISABLE_EVENTS = {"subscription.disable", "invoice.payment_failed"}


def _plan_tier(plan_code: str) -> str:
    if not plan_code:
        return ""
    try:
        from core.juris_kai import plans
        return plans.tier_for_plan_code(plan_code)
    except Exception:
        return ""


def handle_subscription_event(event_name: str, data: Dict[str, Any],
                              *, mode: str = "") -> Dict[str, Any]:
    """Apply one subscription webhook event to the local account store."""
    from core.juris_kai.accounts import get_account_manager

    mgr = get_account_manager()
    data = data or {}
    sub = data.get("subscription") if isinstance(data.get("subscription"), dict) else {}
    code = str(data.get("subscription_code")
               or sub.get("subscription_code")
               or data.get("code") or "").strip()
    email_token = data.get("email_token") or sub.get("email_token") or ""
    customer = data.get("customer") if isinstance(data.get("customer"), dict) else {}
    customer_email = (customer.get("email") or data.get("email") or "").strip()
    plan = data.get("plan") if isinstance(data.get("plan"), dict) else {}
    plan_code = str(plan.get("plan_code") or data.get("plan") or "").strip()
    tier = _plan_tier(plan_code)
    amount = int(data.get("amount") or 0)
    currency = data.get("currency") or "GHS"
    next_payment_date = data.get("next_payment_date") or sub.get("next_payment_date")

    account_id = ""
    existing = mgr.get_subscription(code) if code else None
    if existing:
        account_id = existing.get("account_id") or ""
        tier = tier or existing.get("tier") or ""
    if not account_id and customer_email:
        acct = mgr.find_by_email(customer_email)
        if acct:
            account_id = acct["account_id"]

    status = "active"
    activated = expired = False
    now_iso = datetime.now(timezone.utc).isoformat()
    expires_at = None

    if event_name in _ACTIVATE_EVENTS:
        status = "active"
        if account_id and tier:
            current = mgr.get_active_subscription(account_id)
            already = (current and current.get("is_active")
                       and current.get("tier") == tier)
            if not already:
                mgr.set_subscription(account_id, tier)
                activated = True
    elif event_name == "subscription.not_renew":
        status = "non-renewing"
    elif event_name in _DISABLE_EVENTS:
        status = "cancelled" if event_name == "subscription.disable" else "attention"
        expires_at = now_iso
        if account_id:
            expired = mgr.expire_subscription(account_id)

    if code:
        mgr.upsert_subscription(
            code, account_id=account_id, tier=tier, plan_code=plan_code,
            status=status, email_token=str(email_token or ""),
            customer_email=customer_email, amount_minor=amount,
            currency=currency, next_payment_date=next_payment_date,
            expires_at=expires_at, raw=data,
        )
    mgr.log_security_event("paystack", "subscription_event",
                           f"{event_name} code={code or '-'} "
                           f"account={account_id or '-'} status={status}")
    logger.info("juris subscription %s: code=%s account=%s tier=%s status=%s",
                event_name, code or "-", account_id or "-", tier or "-", status)
    return {
        "handled": True,
        "event": event_name,
        "subscription_code": code,
        "account_id": account_id,
        "tier": tier,
        "status": status,
        "activated": activated,
        "expired": expired,
        "mode": mode,
    }
