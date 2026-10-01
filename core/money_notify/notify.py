"""core.money_notify — Akush Money Telegram notification fan-out (§45).

Subscribes (once, idempotent, fail-safe) to ``money.*`` on the kai event bus
*inside whichever Kai process runs the bus* (api + scheduler — the bus is
in-process; akush-core publishes cross-host via the bus HTTP route). Every
event passes the §45 gate (prefs mode, quiet hours, rate limit, dedupe), is
formatted (concise, masked refs), and is delivered via the akush233-bot
transport path.

Fail-safe states (never crash, never spam):
  * bot disabled in registry → not sent (reason recorded)
  * token absent (vault ``secrets/money/telegram_bot_token`` empty) → the
    message is HELD in the digest buffer, not dropped — the exact moment a
    token lands and the registry flag flips, the next flush delivers
  * unknown money.* topic → ignored (no spam)

No message bodies are ever logged — type + dedupe key only.
"""
from __future__ import annotations

import logging
import os
import time

from core import kai_event_bus
from core.telegram import registry as reg
from core.telegram import transport

from . import prefs as prefs_mod
from . import formatter as fmt_mod
from .gate import Gate

logger = logging.getLogger("kai.money_notify")

BOT_ID = "akush233-bot"

_sub_id: str | None = None
_registered_once = False


def register_subscriber() -> bool:
    """Attach the money.* subscriber. Idempotent + exception-isolated."""
    global _sub_id, _registered_once
    if _registered_once and _sub_id:
        # verify against the live bus so a re-bound instance never double-fires
        if _sub_id in kai_event_bus.event_bus._subscribers:
            return False
    try:
        _sub_id = kai_event_bus.subscribe("money.*", _on_event)
        _registered_once = True
        logger.info("money notify subscriber registered (sub=%s…)",
                    str(_sub_id)[:8])
        return True
    except Exception:
        logger.exception("money notify subscriber registration failed")
        return False


def _on_event(topic: str, envelope: dict) -> None:
    try:
        handle_event(topic, (envelope or {}).get("payload") or {})
    except Exception:
        logger.exception("money notify handle_event failed (topic type only: %s)",
                         prefs_mod.classify(topic))


def handle_event(topic: str, payload: dict, *, now: float | None = None,
                 send=None) -> dict:
    """Gate + format + deliver one event. Returns a decision record.

    Never raises; never logs message text or amounts.
    """
    type_name = prefs_mod.classify(topic)
    if type_name is None:
        return {"notified": False, "reason": "unrouted topic"}

    gate = Gate(prefs_mod.load_prefs(), clock=(lambda: now) if now is not None else None)
    dedupe_key = _dedupe_key(type_name, payload)
    decision = gate.decide(type_name, dedupe_key)

    if not decision["deliver"]:
        if decision["hold_digest"]:
            _buffer_digest(topic, type_name, payload)
        _audit("held", type_name, decision["reason"])
        return {"notified": False, "reason": decision["reason"],
                "held_digest": decision["hold_digest"]}

    gate.mark_seen(dedupe_key)

    text, buttons = _format(type_name, payload)
    result = (send or send_now)(text, buttons, now=now)
    delivered = bool(isinstance(result, dict) and result.get("sent"))
    if delivered:
        gate.record_sent(now)
        _audit("sent", type_name, "ok")
    else:
        reason = (result or {}).get("reason", "send failed")
        # hold rather than drop: a token arriving later still delivers it
        _buffer_digest(topic, type_name, payload)
        _audit("held", type_name, f"send: {reason}")
    return {"notified": delivered, "reason": decision["reason"],
            "send": result}


def _dedupe_key(type_name: str, payload: dict) -> str:
    for field in ("dedupe_key", "id", "event_id", "inbox_id", "commitment_id",
                  "occurrence_id", "debt_id", "account_id", "tx_id",
                  "transaction_id", "payday_id"):
        if payload.get(field) is not None:
            return f"{type_name}:{payload.get(field)}"
    return f"{type_name}:{hash(tuple(sorted((str(k), str(v)) for k, v in payload.items())))}"


def _format(type_name: str, payload: dict):
    try:
        ev = fmt_mod.format_event(type_name, payload)
        return ev["text"], ev.get("buttons") or []
    except Exception:
        return (f"Money update ({type_name})", [])


