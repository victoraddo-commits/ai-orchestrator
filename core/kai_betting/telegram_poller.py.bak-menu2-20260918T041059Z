"""KAI Bet — dedicated Telegram poller for @Betsportz_bot (§30).

Re-homed from CT103 (no internet) to CT111. Owns the Betsportz bot's
getUpdates offset exclusively; dispatches commands to BettingTelegramBot.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request

API = "https://api.telegram.org"
logger = logging.getLogger("kai_betting.telegram_poller")


def _token() -> str:
    return (os.environ.get("BETSPORTZ_BOT_TOKEN")
            or os.environ.get("TELEGRAM_BOT_TOKEN", "")).strip()


def _call(method: str, data: dict | None = None, timeout: int = 40) -> dict:
    tok = _token()
    if not tok:
        return {"ok": False, "error": "no token"}
    body = urllib.parse.urlencode(data or {}).encode()
    req = urllib.request.Request(f"{API}/bot{tok}/{method}", data=body)
    try:
        return json.load(urllib.request.urlopen(req, timeout=timeout))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": type(e).__name__}


def run_forever() -> None:
    from core.kai_betting.telegram_bot import BettingTelegramBot
    bot = BettingTelegramBot()
    offset = None
    logger.info("betsportz poller started")
    while True:
        params = {"timeout": 25}
        if offset is not None:
            params["offset"] = offset
        res = _call("getUpdates", params, timeout=40)
        if not res.get("ok"):
            time.sleep(5)
            continue
        for upd in res.get("result", []):
            offset = int(upd.get("update_id", 0)) + 1
            msg = upd.get("message") or upd.get("edited_message") or {}
            text = msg.get("text")
            chat_id = str((msg.get("chat") or {}).get("id", ""))
            user_id = str((msg.get("from") or {}).get("id", ""))
            if not text or not chat_id:
                continue
            try:
                reply = bot.handle_message(chat_id, text, user_id)
            except Exception as e:  # noqa: BLE001
                reply = f"Betting command error: {type(e).__name__}"
            if reply:
                _call("sendMessage", {"chat_id": chat_id, "text": reply,
                                      "disable_web_page_preview": "true"})


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    run_forever()
