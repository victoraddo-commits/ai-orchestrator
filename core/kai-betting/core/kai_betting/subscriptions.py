"""Kai Betting — Subscription Management.

Handles plan management, subscription lifecycle, and access control.
Integrates with the payment client for purchase flows.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from core.kai_betting.db import get_db
from core.kai_betting.payments import BettingPaymentClient

logger = logging.getLogger(__name__)


class SubscriptionManager:
    """Manages user subscriptions and plan access."""

    def __init__(self, payment_client: Optional[BettingPaymentClient] = None):
        self._payment = payment_client or BettingPaymentClient()

    def purchase(
        self,
        user_id: int,
        plan_key: str,
        payment_provider: str = "hubtel",
        payment_method: str = "mobile_money",
        phone_number: str = "",
        currency: str = "GHS",
    ) -> Dict[str, Any]:
        """Initiate a subscription purchase.

        Args:
            user_id: The user purchasing
            plan_key: Plan key ('daily', 'weekly', 'monthly', etc.)
            payment_provider: Payment gateway
            payment_method: 'mobile_money', 'card', etc.
            phone_number: Mobile money phone number
            currency: Currency code

        Returns:
            Dict with transaction_id and status
        """
        with get_db() as db:
            # Validate user
            user = db.execute(
                "SELECT * FROM users WHERE id = ? AND is_active = 1",
                (user_id,)
            ).fetchone()
            if not user:
                return {"success": False, "error": "User not found or inactive"}

            # Validate plan
            plan = db.execute(
                "SELECT * FROM subscription_plans WHERE key = ? AND is_active = 1",
                (plan_key,)
            ).fetchone()
            if not plan:
                return {"success": False, "error": f"Plan '{plan_key}' not found or inactive"}

            # Check for existing active subscription
            existing = db.execute(
                "SELECT * FROM subscriptions WHERE user_id = ? AND status = 'active'",
                (user_id,)
            ).fetchone()
            if existing:
                return {
                    "success": False,
                    "error": "You already have an active subscription. Let it expire first.",
                    "expires_at": existing["expires_at"],
                }

            # Process payment
            payment_result = self._payment.request_payment(
                user_id=user_id,
                amount=plan["price"],
                currency=plan["currency"],
                phone_number=phone_number,
                payment_method=payment_method,
                plan_key=plan_key,
            )

            return payment_result

    def check_access(self, user_id: int) -> Dict[str, Any]:
        """Check if a user has an active subscription and their limits.

        Returns:
            Dict with has_access, plan_name, expires_at, limits
        """
        with get_db() as db:
            sub = db.execute("""
                SELECT s.*, sp.name as plan_name, sp.features, sp.key as plan_key
                FROM subscriptions s
                JOIN subscription_plans sp ON sp.id = s.plan_id
                WHERE s.user_id = ? AND s.status = 'active'
                ORDER BY s.created_at DESC LIMIT 1
            """, (user_id,)).fetchone()

            if not sub:
                return {
                    "has_access": False,
                    "plan_name": "Free",
                    "expires_at": None,
                    "limits": {"max_picks": 3},  # Free tier
                }

            features = json.loads(sub["features"]) if sub["features"] else {}

            return {
                "has_access": True,
                "plan_name": sub["plan_name"],
                "plan_key": sub["plan_key"],
                "expires_at": sub["expires_at"],
                "started_at": sub["started_at"],
                "auto_renew": bool(sub["auto_renew"]),
                "limits": {
                    "max_picks": features.get("max_picks", 20),
                    "premium_sports": features.get("premium_sports", []),
                },
            }

    def expire_check(self) -> int:
        """Check for and expire lapsed subscriptions. Returns count expired."""
        with get_db() as db:
            cursor = db.execute("""
                UPDATE subscriptions
                SET status = 'expired', updated_at = datetime('now')
                WHERE status = 'active' AND expires_at <= datetime('now')
            """)
            db.commit()
            count = cursor.rowcount
            if count > 0:
                logger.info(f"Expired {count} subscription(s)")
            return count

    def get_plan(self, plan_key: str) -> Optional[Dict[str, Any]]:
        """Get a subscription plan by key."""
        with get_db() as db:
            plan = db.execute(
                "SELECT * FROM subscription_plans WHERE key = ? AND is_active = 1",
                (plan_key,)
            ).fetchone()
            if not plan:
                return None
            data = dict(plan)
            data["features"] = json.loads(data["features"]) if data["features"] else {}
            return data

    def list_active_subscribers(self) -> List[Dict[str, Any]]:
        """List all users with active subscriptions."""
        with get_db() as db:
            rows = db.execute("""
                SELECT u.id, u.email, u.full_name, u.phone_number,
                       s.status as sub_status, s.expires_at,
                       sp.name as plan_name, sp.key as plan_key
                FROM subscriptions s
                JOIN users u ON u.id = s.user_id
                JOIN subscription_plans sp ON sp.id = s.plan_id
                WHERE s.status = 'active'
                ORDER BY s.expires_at ASC
            """).fetchall()
            return [dict(r) for r in rows]

    def count_active_subscribers(self) -> int:
        """Count active subscriptions."""
        with get_db() as db:
            row = db.execute(
                "SELECT COUNT(*) as cnt FROM subscriptions WHERE status = 'active'"
            ).fetchone()
            return row["cnt"] if row else 0
