"""Kai payments subsystem — reusable Paystack provider + ledger + routes.

    from core.payments.paystack import PaystackProvider
    provider = PaystackProvider()
    checkout = provider.initialize(amount=1000, email="a@b.com", reference="r1")

Provider selection for module flows is controlled with ``PAYMENT_PROVIDER``
(default ``hubtel`` — legacy behaviour preserved).
"""

from core.payments.keys import PaymentConfigError
from core.payments.paystack import PaystackError, PaystackProvider

__all__ = ["PaystackProvider", "PaystackError", "PaymentConfigError"]
