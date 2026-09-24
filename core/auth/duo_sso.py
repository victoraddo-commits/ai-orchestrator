"""Kai Duo-first SSO — the central auth plane (roadmap: Duo SSO, from 27S).

A single place every password-protected feature can delegate to: authenticate a
user with a **Duo push** and mint a short-lived, HMAC-signed session that other
services verify over ``/auth/duo/verify``. Also the source of truth for
"high-risk approval" so the vault can require a phone tap before revealing
sensitive secrets.

Design:
  * ``approve(user, purpose)`` — one push per purpose per TTL (cache → no spam).
  * ``login(user, scopes)``   — push → signed SSO session (+ a Command Center
    session token when the orchestrator can mint one).
  * ``sign/verify_session``   — stateless HMAC-signed tokens; revocation list.
  * Fail-closed: if Duo is configured but the push is denied/unreachable, login
    and high-risk approval fail. Disabled only when Duo is entirely unconfigured.

Secret: ``KAI_DUO_SESSION_SECRET`` env or ``/etc/kai/duo_session_secret`` (0600,
auto-generated). stdlib-only; reuses ``core.vault.duo`` for the push.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path

from core.vault.duo import DuoClient, DuoError, config_from_env, is_configured

logger = logging.getLogger("kai.auth.duo_sso")

_SECRET_ENV = "KAI_DUO_SESSION_SECRET"
_SECRET_FILE = os.environ.get("KAI_DUO_SESSION_SECRET_FILE", "/etc/kai/duo_session_secret")
SESSION_TTL = int(os.environ.get("KAI_DUO_SESSION_TTL", "28800"))        # 8h
APPROVAL_TTL = int(os.environ.get("KAI_DUO_APPROVAL_TTL", "300"))        # 5m
DEFAULT_ROLE = os.environ.get("KAI_DUO_DEFAULT_ROLE", "operator")
BREAKGLASS = os.environ.get("KAI_DUO_BREAKGLASS", "") == "1"
# Optional allowlist — when non-empty, only these Duo usernames may use SSO.
ALLOWED_USERS = {u.strip().lower() for u in
                 os.environ.get("KAI_DUO_ALLOWED_USERS", "").split(",") if u.strip()}


def user_allowed(username: str) -> bool:
    return (not ALLOWED_USERS) or (username.strip().lower() in ALLOWED_USERS)

_approvals: dict = {}   # "user:purpose" -> expiry epoch
_revoked: set = set()   # revoked jti


def _secret() -> str:
    s = os.environ.get(_SECRET_ENV)
    if s:
        return s
    try:
        v = Path(_SECRET_FILE).read_text().strip()
        if v:
            return v
    except OSError:
        pass
    v = secrets.token_hex(32)
    try:
        Path(_SECRET_FILE).parent.mkdir(parents=True, exist_ok=True)
        Path(_SECRET_FILE).write_text(v)
        os.chmod(_SECRET_FILE, 0o600)
    except OSError:
        pass
    return v


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign_session(user: str, scopes=("login",), ttl: int | None = None,
                 secret: str | None = None) -> str:
    secret = secret or _secret()
    now = int(time.time())
    payload = {"sub": user, "scopes": list(scopes), "iat": now,
               "exp": now + (ttl or SESSION_TTL), "jti": secrets.token_hex(8)}
    raw = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    sig = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).digest()
    return raw + "." + _b64e(sig)


def verify_session(token: str, secret: str | None = None) -> dict | None:
    if not token or "." not in token:
        return None
    secret = secret or _secret()
    raw, _, sig = token.partition(".")
    expected = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).digest()
    try:
        if not hmac.compare_digest(_b64d(sig), expected):
            return None
        claims = json.loads(_b64d(raw))
    except (ValueError, json.JSONDecodeError):
        return None
    if claims.get("exp", 0) < time.time() or claims.get("jti") in _revoked:
        return None
    return claims


def revoke(token: str, secret: str | None = None) -> bool:
    claims = verify_session(token, secret)
    if not claims:
        return False
    _revoked.add(claims.get("jti"))
    return True


def enabled() -> bool:
    return is_configured() or BREAKGLASS


def approve(username: str, purpose: str = "login", timeout: int = 60,
            ttl: int | None = None) -> dict:
    """Push-approve (cached per user+purpose for ttl). Fail-closed."""
    if not user_allowed(username):
        return {"approved": False, "error": "user not permitted for Duo SSO"}
    key = f"{username}:{purpose}"
    now = time.time()
    if _approvals.get(key, 0) > now:
        return {"approved": True, "cached": True}
    if BREAKGLASS:
        _approvals[key] = now + (ttl or APPROVAL_TTL)
        return {"approved": True, "breakglass": True}
    if not is_configured():
        return {"approved": False, "error": "duo not configured"}
    cfg = config_from_env()
    try:
        res = DuoClient(cfg["ikey"], cfg["skey"], cfg["host"]).approve(username, timeout=timeout)
    except Exception as e:  # noqa: BLE001 - fail closed on any Duo/network error
        return {"approved": False, "error": f"duo error: {type(e).__name__}: {e}"}
    if res.get("approved"):
        _approvals[key] = now + (ttl or APPROVAL_TTL)
    return res


def is_approved(username: str, purpose: str = "login") -> bool:
    return _approvals.get(f"{username}:{purpose}", 0) > time.time()


def login(username: str, scopes=("login",), timeout: int = 60,
          ttl: int | None = None) -> dict:
    """Duo-approve, then mint an SSO session (+ a Command Center session)."""
    if not enabled():
        return {"ok": False, "error": "duo not configured"}
    ap = approve(username, purpose="login", timeout=timeout)
    if not ap.get("approved"):
        return {"ok": False, "error": "push not approved", "detail": ap}
    token = sign_session(username, scopes, ttl=ttl)
    cc_token = _mint_cc_session(username)
    return {"ok": True, "user": username, "token": token,
            "cc_token": cc_token, "expires_in": ttl or SESSION_TTL,
            "scopes": list(scopes)}


def _mint_cc_session(username: str) -> str | None:
    """Mint an orchestrator (Command Center) session JWT for a Duo-authed user.

    Returns None if the orchestrator session store is unavailable — callers
    that require a usable Command Center session must treat None as failure
    rather than falling back to the (non-JWT) Duo HMAC token.
    """
    try:
        from core import authz
        return authz.create_session_for(username, role=DEFAULT_ROLE)
    except Exception as exc:  # noqa: BLE001 - surface but don't fail the Duo push
        logger.warning("duo_sso: could not mint Command Center session: %s", exc)
        return None


def _mint(username: str, scopes, ttl) -> dict:
    token = sign_session(username, scopes, ttl=ttl)
    cc_token = _mint_cc_session(username)
    return {"ok": True, "user": username, "token": token, "cc_token": cc_token,
            "expires_in": ttl or SESSION_TTL, "scopes": list(scopes)}


def send_sms(username: str, timeout: float = 15.0) -> dict:
    """Send a one-time passcode by SMS (fallback when no push device exists)."""
    if not is_configured():
        return {"ok": False, "error": "duo not configured"}
    cfg = config_from_env()
    try:
        res = DuoClient(cfg["ikey"], cfg["skey"], cfg["host"], timeout=timeout).send_sms(username)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "sent": True, "result": res.get("result")}


def login_with_passcode(username: str, passcode: str, scopes=("login",),
                        ttl: int | None = None) -> dict:
    """Complete login by verifying an SMS passcode."""
    if not is_configured():
        return {"ok": False, "error": "duo not configured"}
    if not passcode or not str(passcode).strip().isdigit():
        return {"ok": False, "error": "passcode required"}
    cfg = config_from_env()
    try:
        res = DuoClient(cfg["ikey"], cfg["skey"], cfg["host"]).verify_passcode(username, passcode)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if res.get("result") != "allow":
        return {"ok": False, "error": "passcode not accepted", "detail": res}
    return _mint(username, scopes, ttl)
