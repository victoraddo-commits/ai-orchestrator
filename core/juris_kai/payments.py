"""Juris Kai Hubtel Payment Integration.

Handles Mobile Money payments via Hubtel's API for subscription purchases
and per-document billing. Supports both direct Mobile Money and card payments.

Security: NO imports from core.build_manager, core.approval, or
core.deployment_manager.
"""

import os
import uuid
import hashlib
import logging
from typing import Optional, Dict, Any
from datetime import datetime, timezone

logger = logging.getLogger("juris_kai.payments")

# Hubtel configuration
HUBTEL_API_BASE = os.environ.get(
    "HUBTEL_API_BASE", "https://api.hubtel.com/v1"
)
HUBTEL_CALLBACK_URL = os.environ.get(
    "HUBTEL_CALLBACK_URL", ""
)

# Whether to use Hubtel's test environment
HUBTEL_TEST_MODE = os.environ.get("HUBTEL_TEST_MODE", "true").lower() == "true"


class HubtelPaymentClient:
    """Client for Hubtel's Receive Money API."""

    def __init__(self):
        from core.ai.credential_vault import retrieve_hubtel_credentials
        creds = retrieve_hubtel_credentials()
        self.client_id = creds["client_id"] or os.environ.get("HUBTEL_CLIENT_ID", "")
        self.client_secret = creds["client_secret"] or os.environ.get("HUBTEL_CLIENT_SECRET", "")
        self.merchant_number = creds["merchant_number"] or os.environ.get("HUBTEL_MERCHANT_NUMBER", "")
        self.base_url = HUBTEL_API_BASE.rstrip("/")
        self.test_mode = HUBTEL_TEST_MODE

    def is_configured(self) -> bool:
        """Check if Hubtel credentials are configured."""
        return bool(self.client_id and self.client_secret and self.merchant_number)

    def _basic_auth_token(self) -> str:
        """Generate Basic auth token."""
        import base64
        credentials = f"{self.client_id}:{self.client_secret}"
        return base64.b64encode(credentials.encode()).decode()

    def request_payment(
        self,
        amount_ghs: float,
        customer_name: str,
        customer_phone: str,
        description: str,
        payment_id: str,
        channel: str = "mobile_money",
        mobile_money_provider: str = "mtn",
    ) -> Dict[str, Any]:
        """Initiate a Mobile Money payment request via Hubtel.

        Args:
            amount_ghs: Amount in Ghana Cedis
            customer_name: Customer's full name
            customer_phone: Customer's mobile number (for MoMo)
            description: Payment description
            payment_id: Unique payment reference
            channel: 'mobile_money' or 'card'
            mobile_money_provider: 'mtn', 'vodafone', 'airteltigo'

        Returns:
            Dict with payment status, checkout URL, or error
        """
        # In test/development mode, simulate payment without real credentials
        if self.test_mode:
            return self._simulate_payment(amount_ghs, customer_name, customer_phone, description, payment_id)

        if not self.is_configured():
            return {
                "success": False,
                "error": "Hubtel not configured",
                "payment_id": payment_id,
            }

        try:
            import requests

            payload = {
                "amount": amount_ghs,
                "title": "Juris Kai",
                "description": description,
                "clientReference": payment_id,
                "merchantNumber": self.merchant_number,
                "callbackUrl": HUBTEL_CALLBACK_URL,
                "channel": channel,
                "customer": {
                    "name": customer_name,
                    "phoneNumber": customer_phone,
                },
            }

            if channel == "mobile_money":
                payload["monmoProvider"] = mobile_money_provider

            resp = requests.post(
                f"{self.base_url}/merchantaccount/merchants/{self.merchant_number}/receive/mobilemoney",
                json=payload,
                headers={
                    "Authorization": f"Basic {self._basic_auth_token()}",
                    "Content-Type": "application/json",
                },
                timeout=30,
            )

            if resp.status_code in (200, 201):
                body = resp.json()
                return {
                    "success": True,
                    "payment_id": payment_id,
                    "hubtel_transaction_id": body.get("transactionId", ""),
                    "status": body.get("status", "pending"),
                    "checkout_url": body.get("checkoutUrl", ""),
                    "amount_ghs": amount_ghs,
                    "raw_response": body,
                }
            else:
                logger.error(f"Hubtel API error: {resp.status_code} {resp.text}")
                return {
                    "success": False,
                    "payment_id": payment_id,
                    "error": f"Hubtel API returned {resp.status_code}",
                    "detail": resp.text[:500],
                }

        except ImportError:
            logger.warning("requests library not available for Hubtel API calls")
            return {"success": False, "payment_id": payment_id, "error": "requests library unavailable"}
        except Exception as e:
            logger.error(f"Hubtel payment request failed: {e}")
            return {"success": False, "payment_id": payment_id, "error": str(e)}

    def check_payment_status(self, hubtel_transaction_id: str) -> Dict[str, Any]:
        """Check the status of a Hubtel payment."""
        if not self.is_configured() or self.test_mode:
            return {
                "success": True,
                "hubtel_transaction_id": hubtel_transaction_id,
                "status": "completed",
                "test_mode": True,
            }

        try:
            import requests
            resp = requests.get(
                f"{self.base_url}/merchantaccount/merchants/{self.merchant_number}/transactions/{hubtel_transaction_id}/status",
                headers={"Authorization": f"Basic {self._basic_auth_token()}"},
                timeout=15,
            )
            if resp.status_code == 200:
                body = resp.json()
                return {
                    "success": True,
                    "hubtel_transaction_id": hubtel_transaction_id,
                    "status": body.get("status", "unknown"),
                }
            return {
                "success": False,
                "hubtel_transaction_id": hubtel_transaction_id,
                "error": f"Status check returned {resp.status_code}",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _simulate_payment(
        self, amount_ghs: float, customer_name: str, customer_phone: str,
        description: str, payment_id: str,
    ) -> Dict[str, Any]:
        """Simulate a successful payment in test mode."""
        tx_id = f"TEST-{uuid.uuid4().hex[:16].upper()}"
        logger.info(
            f"[TEST MODE] Simulated payment: GHS {amount_ghs} from {customer_name} "
            f"({customer_phone}) for '{description}' — TX: {tx_id}"
        )
        return {
            "success": True,
            "payment_id": payment_id,
            "hubtel_transaction_id": tx_id,
            "status": "completed",
            "test_mode": True,
            "amount_ghs": amount_ghs,
            "customer_name": customer_name,
            "customer_phone": customer_phone,
        }


# Module-level singleton
_payment_client: Optional[HubtelPaymentClient] = None


def get_payment_client() -> HubtelPaymentClient:
    """Get or create the Hubtel payment client singleton."""
    global _payment_client
    if _payment_client is None:
        _payment_client = HubtelPaymentClient()
    return _payment_client
