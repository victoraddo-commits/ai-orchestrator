"""core.money_telegram.poller — akush233-bot inbound long-poll service.

Mirrors core.kai_betting.telegram_poller (systemd-managed, owns its bot's
getUpdates offset, self-heals webhook conflicts, restart-safe). Fails safe:
until the bot token exists in vault AND the registry flag is enabled, the
poller idles quietly (no crash, no Telegram calls) and the digest flush
tick still runs — that is the disabled state the operator sees, and the
single flag flip + token in vault activates everything.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request

from core.telegram import registry as reg

from . import handlers
from .token import BOT_ID, token_available, runtime_ready, ensure_env_file

API = "https://api.telegram.org"
logger = logging.getLogger("kai.money_telegram")

POLL_TIMEOUT = 25
ERROR_BACKOFF = 5
CONFLICT_BACKOFF = 60
IDLE_BACKOFF = 60


def _token() -> str:
    bot = reg.get(BOT_ID)
    tok = ""
    try:
        from core.telegram.transport import token_for
        tok = token_for(bot) or ""
    except Exception:
        tok = ""
    return tok.strip()


def _call(method: str, data: dict | None = None, timeout: int = 40) -> dict:
    tok = _token()
    if not tok:
        return {"ok": False, "error": "no token"}
    body = urllib.parse.urlencode(data or {}).encode()
    req = urllib.request.Request(f"{API}/bot{tok}/{method}", data=body)
    try:
        return json.load(urllib.request.urlopen(req, timeout=timeout))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__}


def _delete_webhook() -> bool:
    try:
        return bool(_call("deleteWebhook", {"drop_pending_updates": "false"}).get("ok"))
    except Exception:
        return False


def run_once(update: dict, *, client=None, send=None) -> dict:
    """One update through the guard + handlers (test seam)."""
    bot = reg.get(BOT_ID)
    try:
        from core.telegram import guard
        chat_id = None
        callback = update.get("callback_query") if isinstance(update, dict) else None
        message = (update.get("message") or {}) if isinstance(update, dict) else {}
        chat_id = ((callback or {}).get("message", {}).get("chat", {}).get("id")
                   or message.get("chat", {}).get("id"))
        gate = guard.check(bot, "money.query", chat_id=chat_id)
        if not gate.get("allowed"):
            return {"handled": False, "reason": gate.get("reason", "denied")}
    except Exception:
        pass
    return handlers.handle_update(update, client=client, send=send)


def run_forever() -> None:
    logger.info("akush money telegram poller starting")
    while not runtime_ready():
        # disabled state: token missing or registry flag off — idle quietly,
        # keep attempting the vault bootstrap so a token landing in vault is
        # picked up within a minute (post-flag-flip restart not required)
        if not token_available():
            ensure_env_file()
        logger.info("akush bot disabled (registry_enabled=%s token=%s) — idling",
                    bool(reg.get(BOT_ID) and reg.is_enabled(reg.get(BOT_ID))),
                    bool(token_available()))
        time.sleep(IDLE_BACKOFF)

    _delete_webhook()
    offset = None
    logger.info("akush money poller active (@akush233bot)")
    while True:
        params = {"timeout": POLL_TIMEOUT}
        if offset is not None:
            params["offset"] = offset
        res = _call("getUpdates", params, timeout=40)
        if not res.get("ok"):
            desc = str(res.get("description") or res.get("error") or "")
            if "webhook" in desc.lower():
                _delete_webhook()
                continue
            if "409" in desc or "conflict" in desc.lower():
                time.sleep(CONFLICT_BACKOFF)
                continue
            time.sleep(ERROR_BACKOFF)
            continue
        for update in res.get("result") or []:
            offset = (update.get("update_id") or 0) + 1
            try:
                run_once(update)
            except Exception:
                # never log update content — one failure must not kill the loop
                logger.exception("money bot update handling failed")
        try:
            from core.money_notify import notify
            notify.tick()
        except Exception:
            pass


if __name__ == "__main__":
    import logging as _l
    _l.basicConfig(level=_l.INFO)
    run_forever()
