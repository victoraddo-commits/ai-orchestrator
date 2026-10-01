"""core.money_telegram.token — akush233-bot token materialization (vault).

The registry's env_file mechanism (core.telegram.transport.token_for) reads
the process env first, then a dotenv file. The bot token itself lives ONLY
in vault (``secrets/money/telegram_bot_token``); this bootstrap reveals it
and writes the 0600 env file — the same pattern the other bots use, with
vault as the source of truth. Values are never logged, never returned.
"""
from __future__ import annotations

import logging
import os
import stat

from core.telegram import registry as reg
from core.telegram.transport import token_for as transport_token_for, _dotenv_token

logger = logging.getLogger("kai.money_telegram")

BOT_ID = "akush233-bot"
ENV_FILE = os.environ.get("AKUSH_BOT_ENV_FILE", "/etc/kai/akush_bot.env")
ENV_KEY = "TELEGRAM_BOT_TOKEN"
VAULT_PATH = "secrets/money/telegram_bot_token"


def _vault_fetch(path: str) -> str | None:
    from core.ai import kai_vault_client as _vault
    token = _vault.load_token()
    if not token:
        return None
    return _vault.fetch_secret(path, token)


def env_file_token() -> str:
    return (_dotenv_token(ENV_FILE, ENV_KEY) or "").strip()


def ensure_env_file(*, force: bool = False, fetch=None) -> bool:
    """Write the 0600 env file from vault if not already populated.

    Returns True when a token is available in the env file afterwards.
    Never prints or logs the value.
    """
    if not force and env_file_token():
        return True
    fetch = fetch or _vault_fetch
    try:
        value = (fetch(VAULT_PATH) or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("akush bot token vault fetch failed: %s", type(exc).__name__)
        return False
    if not value:
        return False
    try:
        fd = os.open(ENV_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, f"{ENV_KEY}={value}\n".encode())
        finally:
            os.close(fd)
        os.chmod(ENV_FILE, stat.S_IRUSR | stat.S_IWUSR)
        logger.info("akush bot env file materialized from vault (%s)", ENV_FILE)
        return True
    except OSError as exc:
        logger.warning("akush bot env file write failed: %s", type(exc).__name__)
        return False


def token_available(*, allow_vault: bool = True) -> bool:
    bot = reg.get(BOT_ID)
    if transport_token_for(bot):
        return True
    if allow_vault and ensure_env_file():
        return bool(transport_token_for(bot))
    return False


def runtime_ready() -> bool:
    """The one runtime gate behind which everything activates.

    True only when BOTH the registry flag is enabled (the operator's one
    flag flip) AND a token is available (env → env file → vault). Before
    the token exists this is False everywhere: notify holds, poller idles,
    nothing sends, nothing crashes.
    """
    bot = reg.get(BOT_ID)
    if bot is None or not reg.is_enabled(bot):
        return False
    return token_available()
