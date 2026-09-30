"""Persistence-boundary secret guard.

Registries must never store credentials — only vault *references*. Schemas use
``extra="forbid"`` to reject credential fields at the model boundary; this
guard is the second line of defence at the storage boundary, catching raw
dicts that bypass a model.

A boolean is a *flag*, not secret material: keys such as ``otp_present`` or
``token_expired`` describe state and never carry a value, so they are exempt.
"""

from __future__ import annotations

from typing import Any

SECRET_KEY_MARKERS = (
    "password",
    "passwd",
    "passphrase",
    "secret",
    "token",
    "api_key",
    "apikey",
    "private_key",
    "credential",
    "otp",
    "totp_seed",
    "seed_phrase",
    "mnemonic",
)


class SecretFieldError(ValueError):
    """Raised when a payload contains a field that must never be persisted."""


def find_secret_fields(payload: Any, path: str = "") -> list[str]:
    """Return dotted paths of any secret-looking keys in ``payload``."""
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_lower = str(key).lower()
            here = f"{path}.{key}" if path else str(key)
            # Boolean flags (e.g. otp_present) carry no secret value.
            if any(marker in key_lower for marker in SECRET_KEY_MARKERS) and not isinstance(value, bool):
                found.append(here)
            found.extend(find_secret_fields(value, here))
    elif isinstance(payload, (list, tuple)):
        for index, value in enumerate(payload):
            found.extend(find_secret_fields(value, f"{path}[{index}]"))
    return found


def assert_no_secret_fields(payload: Any) -> None:
    """Raise :class:`SecretFieldError` if ``payload`` holds secret-looking keys."""
    found = find_secret_fields(payload)
    if found:
        raise SecretFieldError(
            "refusing to persist secret-looking fields: " + ", ".join(found)
        )
