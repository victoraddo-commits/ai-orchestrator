"""Kai Betting — Payment Integration.

Hubtel mobile money payment processing for subscription purchases.
Uses the same pattern as Juris Kai payments.py.
"""

from __future__ import annotations

import os
import json
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from core.kai_betting.db import get_db

logger = logging.getLogger(__name__)


class BettingPaymentClient:
    """Handles subscription payments via Hubtel mobile money.

    Falls back to simulated/test mode when Hubtel credentials are not set —
    this is the standard Kai pattern for all payments.
    """

    def __init__(self):
        self._client_id = os.environ.get("HUBTEL_CLIENT_ID")
        self._client_secret = os.environ.get("HUBTEL_CLIENT_SECRET")
        self._merchant_number = os.environ.get("HUBTEL_MERCHANT_NUMBER")
        self._test_mode = (
            os.environ.get("HUBTEL_TEST_MODE", "true").lower() == "true"
            or not self._is_configured()
        )
        self._base_url = "https://api.hubtel.com/v1"

    def _is_configured(self) -> bool:
        """Check if Hubtel credentials are available."""
        return bool(self._client_id and self._client_secret and self._merchant_number)

    def is_simulated(self) -> bool:
        """Return True if running in test/simulation mode."""
        return self._test_mode

    def request_payment(
        self,
        user_id: int,
        amount: float,
        currency: str = "GHS",
        phone_number: str = "",
        payment_method: str = "mobile_money",
        plan_key: str = "",
    ) -> Dict[str, Any]:
        """Initiate a mobile money payment request.

        Args:
            user_id: Database user ID
            amount: Amount to charge
            currency: Currency code (default GHS)
            phone_number: Mobile money phone number
            payment_method: 'mobile_money', 'card', etc.
            plan_key: Subscription plan key being purchased

        Returns:
            Dict with transaction_id, status, checkout_url (if applicable)
        """
        transaction_id = f"KBT-{uuid.uuid4().hex[:12].upper()}"

        if self._test_mode:
            return self._simulate_payment(
                user_id=user_id,
                amount=amount,
                currency=currency,
                transaction_id=transaction_id,
                payment_method=payment_method,
                plan_key=plan_key,
            )

        # Real Hubtel integration would go here
        # POST /merchantaccount/merchants/{merchant_number}/receive/mobilemoney
        # with customer name, phone, amount, etc.
        return {
            "success": False,
            "error": "Live Hubtel integration pending — use test mode for now",
            "transaction_id": transaction_id,
        }

    def verify_payment(self, transaction_id: str) -> Dict[str, Any]:
        """Verify a payment's status."""
        if self._test_mode:
            return self._simulate_verify(transaction_id)

        # Real Hubtel verification
        return {"status": "unknown", "transaction_id": transaction_id}

    def handle_callback(
        self,
        transaction_id: str,
        status: str,
        provider: str,
        amount: float,
        currency: str = "GHS",
        provider_response: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Process a payment provider's callback/webhook."""
        with get_db() as db:
            payment = db.execute(
                "SELECT * FROM payments WHERE transaction_id = ?",
                (transaction_id,)
            ).fetchone()

            if not payment:
                return {"success": False, "error": "Transaction not found"}

            new_status = self._map_provider_status(status)

            db.execute(
                """UPDATE payments
                   SET status = ?, provider_response = ?, verified_at = datetime('now')
                   WHERE transaction_id = ?""",
                (new_status, json.dumps(provider_response or {}), transaction_id)
            )

            # If payment completed, activate subscription
            if new_status == "completed":
                self._activate_subscription(db, payment["subscription_id"])

            db.commit()

            return {
                "success": True,
                "transaction_id": transaction_id,
                "status": new_status,
            }

    def _simulate_payment(
        self,
        user_id: int,
        amount: float,
        currency: str,
        transaction_id: str,
        payment_method: str,
        plan_key: str,
    ) -> Dict[str, Any]:
        """Simulate a payment in test mode."""
        with get_db() as db:
            # First find/activate the subscription
            plan = db.execute(
                "SELECT * FROM subscription_plans WHERE key = ?", (plan_key,)
            ).fetchone()
            if not plan:
                return {"success": False, "error": f"Plan '{plan_key}' not found"}

            # Create subscription
            sub_cursor = db.execute("""
                INSERT INTO subscriptions (user_id, plan_id, status, started_at, expires_at)
                VALUES (?, ?, 'active', datetime('now'), datetime('now', ?))
            """, (
                user_id,
                plan["id"],
                f"+{plan['duration_days']} days",
            ))
            sub_id = sub_cursor.lastrowid

            # Record payment
            db.execute("""
                INSERT INTO payments (
                    user_id, subscription_id, transaction_id, provider,
                    amount, currency, status, payment_method,
                    verified_at, created_at
                ) VALUES (?, ?, ?, 'hubtel', ?, ?, 'completed', ?, datetime('now'), datetime('now'))
            """, (
                user_id, sub_id, transaction_id, amount, currency, payment_method,
            ))

            # Deactivate previous subscriptions
            db.execute(
                """UPDATE subscriptions SET status = 'expired'
                   WHERE user_id = ? AND id != ? AND status = 'active'""",
                (user_id, sub_id)
            )

            db.commit()

        logger.info(f"[TEST MODE] Payment simulated: {transaction_id} — GHS {amount:.2f} for {plan_key}")
        return {
            "success": True,
            "transaction_id": transaction_id,
            "status": "completed",
            "test_mode": True,
            "subscription_id": sub_id,
        }

    def _simulate_verify(self, transaction_id: str) -> Dict[str, Any]:
        """Simulate payment verification in test mode."""
        with get_db() as db:
            payment = db.execute(
                "SELECT * FROM payments WHERE transaction_id = ?",
                (transaction_id,)
            ).fetchone()
            if not payment:
                return {"status": "not_found", "transaction_id": transaction_id}
            return {"status": payment["status"], "transaction_id": transaction_id}

    def _map_provider_status(self, provider_status: str) -> str:
        """Map provider-specific status strings to our PaymentStatus enum."""
        status_map = {
            "completed": "completed",
            "success": "completed",
            "successful": "completed",
            "pending": "processing",
            "processing": "processing",
            "failed": "failed",
            "cancelled": "failed",
            "refunded": "refunded",
            "reversed": "refunded",
        }
        return status_map.get(provider_status.lower(), "processing")

    def _activate_subscription(self, db, subscription_id: Optional[int]) -> None:
        """Activate a subscription after successful payment."""
        if not subscription_id:
            return

        sub = db.execute(
            "SELECT * FROM subscriptions WHERE id = ?", (subscription_id,)
        ).fetchone()
        if not sub:
            return

        plan = db.execute(
            "SELECT * FROM subscription_plans WHERE id = ?", (sub["plan_id"],)
        ).fetchone()

        db.execute(
            """UPDATE subscriptions
               SET status = 'active', started_at = datetime('now'),
                   expires_at = datetime('now', ?)
               WHERE id = ?""",
            (f"+{plan['duration_days']} days", subscription_id)
        )
