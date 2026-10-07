"""Deerude bot — task/phase update channel (KAI 2.0 directive).

`@DeerudeClaude_Bot` is the operator's update feed for work in progress:
agents (OpenCode / Claude Code) and the Kai orchestrator push task/phase
progress here. stdlib-only; the bot token is never logged.

Token resolution order:
  1. env `DEERUDE_BOT_TOKEN`
  2. file at env `DEERUDE_BOT_TOKEN_FILE` (default /etc/kai/deerude_bot_token)
Chat id: env `DEERUDE_CHAT_ID` (default: operator chat 612786480).
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

def _token_file() -> str:
    return os.environ.get("DEERUDE_BOT_TOKEN_FILE", "/etc/kai/deerude_bot_token")


DEFAULT_CHAT_ID = os.environ.get("DEERUDE_CHAT_ID", "612786480")
API = "https://api.telegram.org"


def _token() -> str:
    tok = (os.environ.get("DEERUDE_BOT_TOKEN") or "").strip()
    if tok:
        return tok
    try:
        return Path(_token_file()).read_text().strip()
    except OSError:
        return ""


def is_enabled() -> bool:
    return bool(_token())


def format_update(title: str, *, phase: str = None, status: str = None,
                  detail: str = None) -> str:
    lines = ["\U0001f9ed Deerude update"]
    if phase:
        lines.append(f"Phase: {phase}")
    lines.append(title)
    if status:
        lines.append(f"Status: {status}")
    if detail:
        lines.append(detail)
    return "\n".join(lines)


def send(text: str, chat_id: str = None) -> dict:
    """Send a message via @DeerudeClaude_Bot. Never raises; never logs token."""
    tok = _token()
    if not tok:
        return {"ok": False, "error": "deerude token not configured"}
    data = urllib.parse.urlencode({
        "chat_id": chat_id or DEFAULT_CHAT_ID,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(f"{API}/bot{tok}/sendMessage", data=data)
    try:
        resp = json.load(urllib.request.urlopen(req, timeout=20))
        return {"ok": bool(resp.get("ok")),
                "message_id": (resp.get("result") or {}).get("message_id")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": type(e).__name__}
