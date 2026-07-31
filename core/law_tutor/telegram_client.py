"""Thin Telegram Bot HTTP API client for the Law Tutor bot -- deliberately a
separate token, separate offset file, and separate module from
core.telegram_bridge (the ops bot). This file must never import
core.build_manager, core.approval, or anything else that could grant an
operational capability: this bot is education-only for one specific,
non-operational user, and that boundary is enforced by keeping this module's
import graph completely disjoint from Kai's operational chat path.

Config: LAW_TUTOR_BOT_TOKEN in the environment (see .env).
"""

import json
import os
from pathlib import Path

import requests

OFFSET_FILE = Path("memory") / "law_tutor_last_update_id.txt"


def _load_token():
    token = os.environ.get("LAW_TUTOR_BOT_TOKEN")
    if not token:
        raise RuntimeError("LAW_TUTOR_BOT_TOKEN is not set")
    return token


def _api_url(method, token):
    return f"https://api.telegram.org/bot{token}/{method}"


def send_message(chat_id, text, token=None):
    if token is None:
        token = _load_token()

    try:
        response = requests.post(
            _api_url("sendMessage", token),
            json={"chat_id": chat_id, "text": text},
            timeout=15,
        )
        response.raise_for_status()
        body = response.json()
    except Exception as error:
        raise RuntimeError(f"Telegram sendMessage failed: {type(error).__name__}") from error

    if not body.get("ok"):
        raise RuntimeError(f"Telegram sendMessage returned not ok: {body.get('description', 'unknown')}")

    return body


_last_update_id = None


def _load_last_offset():
    try:
        if OFFSET_FILE.exists():
            return int(OFFSET_FILE.read_text().strip())
    except Exception:
        pass
    return None


def _save_last_offset(update_id):
    try:
        OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
        OFFSET_FILE.write_text(str(update_id))
    except Exception:
        pass


def _resolve_offset():
    global _last_update_id

    if _last_update_id is not None:
        return _last_update_id + 1

    _last_update_id = _load_last_offset()
    if _last_update_id is not None:
        return _last_update_id + 1

    return None


def poll_updates(token=None):
    global _last_update_id

    if token is None:
        token = _load_token()

    try:
        response = requests.get(
            _api_url("getUpdates", token),
            params={
                "offset": _resolve_offset(),
                "timeout": 0,
                "allowed_updates": json.dumps(["message"]),
            },
            timeout=15,
        )
        response.raise_for_status()
        body = response.json()
    except Exception as error:
        raise RuntimeError(f"Telegram getUpdates failed: {type(error).__name__}") from error

    if not body.get("ok"):
        raise RuntimeError(f"Telegram getUpdates returned not ok: {body.get('description', 'unknown')}")

    messages = []

    for update in body.get("result", []):
        update_id = update.get("update_id")
        msg = update.get("message") or {}

        _last_update_id = update_id

        text = (msg.get("text") or "").strip()
        chat_id = str((msg.get("chat") or {}).get("id", ""))
        if not text or not chat_id:
            continue

        messages.append({
            "update_id": update_id,
            "chat_id": chat_id,
            "text": text,
            "from": {
                "id": str((msg.get("from") or {}).get("id", "")),
                "username": (msg.get("from") or {}).get("username", ""),
                "first_name": (msg.get("from") or {}).get("first_name", ""),
            },
        })

    if _last_update_id is not None:
        _save_last_offset(_last_update_id)

    return messages
