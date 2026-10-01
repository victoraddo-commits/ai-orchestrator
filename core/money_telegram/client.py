"""core.money_telegram.client — akush-core API client for the bot.

Telegram is an interface, not truth (§44): every read/action goes through
the same akush-core API the PWA uses, authenticated with the ``bot`` service
token from vault ``secrets/money/service_tokens``. No separate truth, no
direct DB access.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request

from core.ai import kai_vault_client as _vault

logger = logging.getLogger("kai.money_telegram")

DEFAULT_BASE = os.environ.get("AKUSH_CORE_URL", "https://192.168.1.118:8095/api/v1")
VAULT_TOKENS_PATH = "secrets/money/service_tokens"
TIMEOUT = 15

_svc_cache: tuple[str, float] | None = None


def _lan_tls_context():
    """TLS context for the pinned LAN host akush-core (CT108, self-signed cert
    like CT111's :8000 — documented deviation, docs/akush/SECURITY.md §49).
    Verification is disabled for this host only; every other HTTPS target
    keeps default verification."""
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def bot_service_token() -> str:
    """Resolve the ``bot`` service token. Never logged; empty on failure."""
    global _svc_cache
    if _svc_cache and time.time() - _svc_cache[1] < 300:
        return _svc_cache[0]
    raw = os.environ.get("SERVICE_TOKENS", "")
    if not raw:
        try:
            token = _vault.load_token()
            if token:
                raw = _vault.fetch_secret(VAULT_TOKENS_PATH, token) or ""
        except Exception as exc:  # noqa: BLE001
            logger.warning("akush service-token fetch failed: %s", type(exc).__name__)
            raw = ""
    tok = ""
    if raw:
        try:
            tok = (json.loads(raw) or {}).get("bot") or ""
        except Exception:
            tok = ""
    _svc_cache = (tok, time.time())
    return tok


class AkushClient:
    """Minimal urllib client: (status, data) tuples, JSON errors surfaced."""

    def __init__(self, base: str | None = None, token: str | None = None):
        self.base = (base or DEFAULT_BASE).rstrip("/")
        self._token = token

    def _auth(self) -> str:
        return self._token if self._token is not None else bot_service_token()

    def request(self, method: str, path: str, body: dict | None = None,
                query: dict | None = None,
                idempotency_key: str | None = None) -> tuple[int, object]:
        url = f"{self.base}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(
                {k: v for k, v in query.items() if v is not None})
        data = None
        headers = {"authorization": f"Bearer {self._auth()}"}
        if body is not None:
            data = json.dumps(body).encode()
            headers["content-type"] = "application/json"
        if idempotency_key:
            headers["idempotency-key"] = idempotency_key
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT,
                                        context=_lan_tls_context()) as res:
                payload = res.read().decode() or "{}"
                try:
                    return res.status, json.loads(payload)
                except Exception:
                    return res.status, None
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read().decode() or "{}")
            except Exception:
                return exc.code, None
        except Exception as exc:  # noqa: BLE001
            logger.warning("akush-core %s %s failed: %s", method, path,
                           type(exc).__name__)
            return 0, {"error": "unreachable",
                       "message": type(exc).__name__}

    def get(self, path: str, query: dict | None = None):
        return self.request("GET", path, query=query)

    def post(self, path: str, body: dict | None = None,
             idempotency_key: str | None = None):
        return self.request("POST", path, body=body,
                            idempotency_key=idempotency_key)

    def ask(self, question: str):
        """POST /search/ask — deterministic NL query (§12)."""
        return self.post("/search/ask", {"q": question})


_client: AkushClient | None = None


def get_client() -> AkushClient:
    global _client
    if _client is None:
        _client = AkushClient()
    return _client
