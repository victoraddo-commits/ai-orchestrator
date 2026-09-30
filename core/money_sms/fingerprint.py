"""Dedup fingerprint for the money SMS bridge (§15).

HMAC-SHA256 over ``source_id|sender|timestamp|normalized_body`` using the
secrets/money/sms_fingerprint_key vault key — deterministic, immutable, no
plaintext Hungarian-SMS content leaves the operator's infrastructure without a
keyed digest binding it. The secret key never appears in exports or events.
"""

from __future__ import annotations

import hashlib
import hmac
import unicodedata

_KEY_CACHE: dict[str, str] = {}

OTP_BODY_PATTERN_TEXT = "(\\bcode\\b|\\botp\\b|\\bpin\\b|\\bpassword\\b)"


def normalize_body(body: str) -> str:
    """NFKC-fold, collapse whitespace runs, lowercase. Deterministic."""
    text = unicodedata.normalize("NFKC", str(body or ""))
    return " ".join(text.split()).strip().lower()


def fingerprint_for(source_id: str, sender: str, timestamp: str,
                    body: str, *, key: str | None = None) -> str:
    """HMAC-SHA256 digest (hex) with the vault-handled fingerprint key."""
    if key is None:
        from core.money_sms.bridge import fingerprint_key
        key = fingerprint_key()
    msg = "|".join((
        str(source_id or ""), str(sender or ""), str(timestamp or ""),
        normalize_body(body),
    ))
    return hmac.new((key or "").encode("utf-8"), msg.encode("utf-8"),
                    hashlib.sha256).hexdigest()


def looks_like_otp_body(body: str) -> bool:
    """True when the body still looks like it carries a 4-8 digit live code.
    Mirrors akush-core's defense (contracts: otp_redacted=false + OTP-looking
    body => 422). Used bridge-side to skip; OTP bodies never leave CT111."""
    from core.sms.detections import detect_otp
    from core.money_sms.bridge import REDACTION_MARKERS
    text = str(body or "")
    if any(marker in text for marker in REDACTION_MARKERS):
        return True
    return bool(detect_otp(text)) or bool(
        __import__("re").search(r"(\b(?:code|otp|pin)\b)[^0-9]{0,24}\b\d{4,8}\b",
                                text, __import__("re").IGNORECASE))


__all__ = ["normalize_body", "fingerprint_for", "looks_like_otp_body"]
