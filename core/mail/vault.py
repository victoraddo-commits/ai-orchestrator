"""core.mail vault bridge.

Resolves mailbox credentials from the kai-vault machine plane **into memory
only**. Values are never persisted, logged, or placed in an event payload — the
caller receives :class:`MailCredentials` transiently and drops it after the
transport is constructed.

The vault path for Proton lives under ``secrets/external/*`` (rotate-flagged),
read with the machine bearer token from CT111 (default
``/root/.credentials/ai-orchestrator-vault-token``). ``fetch`` is injectable so
tests never touch the network.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Optional

from core.mail.schema import MailCredentials


def _creds_from_mapping(data: dict) -> MailCredentials:
    user = data.get("user") or data.get("username") or data.get("email") or data.get("login")
    password = data.get("password") or data.get("pass") or data.get("secret")
    return MailCredentials(user=str(user or ""), password=str(password or ""))


def _parse(value: Any) -> MailCredentials:
    # The machine plane may return a structured value (dict) directly.
    if isinstance(value, dict):
        return _creds_from_mapping(value)
    text = (value or "").strip()
    if text.startswith("{"):
        try:
            return _creds_from_mapping(json.loads(text))
        except (ValueError, TypeError):
            pass
    if ":" in text:
        user, _, password = text.partition(":")
        return MailCredentials(user=user.strip(), password=password.strip())
    # bare secret: treat as the password for the configured mailbox address
    return MailCredentials(user="", password=text)


def _default_fetch(path: str, token: Optional[str]) -> Any:
    from core.ai.kai_vault_client import fetch_secret, load_token

    token = token or load_token()
    if not token:
        return None
    return fetch_secret(path, token)


def load_transport_credentials(
    vault_path: str,
    *,
    fetch: Optional[Callable[[str], Any]] = None,
    token: Optional[str] = None,
) -> Optional[MailCredentials]:
    """Reveal + parse one vault secret. Returns None on any failure.

    The secret value is never logged. Callers must not persist the result.
    """
    if fetch is None:
        fetch = lambda path: _default_fetch(path, token)  # noqa: E731
    value = fetch(vault_path)
    if not value:
        return None
    return _parse(value)


__all__ = ["load_transport_credentials"]
