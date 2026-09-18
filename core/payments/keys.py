"""Paystack key + mode resolution for the payments subsystem.

Keys are resolved (never logged, never written to disk):

  1. environment override — ``PAYSTACK_SECRET_KEY`` / ``PAYSTACK_PUBLIC_KEY``
     (convenient for local dev and tests);
  2. otherwise the Kai vault machine plane via
     ``core.ai.kai_vault_client`` using the seeded paths:
       live: secrets/external/paystack_live_secret, .../paystack_live_public
       test: secrets/external/paystack_test_secret, .../paystack_test_public

Mode gating (default SAFE):
  * ``PAYSTACK_MODE`` is ``test`` by default and only ``live`` when set.
  * A live call additionally requires ``PAYSTACK_ALLOW_LIVE=true``. Without
    it, the provider refuses before any HTTP request is made, so live cards
    can never be charged by accident.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

API_BASE = os.environ.get("PAYSTACK_API_BASE", "https://api.paystack.co")

LIVE_SECRET_PATH = "secrets/external/paystack_live_secret"
LIVE_PUBLIC_PATH = "secrets/external/paystack_live_public"
TEST_SECRET_PATH = "secrets/external/paystack_test_secret"
TEST_PUBLIC_PATH = "secrets/external/paystack_test_public"

VALID_MODES = {"test", "live"}
_TEST_ALIASES = {"sandbox", "development", "dev"}
_LIVE_ALIASES = {"production", "prod"}


class PaymentConfigError(RuntimeError):
    """Raised when payment keys/mode are missing or unsafe."""


def mode() -> str:
    raw = (os.environ.get("PAYSTACK_MODE", "test") or "test").strip().lower()
    if raw in _TEST_ALIASES:
        raw = "test"
    elif raw in _LIVE_ALIASES:
        raw = "live"
    if raw not in VALID_MODES:
        raise PaymentConfigError(
            f"Invalid PAYSTACK_MODE={raw!r} (expected 'test' or 'live')"
        )
    return raw


def live_allowed() -> bool:
    """True only when live mode is selected AND explicitly confirmed."""
    if mode() != "live":
        return False
    return (os.environ.get("PAYSTACK_ALLOW_LIVE", "false") or "false").strip().lower() == "true"


def _env_key(name: str) -> Optional[str]:
    value = os.environ.get(name, "")
    value = value.strip() if value else ""
    return value or None


def _vault_key(path: str) -> Optional[str]:
    try:
        from core.ai.kai_vault_client import fetch_secret, load_token
    except Exception as exc:  # vault client optional / import failure
        logger.warning("paystack: vault client unavailable (%s)", type(exc).__name__)
        return None
    token = load_token()
    if not token:
        return None
    return fetch_secret(path, token)


def secret_key() -> str:
    """Return the Paystack secret key for the active mode (never logged)."""
    env = _env_key("PAYSTACK_SECRET_KEY")
    if env:
        return env
    if mode() == "live" and not live_allowed():
        raise PaymentConfigError(
            "live mode requires PAYSTACK_ALLOW_LIVE=true before live keys are used"
        )
    path = LIVE_SECRET_PATH if mode() == "live" else TEST_SECRET_PATH
    value = _vault_key(path)
    if not value:
        raise PaymentConfigError(
            f"No Paystack secret key for mode={mode()} "
            f"(set PAYSTACK_SECRET_KEY or seed vault {path})"
        )
    return value


def public_key() -> str:
    """Return the Paystack public key for the active mode (never logged)."""
    env = _env_key("PAYSTACK_PUBLIC_KEY")
    if env:
        return env
    path = LIVE_PUBLIC_PATH if mode() == "live" else TEST_PUBLIC_PATH
    value = _vault_key(path)
    if not value:
        raise PaymentConfigError(
            f"No Paystack public key for mode={mode()} "
            f"(set PAYSTACK_PUBLIC_KEY or seed vault {path})"
        )
    return value


def has_secret_key() -> bool:
    try:
        return bool(secret_key())
    except PaymentConfigError:
        return False


def describe() -> dict:
    """Non-secret status summary safe to return from an API/diagnostic."""
    current = mode()
    return {
        "mode": current,
        "live_allowed": live_allowed(),
        "secret_key_present": has_secret_key(),
    }
