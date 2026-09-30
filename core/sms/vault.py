"""core.sms vault bridge.

Resolves the SMS webhook shared token (and any line credential) from the
kai-vault machine plane **into memory only**. Values are never persisted,
logged, or placed in an event payload.

Default paths:
  * webhook token: ``secrets/sms/webhook_token``
  * a line secret: ``secrets/sms/lines/<number>``

The machine bearer token is read from CT111 (default
``/root/.credentials/ai-orchestrator-vault-token``). ``fetch`` is injectable so
tests never touch the network.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Optional

WEBHOOK_TOKEN_PATH = "secrets/sms/webhook_token"


def _default_fetch(path: str, token: Optional[str]) -> Any:
    from core.ai.kai_vault_client import fetch_secret, load_token

    token = token or load_token()
    if not token:
        return None
    return fetch_secret(path, token)


def load_secret(path: str, *, fetch: Optional[Callable[[str], Any]] = None,
                token: Optional[str] = None) -> Optional[str]:
    """Reveal one vault secret. None on any failure. The value is never logged."""
    if fetch is None:
        fetch = lambda p: _default_fetch(p, token)  # noqa: E731
    value = fetch(path)
    if value is None:
        return None
    return str(value).strip() or None


def load_webhook_token(*, fetch: Optional[Callable[[str], Any]] = None,
                       token: Optional[str] = None) -> Optional[str]:
    """Resolve the webhook shared token: env override wins, then the vault."""
    env = os.environ.get("KAI_SMS_WEBHOOK_TOKEN", "").strip()
    if env:
        return env
    return load_secret(WEBHOOK_TOKEN_PATH, fetch=fetch, token=token)


__all__ = ["WEBHOOK_TOKEN_PATH", "load_secret", "load_webhook_token"]
