"""Telegram Manager — Kai Command Center module for Telegram bot management.

Provides:
  - Read/write access to the Telegram MCP server's access.json
  - Activity logging database (SQLite)
  - REST API for the Command Center dashboard panel
  - No dependency on the MCP server process — reads/writes its config file directly
"""

from core.telegram_manager.access import (
    read_access_config,
    write_access_config,
    add_user,
    remove_user,
    set_policy,
    set_ack_reaction,
    set_reply_mode,
    add_group,
    remove_group,
)

from core.telegram_manager.db import (
    log_message,
    upsert_user_profile,
    get_summary,
    get_users,
    get_user_activity,
    get_recent_activity,
    get_daily_stats,
    clear_old_logs,
)

from core.telegram_manager.api import telegram_router

__all__ = [
    "read_access_config",
    "write_access_config",
    "add_user",
    "remove_user",
    "set_policy",
    "set_ack_reaction",
    "set_reply_mode",
    "add_group",
    "remove_group",
    "log_message",
    "upsert_user_profile",
    "get_summary",
    "get_users",
    "get_user_activity",
    "get_recent_activity",
    "get_daily_stats",
    "clear_old_logs",
    "telegram_router",
]
