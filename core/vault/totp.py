"""TOTP (RFC 6238) second factor for the Kai Vault (roadmap 27A / §9).

Self-contained, stdlib-only. Provides enrollment secrets, otpauth:// URIs for
authenticator apps, and time-window code verification. Complements the
WebAuthn/FIDO2 + Duo flows in the Unified Vault 2.0 directive.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import struct
import time
from urllib.parse import quote

DIGITS = 6
STEP = 30
WINDOW = 1  # accept +/- 1 step for clock skew


def generate_secret(nbytes: int = 20) -> str:
    """Return a base32 (no padding) enrollment secret."""
    return base64.b32encode(os.urandom(nbytes)).decode().rstrip("=")


def _key(secret_b32: str) -> bytes:
    s = secret_b32.strip().replace(" ", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def _hotp(secret_b32: str, counter: int, digits: int = DIGITS) -> str:
    digest = hmac.new(_key(secret_b32), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def totp_at(secret_b32: str, t: float | None = None, step: int = STEP,
            digits: int = DIGITS) -> str:
    return _hotp(secret_b32, int((t if t is not None else time.time()) // step), digits)


def verify(secret_b32: str, code: str, t: float | None = None, step: int = STEP,
           digits: int = DIGITS, window: int = WINDOW) -> bool:
    """Constant-time-ish check of a code against +/- ``window`` steps."""
    if not code or not code.strip().isdigit():
        return False
    now = int((t if t is not None else time.time()) // step)
    wanted = str(code).strip().zfill(digits)
    for w in range(-window, window + 1):
        if hmac.compare_digest(_hotp(secret_b32, now + w, digits), wanted):
            return True
    return False


def provisioning_uri(secret_b32: str, account: str, issuer: str = "Kai",
                     digits: int = DIGITS, step: int = STEP) -> str:
    label = quote(f"{issuer}:{account}")
    params = f"secret={secret_b32}&issuer={quote(issuer)}&algorithm=SHA1&digits={digits}&period={step}"
    return f"otpauth://totp/{label}?{params}"
