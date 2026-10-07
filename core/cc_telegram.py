"""Command Center — Telegram control actions (write).

Lets the Command Center operate the Telegram layer: list registered bots, set a
bot's command menu, and send a message — reusing the Telegram Module registry
and each bot's own token (server-side; never exposed to the browser).
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.telegram import registry as reg

cc_telegram_router = APIRouter(prefix="/api/cc/telegram", tags=["cc-telegram"])


def _token(bot) -> str:
    tok = (os.environ.get(bot.token_env or "") or "").strip()
    if tok:
        return tok
    try:
        text = Path(bot.env_file or "").read_text().strip()
        return text if ("=" not in text and ":" in text) else ""
    except OSError:
        return ""


def _bot(bot_id):
    bot = reg.get(bot_id)
    if bot is None:
        raise HTTPException(status_code=404, detail="unknown bot")
    return bot


class CmdBody(BaseModel):
    bot_id: str
    commands: list | None = None


class SendBody(BaseModel):
    bot_id: str
    chat_id: str
    text: str


@cc_telegram_router.get("/bots")
def bots():
    return {"bots": [{"bot_id": b.bot_id, "username": b.username,
                      "owner_module": b.owner_module, "host": b.host,
                      "enabled": reg.is_enabled(b)} for b in reg.all_bots()]}


@cc_telegram_router.post("/set-commands")
def set_commands(body: CmdBody):
    bot = _bot(body.bot_id)
    tok = _token(bot)
    if not tok:
        raise HTTPException(status_code=400, detail="no token for bot")
    cmds = body.commands or [{"command": "start", "description": "Start"},
                             {"command": "help", "description": "Help"}]
    data = urllib.parse.urlencode({"commands": json.dumps(cmds)}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{tok}/setMyCommands", data=data)
    try:
        d = json.load(urllib.request.urlopen(req, timeout=15))
        return {"ok": bool(d.get("ok"))}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=type(e).__name__)


@cc_telegram_router.post("/send")
def send(body: SendBody):
    bot = _bot(body.bot_id)
    tok = _token(bot)
    if not tok:
        raise HTTPException(status_code=400, detail="no token for bot")
    data = urllib.parse.urlencode({"chat_id": body.chat_id, "text": body.text}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage", data=data)
    try:
        d = json.load(urllib.request.urlopen(req, timeout=15))
        return {"ok": bool(d.get("ok")),
                "message_id": (d.get("result") or {}).get("message_id")}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=type(e).__name__)
