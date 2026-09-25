"""Unified Telegram transport (KAI 2.0 directive §1, Phase 4).

One implementation of the Telegram Bot API used by every bot. Bot-specific
concerns (which token, which chat) are parameters resolved from the **Bot
Registry**; business logic stays in the owning module (§2, §46).

The existing `core.telegram_bridge` remains the Kai-Core operator transport;
this facade gives every bot a single, identically-behaved transport surface
without rewriting their loops (compatibility layer, §35 Phase 3).

stdlib-only; never logs tokens.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

from core.telegram import registry as reg

API = "https://api.telegram.org"
DEFAULT_TIMEOUT = 20


def _raw_token_file(path: str) -> str:
    """Read a raw token from a file (only for raw-token files, e.g. Deerude)."""
    try:
        text = Path(path).read_text().strip()
    except OSError:
        return ""
    # raw token files are a single "id:secret" line; dotenv files are not.
    return text if ("=" not in text and ":" in text) else ""


def _dotenv_token(path: str, key: str) -> str:
    """Read ``key`` from a dotenv-style file, honouring quotes and ``export``.

    This is the fix for the registry `env_file`: the systemd unit does NOT
    inject ``/opt/ai-orchestrator/.env`` into the process environment, so
    reading ``os.environ`` alone left every bot token MISSING at runtime.
    """
    if not path or not key:
        return ""
    try:
        text = Path(path).read_text()
    except OSError:
        return ""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        name, sep, val = line.partition("=")
        if sep and name.strip() == key:
            return val.strip().strip('"').strip("'")
    return ""


def token_for(bot) -> str:
    """Resolve a bot's token: process env, then its dotenv file, then raw file.

    Order preserves precedence (explicit env wins) while making the registry's
    ``env_file`` authoritative when the process env has not been seeded (CLI,
    scheduler, ad-hoc callers). Never logged.
    """
    if bot is None:
        return ""
    key = getattr(bot, "token_env", "") or ""
    env_file = getattr(bot, "env_file", "") or ""
    tok = (os.environ.get(key, "") or "").strip()
    if tok:
        return tok
    tok = _dotenv_token(env_file, key)
    if tok:
        return tok
    # Raw "id:secret" file (a bot may declare a token_env but keep only a raw
    # file, e.g. Deerude). Only valid when the file has no '=' in it.
    raw = _raw_token_file(env_file)
    if raw:
        return raw
    return ""


def is_transportable(bot) -> bool:
    """A bot may only transport if it is registered, enabled, and has a token."""
    return bool(bot and reg.is_enabled(bot) and token_for(bot))


def _call(bot, method: str, data: dict = None, *, timeout: int = DEFAULT_TIMEOUT) -> dict:
    tok = token_for(bot)
    if not tok:
        return {"ok": False, "error": "no token"}
    body = urllib.parse.urlencode(data or {}).encode()
    req = urllib.request.Request(f"{API}/bot{tok}/{method}", data=body)
    try:
        return json.load(urllib.request.urlopen(req, timeout=timeout))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": type(e).__name__}


def send_message(bot, chat_id, text: str, *, parse_mode: str = None,
                 reply_markup=None) -> dict:
    if not is_transportable(bot):
        return {"ok": False, "error": "bot not transportable"}
    data = {"chat_id": chat_id, "text": text,
            "disable_web_page_preview": "true"}
    if parse_mode:
        data["parse_mode"] = parse_mode
    if reply_markup is not None:
        data["reply_markup"] = (reply_markup if isinstance(reply_markup, str)
                                else json.dumps(reply_markup))
    return _call(bot, "sendMessage", data)


def get_updates(bot, offset: int = None, *, poll_timeout: int = 0,
                allowed_updates=None) -> dict:
    if not is_transportable(bot):
        return {"ok": False, "error": "bot not transportable"}
    data = {"timeout": poll_timeout}
    if offset is not None:
        data["offset"] = offset
    if allowed_updates:
        data["allowed_updates"] = json.dumps(allowed_updates)
    return _call(bot, "getUpdates", data, timeout=DEFAULT_TIMEOUT + 15)


def send_voice(bot, chat_id, audio_bytes: bytes, duration: int = None) -> dict:
    # Voice upload needs multipart; keep it minimal and explicit.
    if not is_transportable(bot):
        return {"ok": False, "error": "bot not transportable"}
    boundary = "----kaitgmoduleboundary"
    parts = []
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; "
                 f'name="chat_id"\r\n\r\n{chat_id}\r\n'.encode())
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; "
                 f'name="voice"; filename="voice.ogg"\r\n'
                 f"Content-Type: audio/ogg\r\n\r\n".encode()
                 + audio_bytes + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    tok = token_for(bot)
    req = urllib.request.Request(f"{API}/bot{tok}/sendVoice", data=body,
                                 headers={"Content-Type":
                                          f"multipart/form-data; boundary={boundary}"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=60))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": type(e).__name__}
