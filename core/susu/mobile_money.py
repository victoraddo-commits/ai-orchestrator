"""SUSU Mobile Money Integration.

Connects to mobile money providers (MTN MoMo, Telecel Cash, AirtelTigo Money)
via Hubtel's aggregated payment gateway. Provides a clean adapter interface
so the SUSU bot can collect contributions, process payouts, and track fees.

Pattern: follows the same Hubtel integration model as juris_kai/payments.py
with the same test-mode simulation for sandbox validation.
"""

import os
import uuid
import hashlib
import logging
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone

logger = logging.getLogger("susu.mobile_money")


HUBTEL_CLIENT_ID = os.environ.get("HUBTEL_CLIENT_ID", "")
HUBTEL_CLIENT_SECRET = os.environ.get("HUBTEL_CLIENT_SECRET", "")
HUBTEL_MERCHANT_NUMBER = os.environ.get("HUBTEL_MERCHANT_NUMBER", "")
HUBTEL_API_BASE = os.environ.get(
    "HUBTEL_API_BASE", "https://api.hubtel.com/v1"
)
HUBTEL_CALLBACK_URL = os.environ.get("HUBTEL_CALLBACK_URL", "")
HUBTEL_TEST_MODE = os.environ.get("HUBTEL_TEST_MODE", "true").lower() == "true"

SUPPORTED_PROVIDERS = ["mtn", "vodafone", "airteltigo"]
VALID_CHANNELS = ["mobile_money"]


def _now():
    return datetime.utcnow().isoformat() + "Z"


class MobileMoneyProvider(ABC):
    """Abstract base for mobile money provider adapters."""

    @abstractmethod
    def provider_name(self) -> str:
        pass

    @abstractmethod
    def hubtel_provider_code(self) -> str:
        pass

    @abstractmethod
    def supported_networks(self) -> List[str]:
        pass


class MTNMoMo(MobileMoneyProvider):
    def provider_name(self) -> str:
        return "MTN Mobile Money"

    def hubtel_provider_code(self) -> str:
        return "mtn"

    def supported_networks(self) -> List[str]:
        return ["MTN"]


class TelecelCash(MobileMoneyProvider):
    def provider_name(self) -> str:
        return "Telecel Cash"

    def hubtel_provider_code(self) -> str:
        return "vodafone"

    def supported_networks(self) -> List[str]:
        return ["Telecel", "Vodafone"]


class AirtelTigoMoney(MobileMoneyProvider):
    def provider_name(self) -> str:
        return "AirtelTigo Money"

    def hubtel_provider_code(self) -> str:
        return "airteltigo"

    def supported_networks(self) -> List[str]:
        return ["AirtelTigo"]


PROVIDER_REGISTRY: Dict[str, MobileMoneyProvider] = {
    "mtn": MTNMoMo(),
    "vodafone": TelecelCash(),
    "telecel": TelecelCash(),
    "airteltigo": AirtelTigoMoney(),
}


def get_provider(provider_code: str) -> Optional[MobileMoneyProvider]:
    return PROVIDER_REGISTRY.get(provider_code.lower())


