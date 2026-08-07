"""Thin Telegram Bot HTTP API client for the SUSU bot -- separate token,
separate offset file, separate module from core.telegram_bridge (the ops bot)
and core.law_tutor. This bot handles group-based savings (SUSU/ROSCA)
operations only.

Config: SUSU_BOT_TOKEN in the environment (see .env).
"""

import json
import os
from pathlib import Path

import requests

OFFSET_FILE = Path("memory") / "susu_last_update_id.txt"


def _load_token():
    token = os.environ.get("SUSU_BOT_TOKEN")
    if not token:
        raise RuntimeError("SUSU_BOT_TOKEN is not set")
    return token


def _api_url(method, token):
    return f"https://api.telegram.org/bot{token}/{method}"


def send_message(chat_id, text, token=None, reply_markup=None):
    if token is None:
        token = _load_token()

    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        response = requests.post(
            _api_url("sendMessage", token),
            json=payload,
            timeout=15,
        )
        response.raise_for_status()
        body = response.json()
    except Exception as error:
        raise RuntimeError(f"Telegram sendMessage failed: {type(error).__name__}") from error

    if not body.get("ok"):
        raise RuntimeError(f"Telegram sendMessage returned not ok: {body.get('description', 'unknown')}")

    return body


def edit_message_reply_markup(chat_id, message_id, reply_markup=None, token=None):
    if token is None:
        token = _load_token()

    payload = {"chat_id": chat_id, "message_id": message_id}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    try:
        response = requests.post(
            _api_url("editMessageReplyMarkup", token),
            json=payload,
            timeout=15,
        )
        response.raise_for_status()
        body = response.json()
    except Exception as error:
        raise RuntimeError(f"Telegram editMessageReplyMarkup failed: {type(error).__name__}") from error

    if not body.get("ok"):
        raise RuntimeError(f"Telegram editMessageReplyMarkup returned not ok: {body.get('description', 'unknown')}")

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
                "allowed_updates": json.dumps(["message", "callback_query"]),
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

        _last_update_id = update_id

        if "callback_query" in update:
            cq = update["callback_query"]
            msg_data = cq.get("message") or {}
            from_data = cq.get("from") or {}
            messages.append({
                "update_id": update_id,
                "type": "callback_query",
                "callback_id": cq.get("id", ""),
                "data": cq.get("data", ""),
                "chat_id": str((msg_data.get("chat") or {}).get("id", "")),
                "message_id": msg_data.get("message_id"),
                "from": {
                    "id": str(from_data.get("id", "")),
                    "username": from_data.get("username", ""),
                    "first_name": from_data.get("first_name", ""),
                },
            })
            continue

        msg = update.get("message") or {}
        text = (msg.get("text") or "").strip()
        chat_id = str((msg.get("chat") or {}).get("id", ""))
        if not chat_id:
            continue

        messages.append({
            "update_id": update_id,
            "type": "message",
            "chat_id": chat_id,
            "text": text,
            "message_id": msg.get("message_id"),
            "from": {
                "id": str((msg.get("from") or {}).get("id", "")),
                "username": (msg.get("from") or {}).get("username", ""),
                "first_name": (msg.get("from") or {}).get("first_name", ""),
            },
        })

    if _last_update_id is not None:
        _save_last_offset(_last_update_id)

    return messages


def answer_callback_query(callback_query_id, text=None, token=None):
    if token is None:
        token = _load_token()

    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text

    try:
        response = requests.post(
            _api_url("answerCallbackQuery", token),
            json=payload,
            timeout=15,
        )
        response.raise_for_status()
        body = response.json()
    except Exception as error:
        raise RuntimeError(f"Telegram answerCallbackQuery failed: {type(error).__name__}") from error

    if not body.get("ok"):
        raise RuntimeError(f"Telegram answerCallbackQuery returned not ok: {body.get('description', 'unknown')}")

    return body
