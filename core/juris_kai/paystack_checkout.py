"""Juris Kai Paystack checkout + tier activation.

Reuses the shared payments subsystem (``core.payments.paystack`` provider,
``core.payments.store`` ledger, ``core.payments.keys`` vault/mode gating) — it
does NOT introduce a second payment stack. Provider selection for the Telegram
purchase flow is controlled by ``JURIS_PAYMENT_PROVIDER`` (``paystack`` default,
``hubtel`` fallback); the Command Center "Test checkout" action always uses
Paystack.

Security: NO imports of ``core.build_manager``, ``core.approval``, or
``core.deployment_manager``. Keys are resolved from the vault at call time and
never logged.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any, Dict, Optional

from core.payments import store as payments_store
from core.payments.paystack import PaystackProvider

logger = logging.getLogger("juris_kai.paystack_checkout")

MODULE = "juris_kai"
CURRENCY = "GHS"
CHANNELS = ["mobile_money", "card"]
REFERENCE_PREFIX = "JURIS"
VALID_PROVIDERS = ("paystack", "hubtel")

_provider_singleton: Optional[PaystackProvider] = None


def get_paystack_provider() -> PaystackProvider:
    """Lazy Paystack provider singleton (mode/keys resolved by the provider)."""
    global _provider_singleton
    if _provider_singleton is None:
        _provider_singleton = PaystackProvider()
    return _provider_singleton


def reset_provider() -> None:
    """Drop the cached provider (tests, or after a mode change)."""
    global _provider_singleton
    _provider_singleton = None


def provider_name() -> str:
    """Active purchase provider: paystack (default) or hubtel (fallback)."""
    raw = (os.environ.get("JURIS_PAYMENT_PROVIDER", "paystack") or "paystack")
    raw = raw.strip().lower()
    return raw if raw in VALID_PROVIDERS else "paystack"


def _tier_info(tier: str) -> Optional[Dict[str, Any]]:
    from core.juris_kai.accounts import SUBSCRIPTION_TIERS

    return SUBSCRIPTION_TIERS.get(tier)


def _resolve_email(account: Dict[str, Any], email: Optional[str]) -> str:
    email = (email or account.get("email") or "").strip()
    if "@" not in email:
        raise ValueError("a valid email is required (set one with /profile email)")
    return email


def create_paystack_checkout(
    account: Dict[str, Any],
    tier: str,
    *,
    email: Optional[str] = None,
    callback_url: Optional[str] = None,
    provider: Optional[Any] = None,
) -> Dict[str, Any]:
    """Initialize a Paystack transaction for a Juris tier."""
    tier_info = _tier_info(tier)
    if not tier_info:
        raise ValueError(f"unknown tier: {tier}")
    price = float(tier_info.get("price_ghs") or 0)
    if price <= 0:
        raise ValueError(f"tier {tier} is free and needs no checkout")
    email = _resolve_email(account, email)

    metadata = {"module": MODULE, "account_id": account["account_id"], "tier": tier}
    reference = PaystackProvider.generate_reference(REFERENCE_PREFIX)
    amount_minor = PaystackProvider.to_minor_units(price)
    prov = provider or get_paystack_provider()

    # Attach the Paystack Plan so a successful payment creates a real
    # Subscription (and the tier renews) instead of a one-off transaction.
    plan_code = None
    try:
        from core.juris_kai import plans as _plans
        plan_code = _plans.plan_code_for_tier(tier, mode=prov.mode)
    except Exception:  # pricing/plan map must never block a checkout
        plan_code = None
    if plan_code:
        metadata["plan_code"] = plan_code

    result = prov.initialize(
        amount=amount_minor,
        currency=CURRENCY,
        email=email,
        reference=reference,
        channels=list(CHANNELS),
        callback_url=callback_url,
        metadata=metadata,
        plan=plan_code,
    )
    logger.info("juris paystack: initialized %s for account %s (mode=%s)",
                tier, account.get("account_id"), result.get("mode"))
    return {
        "success": True,
        "provider": "paystack",
        "tier": tier,
        "tier_name": tier_info.get("name", tier),
        "price_ghs": price,
        "amount_minor": amount_minor,
        "currency": CURRENCY,
        "email": email,
        "plan_code": plan_code,
        "reference": result.get("reference", reference),
        "authorization_url": result.get("authorization_url", ""),
        "access_code": result.get("access_code", ""),
        "mode": result.get("mode"),
        "idempotent": bool(result.get("idempotent")),
    }


def create_hubtel_checkout(account: Dict[str, Any], tier: str, *,
                           email: Optional[str] = None,
                           phone: Optional[str] = None) -> Dict[str, Any]:
    """Legacy Hubtel (mobile money) checkout — preserved fallback path."""
    from core.juris_kai.payments import get_payment_client

    tier_info = _tier_info(tier)
    if not tier_info:
        raise ValueError(f"unknown tier: {tier}")
    price = float(tier_info.get("price_ghs") or 0)
    if price <= 0:
        raise ValueError(f"tier {tier} is free and needs no checkout")

    payment_id = f"JURIS-{uuid.uuid4().hex[:16].upper()}"
    client = get_payment_client()
    result = client.request_payment(
        amount_ghs=price,
        customer_name=account.get("full_name") or account.get("account_id", ""),
        customer_phone=(phone or account.get("phone") or ""),
        description=f"{tier_info.get('name', tier)} subscription",
        payment_id=payment_id,
    )
    return {
        "success": bool(result.get("success")),
        "provider": "hubtel",
        "tier": tier,
        "tier_name": tier_info.get("name", tier),
        "price_ghs": price,
        "currency": CURRENCY,
        "email": email or account.get("email") or "",
        "reference": payment_id,
        "authorization_url": result.get("checkout_url", ""),
        "status": result.get("status"),
        "test_mode": bool(result.get("test_mode")),
        "mode": "test" if result.get("test_mode") else "live",
        "error": result.get("error"),
    }


def create_checkout(account: Dict[str, Any], tier: str, *,
                    email: Optional[str] = None,
                    callback_url: Optional[str] = None,
                    provider: Optional[str] = None) -> Dict[str, Any]:
    """Create a tier checkout with the selected provider."""
    selected = (provider or provider_name()).strip().lower()
    if selected == "hubtel":
        return create_hubtel_checkout(account, tier, email=email)
    return create_paystack_checkout(
        account, tier, email=email, callback_url=callback_url)


# ── metadata + activation ─────────────────────────────────────────────────

def _parse_metadata(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _resolve_metadata(reference: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    meta = _parse_metadata((data or {}).get("metadata"))
    if meta:
        return meta
    record = payments_store.get_payment(reference)
    if record:
        return _parse_metadata(record.get("metadata"))
    return {}


def activate_reference(reference: str, *, status: Optional[str] = None,
                       data: Optional[Dict[str, Any]] = None,
                       provider: str = "paystack") -> Dict[str, Any]:
    """Activate a Juris tier from a verified payment reference.

    Idempotent by reference: a duplicate webhook returns ``duplicate=True``
    without extending the subscription again.
    """
    from core.juris_kai.accounts import get_account_manager, SUBSCRIPTION_TIERS

    if not reference:
        return {"activated": False, "reason": "missing_reference"}
    data = data or {}
    status = str(status or data.get("status") or "").lower()
    if status != "success":
        return {"activated": False, "reference": reference,
                "reason": f"status={status or 'unknown'}"}

    meta = _resolve_metadata(reference, data)
    if meta.get("module") != MODULE:
        return {"activated": False, "reference": reference, "reason": "not_juris_kai"}
    account_id = meta.get("account_id")
    tier = meta.get("tier")
    if not account_id or not tier:
        return {"activated": False, "reference": reference,
                "reason": "incomplete_metadata"}
    if tier not in SUBSCRIPTION_TIERS:
        return {"activated": False, "reference": reference,
                "reason": f"unknown_tier:{tier}"}

    mgr = get_account_manager()
    if not mgr.get_account(account_id):
        return {"activated": False, "reference": reference, "reason": "unknown_account"}

    fresh = mgr.mark_checkout_activation(
        reference, account_id, tier, provider=provider,
        amount_minor=int(data.get("amount") or 0))
    if not fresh:
        return {"activated": False, "reference": reference, "account_id": account_id,
                "tier": tier, "duplicate": True}

    mgr.set_subscription(account_id, tier)
    mgr.record_checkout_payment(
        reference, account_id, SUBSCRIPTION_TIERS[tier].get("price_ghs", 0),
        tier, provider=provider)
    logger.info("juris paystack: activated tier %s for account %s (ref=%s)",
                tier, account_id, reference)
    return {
        "activated": True,
        "reference": reference,
        "account_id": account_id,
        "tier": tier,
        "subscription": mgr.get_active_subscription(account_id),
    }


def handle_webhook(raw_body: Any, signature: Optional[str],
                   provider: Optional[Any] = None) -> Dict[str, Any]:
    """Verify + ledger + activate a Paystack webhook. Raises on bad signature."""
    prov = provider or get_paystack_provider()
    event = prov.parse_webhook(raw_body, signature)
    data = event.get("data") or {}
    reference = data.get("reference") or ""
    event_name = event.get("event", "")
    status = "success" if event_name == "charge.success" else str(
        data.get("status") or "unknown")

    duplicate = False
    if reference:
        try:
            ledger = payments_store.record_webhook(
                reference,
                event=event_name,
                status=status,
                channel=data.get("channel", "") or "",
                gateway_response=data.get("gateway_response", "") or "",
                amount=data.get("amount"),
                currency=data.get("currency", "") or "",
                raw=event,
                mode=getattr(prov, "mode", "") or "",
            )
            duplicate = bool(ledger.get("duplicate"))
        except Exception:  # ledger write must never block activation
            logger.warning("juris paystack: ledger write failed", exc_info=True)

    activation = ({"activated": False, "reason": "no_reference"} if not reference
                  else activate_reference(reference, status=status, data=data))

    subscription = None
    try:
        from core.juris_kai import subscriptions as _subscriptions
        if event_name in _subscriptions.SUBSCRIPTION_EVENTS:
            subscription = _subscriptions.handle_subscription_event(
                event_name, data, mode=getattr(prov, "mode", ""))
    except Exception:  # subscription sync must never break the webhook ack
        logger.warning("juris paystack: subscription sync failed", exc_info=True)

    return {
        "success": True,
        "event": event_name,
        "reference": reference,
        "status": status,
        "duplicate": duplicate,
        "activation": activation,
        "subscription": subscription,
    }
