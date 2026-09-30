"""core.sms normalize — pure helpers over untrusted message text.

Number normalization, sender classification, matching, and OTP redaction. No
network I/O and no persistence: everything here is deterministic and safe to
run on untrusted input.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional, Union

from core.sms.schema import SenderKind

_REDACTION = "[REDACTED]"
_NON_DIGIT = re.compile(r"\D")
_INTL_PREFIX_US = "011"


def to_e164(number: Union[str, None], default_cc: Optional[Union[str, int]] = None) -> str:
    """Best-effort E.164 canonicalization of a phone number.

    Handles the prefixes real gateways send — ``+``, the international ``00``
    prefix, and the US ``011`` international dialing prefix — and strips
    formatting (spaces, dashes, parens, dots). When *default_cc* is supplied a
    national number with no international prefix is prefixed with that country
    code (idempotently: a number already carrying it is not double-prefixed,
    and a national trunk ``0`` is dropped first).

    Returns ``""`` for empty/None input. Deliberately small and plan-agnostic:
    it canonicalizes the *string* so equal numbers compare equal, without
    validating against country numbering plans.
    """
    raw = str(number).strip() if number is not None else ""
    if not raw:
        return ""
    digits = _NON_DIGIT.sub("", raw)
    if not digits:
        return ""

    intl = raw.startswith("+")
    if not intl and digits.startswith(_INTL_PREFIX_US):
        digits = digits[len(_INTL_PREFIX_US):]
        intl = True
    elif not intl and digits.startswith("00"):
        digits = digits[2:]
        intl = True

    if intl:
        # A country code never begins with 0, so a leftover leading 00 (as in
        # ``+0013805003090``) is a redundant international prefix, not part of
        # the number.
        digits = digits.lstrip("0")
        return f"+{digits}" if digits else ""

    if default_cc is not None:
        cc = _NON_DIGIT.sub("", str(default_cc))
        if cc:
            national = digits.lstrip("0")
            if not national.startswith(cc):
                national = f"{cc}{national}"
            return f"+{national}"
    return f"+{digits}"


def normalize_number(raw: str) -> str:
    """Best-effort E.164-ish normalization; short codes pass through unchanged."""
    if raw is None:
        return ""
    s = re.sub(r"[^\d+]", "", str(raw).strip())
    digits = _NON_DIGIT.sub("", s)
    if not digits:
        return ""
    # Plain national / short tokens keep their digits verbatim (a short code is
    # not an E.164 number); anything with an international prefix is canonical.
    if not s.startswith("+") and not digits.startswith(("00", _INTL_PREFIX_US)):
        return digits
    return to_e164(raw)


def normalize_sender(raw: str) -> str:
    """Normalize a sender: keep alphanumeric sender IDs verbatim, else treat as
    a number (so ``MyBank`` is not stripped to an empty string)."""
    s = str(raw or "").strip()
    if any(c.isalpha() for c in s):
        return s
    return normalize_number(s)


def _digits(raw: str) -> str:
    return _NON_DIGIT.sub("", str(raw or ""))


def sender_kind(raw: str) -> SenderKind:
    s = str(raw or "").strip()
    if not s:
        return SenderKind.UNKNOWN
    if any(c.isalpha() for c in s):
        return SenderKind.ALPHANUMERIC
    digits = _digits(s)
    if 4 <= len(digits) <= 6:
        return SenderKind.SHORT_CODE
    if len(digits) >= 7:
        return SenderKind.MOBILE
    return SenderKind.UNKNOWN


def numbers_match(a: str, b: str) -> bool:
    """True when two numbers refer to the same line (ignoring formatting and a
    national leading zero). Compares the last 9 digits when both qualify."""
    da, db = _digits(a), _digits(b)
    if not da or not db:
        return False
    if len(da) >= 9 and len(db) >= 9:
        return da[-9:] == db[-9:]
    return da.lstrip("0") == db.lstrip("0")


def redact_otp(body: str, codes: Iterable[str]) -> str:
    """Replace every detected code with ``[REDACTED]`` (the value is never kept)."""
    out = str(body or "")
    for code in sorted({c for c in codes if c}, key=len, reverse=True):
        out = out.replace(code, _REDACTION)
    return out


__all__ = [
    "to_e164",
    "normalize_number",
    "normalize_sender",
    "sender_kind",
    "numbers_match",
    "redact_otp",
]
