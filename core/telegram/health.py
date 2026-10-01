"""Telegram Module health + observability (directive §31, §32, Phase 9).

Separates Telegram-Module health from owning-application health (§32): the
module can be healthy while a single bot/app is degraded, and vice versa.
stdlib-only, read-only.
"""
from __future__ import annotations

from core.telegram import registry as reg

# Inbound authorization gates per bot (where the module is enforced).
INBOUND_GATES = {
    "kai-enzo-bot": "core.telegram_bridge.route_inbound_reply (identity)",
    "juris-kai-bot": "core.juris_kai.bot.handle_message (legal.query)",
    "betsportz-bot": "core.kai_betting.telegram_bot.handle_message (betting.query)",
    "susugh-bot": "susu.telegram.bot.handle_command (susu.group)",
    "akush233-bot": "core.money_telegram.handlers.handle_update (money.query)",
}

# akush233-bot LEFT this set in Phase 6: it now has inbound handlers (the
# money Telegram poller) and is inbound-capable. The OUTBOUND_ONLY invariant
# is NOT weakened — deerudeclaude-bot remains outbound-only (agent updates).
OUTBOUND_ONLY = {"deerudeclaude-bot"}


def bots_report() -> list:
    out = []
    for b in reg.all_bots():
        out.append({
            "bot_id": b.bot_id,
            "username": b.username,
            "owner_module": b.owner_module,
            "host": b.host,
            "enabled": reg.is_enabled(b),
            "status": b.status,
            "capabilities": sorted(b.capabilities),
            "inbound_gate": INBOUND_GATES.get(b.bot_id),
            "role": "outbound" if b.bot_id in OUTBOUND_ONLY else "inbound",
        })
    return out


def health() -> dict:
    bots = bots_report()
    return {
        "module": "telegram",
        "ok": True,
        "registered": len(bots),
        "active": sum(1 for b in bots if b["enabled"]),
        "blocked": sorted(reg.BLOCKED_BOT_NAMES),
        "bots": bots,
    }
