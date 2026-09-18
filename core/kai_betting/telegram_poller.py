"""KAI Bet - dedicated Telegram poller for @Betsportz_bot.

Re-homed from CT103 (no internet) to CT111. Owns the Betsportz bot's
getUpdates offset exclusively; dispatches commands and inline-menu callbacks
to BettingTelegramBot.
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

MENU = [
    {"command": "odds", "description": "Odds groups - pick one to view"},
    {"command": "groups", "description": "Choose a bet group"},
    {"command": "picks", "description": "Today's predictions"},
    {"command": "results", "description": "Recent results"},
    {"command": "performance", "description": "Win rate and ROI"},
    {"command": "subscribe", "description": "Get premium access"},
    {"command": "myaccount", "description": "My subscription"},
    {"command": "help", "description": "All commands"},
]


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


def _send(chat_id, text, reply_markup=None, message_id=None) -> dict:
    data = {"chat_id": chat_id, "text": text, "disable_web_page_preview": "true",
            "parse_mode": "Markdown"}
    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup)
    if message_id:
        data["message_id"] = str(message_id)
        res = _call("editMessageText", data)
    else:
        res = _call("sendMessage", data)
    if not res.get("ok"):        # Markdown parse failed -> resend plain
        data.pop("parse_mode", None)
        res = _call("editMessageText" if message_id else "sendMessage", data)
    return res


def _set_menu() -> None:
    _call("setMyCommands", {"commands": json.dumps(MENU)})


def run_forever() -> None:
    from core.kai_betting.telegram_bot import BettingTelegramBot
    bot = BettingTelegramBot()
    _set_menu()
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

            cb = upd.get("callback_query")
            if cb:
                cid = str(((cb.get("message") or {}).get("chat") or {}).get("id", ""))
                uid = str((cb.get("from") or {}).get("id", ""))
                mid = (cb.get("message") or {}).get("message_id")
                try:
                    out = bot.handle_callback(cb.get("data", ""), uid)
                except Exception as e:  # noqa: BLE001
                    out = {"text": f"Menu error: {type(e).__name__}", "reply_markup": None}
                _call("answerCallbackQuery", {"callback_query_id": cb.get("id", "")})
                if cid:
                    _send(cid, out["text"], out.get("reply_markup"), message_id=mid)
                continue

            msg = upd.get("message") or upd.get("edited_message") or {}
            text = msg.get("text")
            chat_id = str((msg.get("chat") or {}).get("id", ""))
            user_id = str((msg.get("from") or {}).get("id", ""))
            if not text or not chat_id:
                continue
            try:
                out = bot.handle_update(chat_id, text, user_id)
            except Exception as e:  # noqa: BLE001
                out = {"text": f"Betting command error: {type(e).__name__}", "reply_markup": None}
            if out.get("text"):
                _send(chat_id, out["text"], out.get("reply_markup"))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    run_forever()
