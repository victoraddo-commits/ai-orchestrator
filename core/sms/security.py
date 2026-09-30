"""core.sms security — sender plausibility heuristics.

SMS sender IDs are trivially spoofable, so provenance is judged by *shape*:
short codes (4-6 digits) and well-formed mobile numbers are plausible, while
alphanumeric IDs are flagged as spoofable and compared against a known set of
official sender IDs for lookalikes/typosquats. No network I/O.
"""

from __future__ import annotations

from typing import Iterable, Optional

from core.sms.normalize import sender_kind
from core.sms.schema import SenderKind, SenderValidation


def _digits(raw: str) -> str:
    return "".join(c for c in str(raw or "") if c.isdigit())


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _lookalike(candidate: str, official: str) -> bool:
    a, b = candidate.strip().lower(), official.strip().lower()
    if not a or not b or a == b:
        return False
    if _levenshtein(a, b) <= 2:
        return True
    return a.replace("-", "").replace(" ", "") == b.replace("-", "").replace(" ", "")


def validate_sender(
    from_number: str, official_senders: Optional[Iterable[str]] = None
) -> SenderValidation:
    official = [o for o in (official_senders or []) if o]
    s = str(from_number or "").strip()
    kind = sender_kind(s)
    result = SenderValidation(from_number=s, kind=kind)

    if not s:
        result.suspicious = True
        result.notes.append("missing sender")
        return result

    if kind == SenderKind.ALPHANUMERIC:
        result.notes.append("alphanumeric sender id (spoofable)")
        for o in official:
            if s.lower() == o.lower():
                result.aligned = True
            elif _lookalike(s, o):
                result.suspicious = True
                result.notes.append(f"lookalike sender id: {s!r} ~ {o!r}")
    elif kind == SenderKind.SHORT_CODE:
        result.aligned = True
        result.notes.append("short code sender")
    elif kind == SenderKind.MOBILE:
        digits = _digits(s)
        if 7 <= len(digits) <= 15:
            result.aligned = True
        else:
            result.suspicious = True
            result.notes.append("implausible mobile number length")
    else:
        result.suspicious = True
        result.notes.append("unrecognized sender format")

    return result


__all__ = ["validate_sender"]
