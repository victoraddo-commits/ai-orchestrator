"""KAI Telegram Module — capability guard (directive §17-§19, §31, §47).

Single, deny-by-default checkpoint a bot request passes through *before* its
owning module runs business logic. It:
  * enforces registered capabilities (registry),
  * delegates generic security to AgentGuard when available (§18),
  * stamps a correlation id that survives the request path (§31),
  * emits a safe audit event (never logs tokens/secrets).

It does NOT replace AgentGuard — it is the Telegram-module layer that calls it.
"""
from __future__ import annotations

import logging
import uuid

from core.telegram import registry as reg

logger = logging.getLogger("kai.telegram.guard")


def new_correlation_id() -> str:
    return uuid.uuid4().hex[:16]


def _agentguard(payload: dict) -> dict:
    """Delegate to the existing AgentGuard if present; else abstain.

    Returns {"allowed": bool, "reason": str}.
    """
    try:
        from core.agentguard import guard as ag  # type: ignore
        for attr in ("evaluate", "check", "authorize"):
            fn = getattr(ag, attr, None)
            if callable(fn):
                return fn(payload) or {"allowed": True, "reason": f"{attr} (no verdict)"}
    except Exception:  # noqa: BLE001 - AgentGuard optional at this layer
        pass
    return {"allowed": True, "reason": "agentguard not wired"}


def _audit(correlation_id, bot, capability, allowed, reason) -> None:
    logger.info(
        "telegram.access bot=%s module=%s cap=%s allowed=%s corr=%s reason=%s",
        getattr(bot, "bot_id", None), getattr(bot, "owner_module", None),
        capability, allowed, correlation_id, reason,
    )


def check(bot, capabilities, *, chat_id=None, user_id=None,
          correlation_id=None) -> dict:
    """Authorize a bot for one or more capabilities. Deny-by-default."""
    corr = correlation_id or new_correlation_id()
    caps = (capabilities if isinstance(capabilities, (list, tuple, set))
            else [capabilities])
    for cap in caps:
        decision = reg.authorize(bot, cap)
        if not decision["allowed"]:
            _audit(corr, bot, cap, False, decision["reason"])
            return {"allowed": False, "reason": decision["reason"],
                    "capability": cap, "correlation_id": corr}
    verdict = _agentguard({
        "source": "telegram",
        "bot": getattr(bot, "bot_id", None),
        "module": getattr(bot, "owner_module", None),
        "capabilities": list(caps),
        "chat_id": chat_id,
        "user_id": user_id,
    })
    if not verdict.get("allowed", True):
        reason = verdict.get("reason", "agentguard denied")
        _audit(corr, bot, ",".join(caps), False, reason)
        return {"allowed": False, "reason": reason, "correlation_id": corr}
    _audit(corr, bot, ",".join(caps), True, "allowed")
    return {"allowed": True, "correlation_id": corr}


def require(bot, capability, **kwargs) -> dict:
    """Convenience: check() a single capability."""
    return check(bot, capability, **kwargs)


def verify(bot, *, chat_id=None, user_id=None, correlation_id=None) -> dict:
    """Identity gate: allow only registered, enabled bots.

    Used at a transport chokepoint where capability granularity is applied
    downstream (e.g. the Kai Core operator bot, which already routes commands
    through the Command Bus + AgentGuard).
    """
    corr = correlation_id or new_correlation_id()
    if bot is None:
        _audit(corr, None, "-", False, "unknown bot")
        return {"allowed": False, "reason": "unknown bot",
                "correlation_id": corr}
    if reg.is_retired(bot):
        _audit(corr, bot, "-", False, "bot retired")
        return {"allowed": False, "reason": "bot retired",
                "correlation_id": corr}
    if not reg.is_enabled(bot):
        _audit(corr, bot, "-", False, "bot disabled")
        return {"allowed": False, "reason": "bot disabled",
                "correlation_id": corr}
    _audit(corr, bot, "-", True, "identified")
    return {"allowed": True, "reason": "identified", "correlation_id": corr,
            "bot_id": bot.bot_id, "owner_module": bot.owner_module}
