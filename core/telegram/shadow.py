"""Shadow validation (directive §35 Phase 7) — non-destructive routing checks.

Given a bot identity and an intended capability, report how the Telegram Module
*would* decide, without executing, sending, or mutating anything. Lets old and
new routing be compared safely before/after cutover.
"""
from __future__ import annotations

from core.telegram import guard
from core.telegram import registry as reg


def shadow_route(*, username=None, token_env=None, host=None, env_file=None,
                 capability=None) -> dict:
    bot = reg.resolve_bot(username=username, token_env=token_env, host=host,
                          env_file=env_file)
    verdict = (guard.check(bot, capability) if capability else guard.verify(bot))
    return {
        "resolved_bot": getattr(bot, "bot_id", None),
        "owner_module": getattr(bot, "owner_module", None),
        "registered": bot is not None,
        "enabled": reg.is_enabled(bot),
        "capability": capability,
        "allowed": verdict.get("allowed"),
        "reason": verdict.get("reason"),
    }


_CAPS = ("legal.query", "legal.retrieve", "susu.group", "betting.query",
         "money.notify", "notify.send", "proxmox.admin")


def validate_matrix() -> dict:
    """Routing + capability matrix for every registered bot (no side effects)."""
    out = {}
    for b in reg.all_bots():
        out[b.bot_id] = {c: reg.authorize(b, c)["allowed"] for c in _CAPS}
    return out
