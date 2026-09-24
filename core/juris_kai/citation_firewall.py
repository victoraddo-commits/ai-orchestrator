"""Hallucination firewall for Juris Kai grounded answers (Phase 2 Task 2).

An answer only reaches this module *after* the strict retrieval gate
(:mod:`core.juris_kai.grounding`) has decided it is groundable. The firewall is
additive: it audits the citations inside that answer and removes any that the
legal brain cannot verify, replacing each with a visible marker so the reader
(and the audit trail) can see exactly what was removed.

Verification happens on CT100 (:mod:`core.legal.citations`); here we consume it
through :func:`core.legal_brain_client.verify_citations`. The verifier is
injectable for tests.

Fail-open by design: the strict gate already guaranteed grounding, so a
verifier outage must leave the answer untouched (and be reported in ``error``)
rather than crash a legal reply.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("juris_kai.citation_firewall")

# Keep this string in lock-step with core.legal.citations.UNVERIFIED_MARKER.
UNVERIFIED_MARKER = "[unverified — not found in database]"

_VERIFIED = "VERIFIED"


def _default_verifier(text: str) -> dict:
    from core import legal_brain_client as lb
    return lb.verify_citations(text, record=True)


def _apply_marker(text: str, citations: list, marker: str) -> str:
    """Replace each non-verified citation span with ``marker`` (right-to-left)."""
    edits = sorted(
        ((c.get("start"), c.get("end")) for c in citations
         if c.get("status") != _VERIFIED),
        key=lambda se: se[0] if isinstance(se[0], int) else -1,
        reverse=True)
    for start, end in edits:
        if (isinstance(start, int) and isinstance(end, int)
                and 0 <= start < end <= len(text)):
            text = text[:start] + marker + text[end:]
    return text


def apply_citation_firewall(answer: str, verifier=None, marker: str = UNVERIFIED_MARKER) -> dict:
    """Verify an answer's citations and strip the unverifiable ones.

    Returns ``{text, report, changed, error}``:

    * ``text``    — the answer with every non-``VERIFIED`` citation replaced by
      ``marker`` (unchanged when verification is unavailable).
    * ``report``  — the verifier's raw report (for audit / belief-ledger reuse).
    * ``changed`` — whether any span was rewritten.
    * ``error``   — a human-readable failure string, or ``None`` on success.

    Never raises: an empty answer is returned untouched without calling the
    verifier; a verifier exception or malformed report is reported in ``error``
    and the original text is preserved.
    """
    text = answer or ""
    if not text.strip():
        return {"text": text, "report": {}, "changed": False, "error": None}

    verify = verifier or _default_verifier
    try:
        report = verify(text)
    except Exception as exc:  # noqa: BLE001 - fail open, never break a reply
        logger.warning("citation firewall: verifier failed (fail open): %s", exc)
        return {"text": text, "report": {}, "changed": False,
                "error": f"{type(exc).__name__}: {exc}"}

    if not isinstance(report, dict) or "citations" not in report:
        logger.warning("citation firewall: malformed verifier report (fail open)")
        return {"text": text, "report": {}, "changed": False,
                "error": "malformed verifier report"}

    cleaned = _apply_marker(text, report.get("citations") or [], marker)
    return {"text": cleaned, "report": report, "changed": cleaned != text,
            "error": None}
