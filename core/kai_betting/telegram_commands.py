"""KAI Bet — Telegram command routing for the shared Kai bot (§30).

Re-homed from the separate `betsportz_bot` (which had no valid token and whose
host, CT103, has no internet) onto the operator's working Kai bot. The bridge
calls `handle_betting_command()` alongside the money/enhancement routers.
"""
from __future__ import annotations

from typing import Optional

BETTING_COMMANDS = (
    "/start", "/help", "/day", "/picks", "/odds", "/results",
    "/performance", "/sports", "/subscribe", "/myaccount", "/stats", "/predictions",
)


def is_betting_command(text: str) -> bool:
    if not text:
        return False
    first = text.strip().split()[0].lower()
    first = first.split("@", 1)[0]  # strip @BotName suffix
    return first in BETTING_COMMANDS


def handle_betting_command(text: str, chat_id: str = "", user_id: str = "") -> Optional[str]:
    """Return a reply for a betting command, or None if it isn't one. Never raises."""
    if not is_betting_command(text):
        return None
    try:
        from core.kai_betting.telegram_bot import BettingTelegramBot
        bot = BettingTelegramBot()
        return bot.handle_message(str(chat_id), text, str(user_id))
    except Exception as e:  # noqa: BLE001
        return f"Betting command error: {type(e).__name__}: {e}"
