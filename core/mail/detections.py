"""core.mail detections — classification by subject/body patterns.

Pure, deterministic heuristics over sanitized content (never instructions).
"""

from __future__ import annotations

import re

from core.mail.schema import MailClassification

_PATTERNS: list[tuple[MailClassification, tuple[re.Pattern, ...]]] = [
    (MailClassification.VERIFICATION, tuple(re.compile(p, re.I) for p in (
        r"\bverify your (email|address|account)\b",
        r"\bconfirm your (email|address|account)\b",
        r"\bverification (link|email|code)\b",
        r"\bactivate your account\b",
        r"\b(check your (email|inbox))\b",
    ))),
    (MailClassification.PASSWORD_RESET, tuple(re.compile(p, re.I) for p in (
        r"\breset your password\b", r"\bpassword reset\b", r"\bforgot (your )?password\b",
    ))),
    (MailClassification.SECURITY_ALERT, tuple(re.compile(p, re.I) for p in (
        r"\bsecurity alert\b", r"\bsuspicious (sign-?in|login|activity)\b",
        r"\bunauthori[sz]ed (access|sign-?in|login)\b",
    ))),
    (MailClassification.LOGIN_ALERT, tuple(re.compile(p, re.I) for p in (
        r"\bnew sign-?in\b", r"\bnew login\b", r"\bsign-?in from\b", r"\bnew device\b",
    ))),
    (MailClassification.SUSPENSION, tuple(re.compile(p, re.I) for p in (
        r"\baccount (has been )?suspended\b", r"\byour account will be suspended\b",
        r"\baccount (deactivated|disabled)\b",
    ))),
]


def classify(subject: str = "", body_text: str = "", body_html: str = "") -> MailClassification:
    haystack = " \n ".join((subject or "", body_text or "", body_html or ""))
    for classification, patterns in _PATTERNS:
        if any(p.search(haystack) for p in patterns):
            return classification
    return MailClassification.OTHER


__all__ = ["classify"]
