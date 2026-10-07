"""Telegram WebApp (Mini App) initData authentication — roadmap 27C.

Telegram passes signed ``initData`` to the WebApp. It must be validated
server-side against the bot token before the app trusts the identity:

    secret_key = HMAC_SHA256(key="WebAppData", msg=bot_token)
    hash       = HMAC_SHA256(key=secret_key,  msg=data_check_string)

where ``data_check_string`` is every field except ``hash`` sorted by key and
joined with ``\\n`` as ``k=v``. stdlib-only.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


def _data_check_string(pairs: dict) -> str:
    return "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))


def validate_init_data(init_data: str, bot_token: str, max_age: int = 86400) -> dict:
    """Return {user, auth_date, query_id} or raise ValueError.

    ``max_age`` (seconds) rejects stale initData; 0 disables the freshness check.
    """
    if not init_data:
        raise ValueError("missing init_data")
    if not bot_token:
        raise ValueError("missing bot_token")

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received = pairs.pop("hash", "")
    if not received:
        raise ValueError("init_data has no hash")

    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, _data_check_string(pairs).encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        raise ValueError("init_data hash mismatch")

    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError:
        raise ValueError("invalid auth_date")
    if max_age and auth_date and (time.time() - auth_date) > max_age:
        raise ValueError("init_data expired")

    user = {}
    if pairs.get("user"):
        try:
            user = json.loads(pairs["user"])
        except json.JSONDecodeError:
            raise ValueError("invalid user json")
    return {"user": user, "auth_date": auth_date, "query_id": pairs.get("query_id")}


def sign_for_test(init_data_fields: dict, bot_token: str) -> str:
    """Build a valid init_data string from fields (used by tests and tooling)."""
    pairs = {k: str(v) for k, v in init_data_fields.items()}
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, _data_check_string(pairs).encode(), hashlib.sha256).hexdigest()
    from urllib.parse import urlencode
    return urlencode({**pairs, "hash": digest})