class MobileMoneyClient:
    """Client for initiating and tracking mobile money payments via Hubtel."""

    def __init__(self):
        self.client_id = HUBTEL_CLIENT_ID
        self.client_secret = HUBTEL_CLIENT_SECRET
        self.merchant_number = HUBTEL_MERCHANT_NUMBER
        self.base_url = HUBTEL_API_BASE.rstrip("/")
        self.test_mode = HUBTEL_TEST_MODE

    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.merchant_number)

    def _basic_auth_token(self) -> str:
        import base64
        credentials = f"{self.client_id}:{self.client_secret}"
        return base64.b64encode(credentials.encode()).decode()

    def _simulate_payment(
        self, amount: float, phone: str, provider: str, description: str,
        payment_id: str, payer_name: str = "",
    ) -> Dict[str, Any]:
        tx_id = f"SUSU-TEST-{uuid.uuid4().hex[:12].upper()}"
        logger.info(
            f"[TEST MODE] Simulated {provider} payment: {amount} from {phone} "
            f"({payer_name}) for '{description}' — TX: {tx_id}"
        )
        return {
            "success": True,
            "payment_id": payment_id,
            "transaction_id": tx_id,
            "provider_transaction_id": tx_id,
            "status": "completed",
            "test_mode": True,
            "amount": amount,
            "provider": provider,
            "customer_phone": phone,
            "customer_name": payer_name,
        }

    def request_payment(
        self,
        amount: float,
        phone: str,
        provider: str,
        description: str,
        payment_id: str,
        payer_name: str = "",
        channel: str = "mobile_money",
    ) -> Dict[str, Any]:
        """Initiate a mobile money payment request via Hubtel.

        Args:
            amount: Payment amount
            phone: Customer's mobile number
            provider: Provider code ('mtn', 'vodafone', 'airteltigo')
            description: Payment description
            payment_id: Unique payment reference (SUSU internal)
            payer_name: Customer's name for the payment receipt
            channel: Payment channel (default: mobile_money)

        Returns:
            Dict with success, transaction_id, status, and provider metadata
        """
        provider_obj = get_provider(provider)
        if provider_obj is None:
            return {
                "success": False,
                "payment_id": payment_id,
                "error": f"Unsupported provider: {provider}. Supported: {sorted(PROVIDER_REGISTRY.keys())}",
            }

        if self.test_mode:
            return self._simulate_payment(amount, phone, provider, description, payment_id, payer_name)

        if not self.is_configured():
            return {
                "success": False,
                "payment_id": payment_id,
                "error": "Hubtel not configured. Set HUBTEL_CLIENT_ID, HUBTEL_CLIENT_SECRET, HUBTEL_MERCHANT_NUMBER.",
            }

        try:
            import requests

            payload = {
                "amount": amount,
                "title": "SUSU Group Savings",
                "description": description,
                "clientReference": payment_id,
                "merchantNumber": self.merchant_number,
                "callbackUrl": HUBTEL_CALLBACK_URL,
                "channel": channel,
                "customer": {
                    "name": payer_name or "SUSU Member",
                    "phoneNumber": phone,
                },
            }

            if channel == "mobile_money":
                payload["monmoProvider"] = provider_obj.hubtel_provider_code()

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
                hubtel_tx_id = body.get("transactionId", "")
                return {
                    "success": True,
                    "payment_id": payment_id,
                    "transaction_id": hubtel_tx_id,
                    "provider_transaction_id": hubtel_tx_id,
                    "status": body.get("status", "pending"),
                    "checkout_url": body.get("checkoutUrl", ""),
                    "amount": amount,
                    "provider": provider,
                    "customer_phone": phone,
                    "customer_name": payer_name,
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

    def check_payment_status(self, transaction_id: str) -> Dict[str, Any]:
        """Check the status of a Hubtel payment by transaction ID."""
        if not self.is_configured() or self.test_mode:
            return {
                "success": True,
                "transaction_id": transaction_id,
                "status": "completed" if self.test_mode else "unknown",
                "test_mode": self.test_mode,
            }

        try:
            import requests
            resp = requests.get(
                f"{self.base_url}/merchantaccount/merchants/{self.merchant_number}/transactions/{transaction_id}/status",
                headers={"Authorization": f"Basic {self._basic_auth_token()}"},
                timeout=15,
            )
            if resp.status_code == 200:
                body = resp.json()
                return {
                    "success": True,
                    "transaction_id": transaction_id,
                    "status": body.get("status", "unknown"),
                }
            return {
                "success": False,
                "transaction_id": transaction_id,
                "error": f"Status check returned {resp.status_code}",
            }
        except Exception as e:
            return {"success": False, "transaction_id": transaction_id, "error": str(e)}

    def calculate_processor_fee(self, amount: float, provider: str) -> Dict[str, Any]:
        """Calculate the payment processor fee for a given amount and provider.

        Processor fee structure (typical Hubtel rates):
          - MTN MoMo: 1.0%
          - Telecel Cash: 1.0%
          - AirtelTigo Money: 1.0%

        Returns fee data consumable by the Fee Engine (SUSU-3).
        """
        provider_obj = get_provider(provider)
        if provider_obj is None:
            return {"success": False, "error": f"Unknown provider: {provider}"}

        fee_percentage = 1.0
        fee_amount = round(amount * fee_percentage / 100.0, 2)

        return {
            "success": True,
            "provider": provider,
            "provider_name": provider_obj.provider_name(),
            "amount": amount,
            "fee_percentage": fee_percentage,
            "fee_amount": fee_amount,
            "net_amount": round(amount - fee_amount, 2),
        }


_client: Optional[MobileMoneyClient] = None


def get_client() -> MobileMoneyClient:
    global _client
    if _client is None:
        _client = MobileMoneyClient()
    return _client