def _buffer_digest(topic: str, type_name: str, payload: dict) -> None:
    st = prefs_mod.load_state()
    buf = st.setdefault("digest_buffer", [])
    buf.append({"ts": time.time(), "topic": topic, "type": type_name,
                "payload": payload})
    prefs_mod.save_state()


def _audit(action: str, type_name: str, reason: str) -> None:
    logger.info("money.notify action=%s type=%s reason=%s",
                action, type_name, reason)


def send_now(text: str, buttons=None, *, now: float | None = None) -> dict:
    """Seam wrapper (tests monkeypatch this name); real work in deliver_now."""
    return deliver_now(text, buttons, now=now)


def deliver_now(text: str, buttons=None, *, now: float | None = None) -> dict:
    """Deliver one message via the akush233-bot transport path.

    Token resolution order: process env → registry env_file → vault
    (``secrets/money/telegram_bot_token`` materialized into the 0600 env
    file by core.money_telegram.token.ensure_env_file). Token-absent is a
    clean, non-crashing hold.
    """
    bot = reg.get(BOT_ID)
    if bot is None or not reg.is_enabled(bot):
        return {"sent": False, "reason": "bot disabled"}

    if not transport.token_for(bot):
        try:
            from core.money_telegram.token import ensure_env_file
            ensure_env_file()
        except Exception:
            pass
    if not transport.token_for(bot):
        return {"sent": False, "reason": "no token"}

    chat = prefs_mod.chat_id()
    if not chat:
        return {"sent": False, "reason": "no operator chat configured"}

    reply_markup = None
    if buttons:
        reply_markup = {"inline_keyboard": buttons}
    try:
        res = transport.send_message(bot, chat, text, reply_markup=reply_markup)
    except Exception as exc:  # noqa: BLE001
        logger.warning("money notify send failed: %s", type(exc).__name__)
        return {"sent": False, "reason": f"transport: {type(exc).__name__}"}
    if not isinstance(res, dict) or not res.get("ok"):
        logger.warning("money notify send rejected: %s",
                       (res or {}).get("error", "unknown"))
        return {"sent": False, "reason": "transport rejected"}
    return {"sent": True}


# ---------------------------------------------------------------------------
# digest flush (§45 daily digest + weekly/monthly rollups)
# ---------------------------------------------------------------------------
def flush_digest(kind: str = "daily", *, now: float | None = None,
                 send=None) -> dict:
    """Send the accumulated digest buffer as one rollup; clear the buffer."""
    st = prefs_mod.load_state()
    items = st.get("digest_buffer") or []
    text = fmt_mod.format_digest(kind, items)
    result = (send or send_now)(text, None, now=now)
    if isinstance(result, dict) and result.get("sent"):
        st["digest_buffer"] = []
        st["last_digest_ts"] = now if now is not None else time.time()
        prefs_mod.save_state()
        _audit("digest", kind, f"{len(items)} items")
    return {"sent": bool(isinstance(result, dict) and result.get("sent")),
            "items": len(items)}


def tick(now: float | None = None) -> dict:
    """Lazy digest flush check (called from the poller loop / handlers).

    Flushes the daily digest when the digest hour has arrived, and appends
    the weekly rollup on Mondays / monthly rollup on the 1st.
    """
    import datetime as _dt
    ts = now if now is not None else time.time()
    dt = _dt.datetime.fromtimestamp(ts)
    st = prefs_mod.load_state()
    last = st.get("last_digest_ts")
    prefs = prefs_mod.load_prefs()
    digest_hour = int(prefs.get("digest_hour", 8))

    due_daily = last is None or (
        dt.hour >= digest_hour
        and _dt.datetime.fromtimestamp(float(last)).date() < dt.date())
    if not due_daily:
        return {"flushed": False}
    kind = "daily"
    if dt.day == 1:
        kind = "monthly"
    elif dt.weekday() == 0:
        kind = "weekly"
    return flush_digest(kind, now=ts)


def runtime_state() -> dict:
    """Health/debug snapshot (no tokens, no message bodies)."""
    bot = reg.get(BOT_ID)
    token_ok = bool(transport.token_for(bot)) if bot else False
    st = prefs_mod.load_state()
    return {
        "bot": BOT_ID,
        "registry_enabled": reg.is_enabled(bot) if bot else False,
        "token_present": token_ok,
        "active": bool(reg.is_enabled(bot) and token_ok),
        "digest_buffered": len(st.get("digest_buffer") or []),
        "subscriber_registered": _sub_id is not None,
    }
