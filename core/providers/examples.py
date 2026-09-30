"""Shipped stub/example providers.

Only a ``generic`` placeholder adapter ships in STEP 2 — enough to demonstrate
the :class:`~core.providers.adapter.ProviderAdapter` interface and the policy
gate without integrating any real provider. It is intentionally partial: only
``discovery`` and ``account_status`` are implemented; the remaining operations
inherit the interface defaults and raise ``NotApplicableError``.

Flows that are deliberately *not* built yet: browser automation, email/SMS
verification, and any real provider (Amazon is STEP 7).
"""

from __future__ import annotations

from core.providers import store
from core.providers.adapter import NOT_APPLICABLE, ProviderAdapter
from core.providers.schema import ProviderDescriptor


class GenericAdapter(ProviderAdapter):
    """Minimal, partially-implemented example adapter for the stub provider."""

    def __init__(self, descriptor: ProviderDescriptor | None = None):
        self._descriptor = descriptor or store.get_base("generic")
        if self._descriptor is None:
            raise store.ProviderNotFound("generic")

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._descriptor

    def discovery(self, **kwargs):
        return {"provider_id": self.descriptor.provider_id, "status": "stub"}

    def account_status(self, **kwargs):
        # Explicitly not applicable rather than raising.
        return NOT_APPLICABLE


def register_examples() -> None:
    """Register the shipped stub adapter (idempotent per reset cycle)."""
    store.register_adapter(GenericAdapter())


__all__ = ["GenericAdapter", "register_examples"]
