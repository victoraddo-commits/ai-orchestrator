"""FastAPI router for the payments subsystem — mounted at ``/api/payments``.

Endpoints
  POST /api/payments/initialize        operator/session (capability: payments.manage)
  GET  /api/payments/verify/{ref}      operator/session
  GET  /api/payments/{ref}             operator/session
  POST /api/payments/webhook           NO bearer auth — Paystack authenticates
                                       with the x-paystack-signature HMAC header.

The webhook reads the raw request body (never re-serialized) before verifying
the signature, then applies the event to the ledger idempotently.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core.authz import _require_write_capability
from core.payments import store as payments_store
from core.payments.keys import PaymentConfigError
from core.payments.paystack import PaystackError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/payments", tags=["payments"])

_provider = None


def get_provider():
    """Lazy singleton provider (tests monkeypatch this)."""
    global _provider
    if _provider is None:
        from core.payments.paystack import PaystackProvider

        _provider = PaystackProvider()
    return _provider


class InitializeRequest(BaseModel):
    amount: int = Field(gt=0, description="Amount in the minor unit (pesewas/kobo)")
    email: str = Field(min_length=3)
    currency: str = "GHS"
    reference: Optional[str] = None
    channels: Optional[List[str]] = None
    callback_url: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


@router.post("/initialize", dependencies=[_require_write_capability("payments.manage")])
def initialize_payment(body: InitializeRequest):
    provider = get_provider()
    try:
        result = provider.initialize(
            amount=body.amount,
            currency=body.currency,
            email=body.email,
            reference=body.reference,
            channels=body.channels,
            callback_url=body.callback_url,
            metadata=body.metadata,
        )
    except PaymentConfigError as exc:
        raise HTTPException(status_code=503, detail=f"payments not configured: {exc}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except PaystackError as exc:
        logger.warning("paystack initialize failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))
    return {"success": True, "data": result}


@router.get("/verify/{reference}", dependencies=[_require_write_capability("payments.manage")])
def verify_payment(reference: str):
    provider = get_provider()
    try:
        result = provider.verify(reference)
    except PaymentConfigError as exc:
        raise HTTPException(status_code=503, detail=f"payments not configured: {exc}")
    except PaystackError as exc:
        logger.warning("paystack verify failed for %s: %s", reference, exc)
        raise HTTPException(status_code=502, detail=str(exc))
    return {"success": True, "data": result}


@router.get("/{reference}", dependencies=[_require_write_capability("payments.manage")])
def get_payment(reference: str):
    record = payments_store.get_payment(reference)
    if record is None:
        raise HTTPException(status_code=404, detail=f"payment {reference!r} not found")
    return {"success": True, "data": record}


@router.post("/webhook")
async def paystack_webhook(request: Request):
    """Receive a Paystack event. Authenticated solely by the HMAC signature."""
    signature = request.headers.get("x-paystack-signature")
    raw_body = await request.body()

    provider = get_provider()
    if not provider.verify_webhook(raw_body, signature):
        logger.warning("paystack webhook rejected: bad or missing signature")
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        event = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Invalid webhook body")

    data = event.get("data") or {}
    reference = data.get("reference") or ""
    if not reference:
        return {"status": "ok", "handled": False, "reason": "no reference"}

    if event.get("event") == "charge.success":
        status = "success"
    else:
        status = str(data.get("status") or "unknown")

    outcome = payments_store.record_webhook(
        reference,
        event=event.get("event", ""),
        status=status,
        channel=data.get("channel", "") or "",
        gateway_response=data.get("gateway_response", "") or "",
        amount=data.get("amount"),
        currency=data.get("currency", "") or "",
        raw=event,
    )
    return {
        "status": "ok",
        "handled": True,
        "reference": reference,
        "event": event.get("event", ""),
        "duplicate": outcome["duplicate"],
    }
