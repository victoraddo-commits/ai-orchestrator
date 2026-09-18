#!/usr/bin/env python3
"""Paystack smoke test — initialize a transaction WITHOUT completing payment.

Refuses to run unless ``PAYSTACK_SMOKE=1``. Prints only non-secret evidence
(mode, key presence, reference, status, checkout URL). Never logs keys.

Examples
--------
    # safe default: requires test keys
    PAYSTACK_SMOKE=1 python scripts/paystack_smoke.py

    # explicit live initialize (NO card is charged — the transaction is only
    # created; nobody completes the checkout)
    PAYSTACK_SMOKE=1 PAYSTACK_MODE=live PAYSTACK_ALLOW_LIVE=true \
        python scripts/paystack_smoke.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.payments import keys as payments_keys
from core.payments.paystack import PaystackError, PaystackProvider


def main() -> int:
    if os.environ.get("PAYSTACK_SMOKE") != "1":
        print("refusing to run: set PAYSTACK_SMOKE=1 (this calls the Paystack API)")
        return 2

    mode = payments_keys.mode()
    provider = PaystackProvider()
    info = {
        "mode": mode,
        "live_allowed": payments_keys.live_allowed(),
        "secret_key_present": payments_keys.has_secret_key(),
        "public_key_present": bool(provider.public_key_value),
        "base_url": provider._base_url,
    }
    print("config:", json.dumps(info))

    reference = PaystackProvider.generate_reference(prefix="KAI-SMOKE")
    email = os.environ.get("PAYSTACK_SMOKE_EMAIL", "smoke@example.com")
    try:
        result = provider.initialize(
            amount=100,  # GHS 1.00 in pesewas — initialize only, never completed
            currency="GHS",
            email=email,
            reference=reference,
            channels=["mobile_money", "card"],
            metadata={"purpose": "kai-paystack-smoke", "mode": mode},
        )
    except (PaystackError, payments_keys.PaymentConfigError, ValueError) as exc:
        print("initialize FAILED:", type(exc).__name__, str(exc))
        return 1

    print("initialize OK:", json.dumps({
        "reference": result.get("reference"),
        "status": result.get("status"),
        "mode": result.get("mode"),
        "authorization_url": result.get("authorization_url"),
    }))
    print("NOTE: transaction initialized only — do NOT complete this checkout.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
