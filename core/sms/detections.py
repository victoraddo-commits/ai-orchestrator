"""core.sms detections — pure, deterministic heuristics over message text.

OTP detection is deliberately conservative (keyword-anchored digit runs) so a
reference number or amount is not mistaken for a one-time code. Classification
mirrors core.mail: OTP / security-alert / suspicious / promotional / other.
"""

from __future__ import annotations

import re

from core.sms.schema import SmsClassification

_OTP_PATTERNS: tuple[re.Pattern, ...] = (
    # "<keyword> ... <digits>"  (verification code is 482913 / OTP: 556677 / code 9012)
    re.compile(
        r"\b(?:otp|one[-\s]?time(?:\s+(?:code|password|passcode))?|passcode|pin|"
        r"verification\s+code|security\s+code|access\s+code|code)\b"
        r"[^0-9\n]{0,24}?(\d{4,8})\b",
        re.IGNORECASE,
    ),
    # "<digits> ... <keyword>"  (483920 is your OTP)
    re.compile(r"\b(\d{4,8})\b[^\n]{0,24}?\b(?:otp|code|passcode|pin)\b", re.IGNORECASE),
    # "G-754321 is your Google verification code"
    re.compile(r"\b[A-Za-z]{0,3}-?(\d{4,8})\s+is\s+your\b", re.IGNORECASE),
)

_SECURITY_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"\bsecurity alert\b", re.IGNORECASE),
    re.compile(r"\bsuspicious (?:sign-?in|login|activity)\b", re.IGNORECASE),
    re.compile(r"\bunauthori[sz]ed (?:access|login|sign-?in)\b", re.IGNORECASE),
)

_SUSPICIOUS_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"\byou(?:'ve| have)?\s+won\b", re.IGNORECASE),
    re.compile(r"\bclaim your (?:prize|reward|gift)\b", re.IGNORECASE),
    re.compile(r"\b(?:prize|lottery|jackpot)\b", re.IGNORECASE),
    re.compile(r"\bfree gift\b", re.IGNORECASE),
    re.compile(r"\bclick this link\b", re.IGNORECASE),
)

_PROMO_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"\b\d{1,3}\s*%\s*off\b", re.IGNORECASE),
    re.compile(r"\b(?:sale|discount|promo(?:tion)?|offer)\b", re.IGNORECASE),
    re.compile(r"\bshop now\b", re.IGNORECASE),
    re.compile(r"\bbuy now\b", re.IGNORECASE),
)


def detect_otp(body: str) -> list[str]:
    """Return unique OTP-looking codes found in *body*, in order of appearance."""
    text = str(body or "")
    found: list[str] = []
    for pattern in _OTP_PATTERNS:
        for match in pattern.finditer(text):
            code = match.group(1)
            if code and code not in found:
                found.append(code)
    return found


def has_otp(body: str) -> bool:
    return bool(detect_otp(body))


def classify(body: str) -> SmsClassification:
    text = str(body or "")
    if detect_otp(text):
        return SmsClassification.OTP
    if any(p.search(text) for p in _SECURITY_PATTERNS):
        return SmsClassification.SECURITY_ALERT
    if any(p.search(text) for p in _SUSPICIOUS_PATTERNS):
        return SmsClassification.SUSPICIOUS
    if any(p.search(text) for p in _PROMO_PATTERNS):
        return SmsClassification.PROMOTIONAL
    return SmsClassification.OTHER


__all__ = ["detect_otp", "has_otp", "classify"]
