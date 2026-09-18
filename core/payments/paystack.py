"""Paystack provider for the Kai payments subsystem.

Implements the transaction lifecycle used by every Kai money flow:

  * ``initialize``      — POST /transaction/initialize (redirect/checkout URL)
  * ``verify``          — GET  /transaction/verify/:reference
  * ``refund``          — POST /refund
  * ``verify_webhook``  — HMAC-SHA512 of the raw body with the secret key

Amounts are ALWAYS integers in the minor unit of the currency (GHS → pesewas,
NGN → kobo). The secret key is never logged and never persisted.

Docs: https://paystack.com/docs/api/transaction/
      https://paystack.com/docs/payments/webhooks/
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx

from core.payments import keys as _keys
from core.payments import store as _store

logger = logging.getLogger(__name__)

VALIDATE_RE = re.compile(r"^[A-Za-z0-9.\-=]+$")

# Currencies Paystack supports for initialize/verify. GHS is the Kai default.
SUPPORTED_CURRENCIES = {
    "NGN", "GHS", "ZAR", "KES", "USD", "XOF", "EGP", "RWF",
    "TZS", "UGX", "ZMW", "MWK", "MZN", "AOA", "CDF", "SLL", "LRD",
}

DEFAULT_CHANNELS = ["card", "mobile_money"]

# Ghana mobile-money provider codes from the Paystack payment-channels docs.
GH_MOMO_PROVIDERS = {
    "mtn": "MTN",
    "vod": "Telecel",
    "telecel": "Telecel",
    "atl": "AirtelTigo",
    "airteltigo": "AirtelTigo",
}

TERMINAL_SUCCESS = "success"


class PaystackError(RuntimeError):
    """Paystack returned an error (or an unreachable transport)."""

    def __init__(self, message: str, *, status_code: Optional[int] = None, payload: Optional[dict] = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload or {}


class PaystackProvider:
    """Reusable Paystack client. Inject `http_client` for tests."""

    provider_name = "paystack"

    def __init__(
        self,
        secret_key: Optional[str] = None,
        public_key: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        mode: Optional[str] = None,
        store: Any = None,
        http_client: Optional[Any] = None,
        timeout: float = 30.0,
    ):
        self._mode = mode or _keys.mode()
        self._secret_key = secret_key
        self._public_key = public_key
        self._base_url = (base_url or _keys.API_BASE).rstrip("/")
        self._store = store or _store
        self._client = http_client
        self._timeout = timeout

    # ── introspection ───────────────────────────────────────────────────

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def is_live(self) -> bool:
        return self._mode == "live"

    @property
    def public_key_value(self) -> Optional[str]:
        if self._public_key:
            return self._public_key
        try:
            return _keys.public_key()
        except _keys.PaymentConfigError:
            return None

    # ── key handling ────────────────────────────────────────────────────

    def _resolve_secret(self) -> str:
        if self.is_live and not _keys.live_allowed():
            raise _keys.PaymentConfigError(
                "live mode requires PAYSTACK_ALLOW_LIVE=true before live keys are used"
            )
        if self._secret_key:
            return self._secret_key
        return _keys.secret_key()

    # ── HTTP ────────────────────────────────────────────────────────────

    def _request(self, method: str, path: str, *, json_body: Optional[dict] = None):
        secret = self._resolve_secret()
        created = self._client is None
        client = self._client if self._client is not None else httpx.Client()
        headers = {
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
        }
        url = f"{self._base_url}{path}"
        try:
            response = client.request(
                method, url, json=json_body, headers=headers, timeout=self._timeout
            )
        except httpx.HTTPError as exc:
            raise PaystackError(
                f"Paystack transport error: {type(exc).__name__}"
            ) from exc
        finally:
            if created:
                client.close()

        try:
            payload = response.json()
        except ValueError as exc:
            raise PaystackError(
                f"Paystack returned non-JSON response (HTTP {response.status_code})",
                status_code=response.status_code,
            ) from exc

        if response.status_code >= 500:
            raise PaystackError(
                "Paystack server error",
                status_code=response.status_code,
                payload=payload,
            )
        return payload, response.status_code

    # ── initialize ──────────────────────────────────────────────────────

    def initialize(
        self,
        amount: int,
        currency: str = "GHS",
        email: str = "",
        reference: Optional[str] = None,
        channels: Optional[List[str]] = None,
        callback_url: Optional[str] = None,
        metadata: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Create a Paystack transaction and return its checkout URL."""
        if isinstance(amount, bool) or not isinstance(amount, int):
            raise ValueError("amount must be an integer in the minor unit (e.g. pesewas)")
        if amount <= 0:
            raise ValueError("amount must be greater than zero")
        currency = (currency or "GHS").upper()
        if currency not in SUPPORTED_CURRENCIES:
            raise ValueError(f"unsupported currency {currency!r}")
        if not email or "@" not in email:
            raise ValueError("a valid customer email is required")
        if not reference:
            reference = self.generate_reference()
        if not VALIDATE_RE.match(reference):
            raise ValueError(
                "reference may only contain alphanumerics and '-', '.', '='"
            )

        # Idempotency: reuse a previously-initialized authorization URL.
        existing = self._store.get_payment(reference)
        if existing and existing.get("authorization_url"):
            return self._result(existing, idempotent=True)

        body: Dict[str, Any] = {
            "email": email,
            "amount": amount,
            "currency": currency,
            "reference": reference,
        }
        if channels:
            body["channels"] = [c.lower() for c in channels]
        if callback_url:
            body["callback_url"] = callback_url
        if metadata:
            body["metadata"] = metadata if isinstance(metadata, str) else json.dumps(metadata)

        payload, status_code = self._request(
            "POST", "/transaction/initialize", json_body=body
        )
        if not payload.get("status"):
            raise PaystackError(
                payload.get("message") or "Paystack initialize failed",
                status_code=status_code,
                payload=payload,
            )

        data = payload.get("data") or {}
        record = self._store.record_initialized(
            reference=reference,
            amount=amount,
            currency=currency,
            email=email,
            authorization_url=data.get("authorization_url", ""),
            access_code=data.get("access_code", ""),
            mode=self._mode,
            requested_channel=",".join(channels or []),
            metadata=metadata,
            raw=payload,
        )
        result = self._result(record, idempotent=False)
        result["raw"] = payload
        return result

    # ── verify ──────────────────────────────────────────────────────────

    def verify(self, reference: str) -> Dict[str, Any]:
        """Confirm a transaction's status and persist the result."""
        payload, status_code = self._request(
            "GET", f"/transaction/verify/{quote(str(reference), safe='')}"
        )
        if not payload.get("status"):
            raise PaystackError(
                payload.get("message") or "Paystack verify failed",
                status_code=status_code,
                payload=payload,
            )
        data = payload.get("data") or {}
        record = self._store.update_verified(
            reference,
            status=str(data.get("status", "unknown")),
            channel=data.get("channel", "") or "",
            gateway_response=data.get("gateway_response", "") or "",
            amount=data.get("amount"),
            currency=data.get("currency", "") or "",
            raw=payload,
        )
        result = self._result(record)
        result["raw"] = payload
        return result

    # ── refund ──────────────────────────────────────────────────────────

    def refund(self, reference: str, amount: Optional[int] = None) -> Dict[str, Any]:
        """Request a refund for a transaction (full or partial)."""
        body: Dict[str, Any] = {"transaction": reference}
        if amount is not None:
            if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
                raise ValueError("refund amount must be a positive integer (minor unit)")
            body["amount"] = amount
        payload, status_code = self._request("POST", "/refund", json_body=body)
        if not payload.get("status"):
            raise PaystackError(
                payload.get("message") or "Paystack refund failed",
                status_code=status_code,
                payload=payload,
            )
        data = payload.get("data") or {}
        self._store.update_verified(
            reference, status=str(data.get("status", "refund_pending")),
            gateway_response=data.get("status", "") or "", raw=payload,
        )
        return {
            "provider": "paystack",
            "reference": reference,
            "status": data.get("status", "pending"),
            "amount": amount,
            "raw": payload,
        }

    # ── webhook ─────────────────────────────────────────────────────────

    def verify_webhook(self, raw_body: Any, signature: Optional[str]) -> bool:
        """Constant-time check of ``x-paystack-signature``.

        The signature is HMAC-SHA512 of the *raw* request body, keyed with the
        Paystack secret key.
        """
        if raw_body is None or not signature:
            return False
        if isinstance(raw_body, str):
            raw_body = raw_body.encode("utf-8")
        try:
            secret = self._secret_key or _keys.secret_key()
        except _keys.PaymentConfigError:
            return False
        digest = hmac.new(
            secret.encode("utf-8"), raw_body, hashlib.sha512
        ).hexdigest()
        return hmac.compare_digest(digest, str(signature))

    def parse_webhook(self, raw_body: Any, signature: Optional[str]) -> Dict[str, Any]:
        """Verify then parse a webhook body. Raises PaystackError on bad sig."""
        if not self.verify_webhook(raw_body, signature):
            raise PaystackError("invalid Paystack webhook signature")
        if isinstance(raw_body, (bytes, bytearray)):
            raw_body = raw_body.decode("utf-8")
        try:
            return json.loads(raw_body)
        except (TypeError, ValueError) as exc:
            raise PaystackError("invalid Paystack webhook JSON") from exc

    def handle_webhook(self, raw_body: Any, signature: Optional[str]) -> Dict[str, Any]:
        """Verify a webhook, update the ledger idempotently, return a summary."""
        event = self.parse_webhook(raw_body, signature)
        data = event.get("data") or {}
        reference = data.get("reference") or ""
        if not reference:
            return {"acknowledged": True, "handled": False, "reason": "no reference"}

        if event.get("event") == "charge.success":
            status = TERMINAL_SUCCESS
        else:
            status = str(data.get("status") or "unknown")

        outcome = self._store.record_webhook(
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
            "acknowledged": True,
            "handled": True,
            "event": event.get("event", ""),
            "reference": reference,
            "status": status,
            "duplicate": outcome["duplicate"],
        }

    # ── helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def generate_reference(prefix: str = "KAI") -> str:
        return f"{prefix}-{secrets.token_hex(8).upper()}"

    @staticmethod
    def to_minor_units(amount: Any) -> int:
        """Convert a major-unit amount (float/str GHS) to integer pesewas."""
        if isinstance(amount, bool):
            raise ValueError("amount must be numeric")
        return int(round(float(amount) * 100))

    def _result(self, record: Optional[dict], *, idempotent: bool = False) -> Dict[str, Any]:
        record = record or {}
        return {
            "provider": "paystack",
            "reference": record.get("reference"),
            "status": record.get("status", "initialized"),
            "amount": record.get("amount"),
            "currency": record.get("currency"),
            "email": record.get("email", ""),
            "channel": record.get("channel", ""),
            "requested_channel": record.get("requested_channel", ""),
            "authorization_url": record.get("authorization_url", ""),
            "access_code": record.get("access_code", ""),
            "mode": record.get("mode", self._mode),
            "created_at": record.get("created_at"),
            "verified_at": record.get("verified_at"),
            "idempotent": idempotent,
        }
