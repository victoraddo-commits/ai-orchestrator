"""Provider adapter interface.

An adapter knows ONE provider (Home Assistant, Tuya, MQTT, a camera vendor…).
It only translates provider state/commands; registry, authorization, audit and
World-Model sync live above it.
"""
from __future__ import annotations


class AdapterError(Exception):
    """Provider-level failure (mapped to HTTP 502/400 by the route layer)."""


class ProviderAdapter:
    name: str = "base"

    def identify(self) -> list[dict]:
        """Return provider devices as dicts with at least ``provider_id``."""
        raise NotImplementedError

    def get_state(self, provider_id: str) -> dict:
        """Return the current state; never invent fields."""
        raise NotImplementedError

    def set_state(self, provider_id: str, changes: dict) -> dict:
        """Apply changes and return the observed state (read-back)."""
        raise NotImplementedError

    def health(self) -> dict:
        """Report provider health as an honest dict.

        Returns at minimum ``{"ok": bool}``; adapters may add ``detail`` and a
        ``notice`` for operator-facing remediation (e.g. an expired session).
        The default is "unknown" — never claim health an adapter cannot prove.
        """
        return {"ok": None, "detail": "no health probe for this provider"}
