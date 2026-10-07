"""KAI Telegram Module — shared transport/interface fabric (directive §1).

Complements, and does not duplicate, the existing Kai pieces:
  * ``core/telegram_bridge``  — low-level Telegram Bot API transport
  * ``core/telegram_poller``  — dedicated inbound long-poll loop
  * ``core/telegram_manager`` — Command Center admin/access panel

This package adds the two things the audit proved were missing:
  * ``registry`` — deterministic bot → owner → capability mapping (§20-§22)
  * ``guard``    — deny-by-default capability authorization + audit (§17-§19)

Invariant (directive §1.1): ONE Telegram Module, MANY bots, and EACH bot has
ONE explicit functional owner. Transport is shared; business logic stays in
the owning module.
"""
from core.telegram.registry import (
    Bot,
    all_bots,
    authorize,
    by_token_env,
    by_username,
    get,
    has_capability,
    is_blocked,
    is_enabled,
    is_retired,
    resolve_bot,
)
from core.telegram.guard import check, new_correlation_id, require, verify
from core.telegram import transport
from core.telegram import health as health_mod
from core.telegram import shadow

__all__ = [
    "Bot",
    "all_bots",
    "authorize",
    "by_token_env",
    "by_username",
    "get",
    "has_capability",
    "is_blocked",
    "is_enabled",
    "is_retired",
    "resolve_bot",
    "check",
    "new_correlation_id",
    "require",
    "verify",
    "transport",
    "health_mod",
    "shadow",
]
