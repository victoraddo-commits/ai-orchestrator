"""Honest uncertainty classification for Juris Kai propositions (Phase 5 T1).

Every proposition a reasoning pass emits is classified against the retrieved
authorities alone. The rules are deliberately conservative:

* ``settled``      — at least two distinct retrieved authorities agree;
* ``probable``     — exactly one authority (or an authority alongside an
                     unverifiable citation), so the point is plausible but not
                     established;
* ``disputed``     — retrieved authorities conflict (contrary / repealed);
* ``unresolved``   — no supporting authority, or only contrary/repealed
                     authority, was retrieved;
* ``missing_facts``— the proposition depends on facts that were not supplied.

A single source can therefore never yield ``settled`` ("single-source ⇒ at
most probable"), and no authority can never yield an answer ("no authority ⇒
unresolved"). The module is pure and model-free: the caller supplies the
propositions and the retrieved documents, so classification is deterministic
and auditable.
"""
from __future__ import annotations

import re

SETTLED = "settled"
PROBABLE = "probable"
DISPUTED = "disputed"
UNRESOLVED = "unresolved"
MISSING_FACTS = "missing_facts"

STATUSES = (SETTLED, PROBABLE, DISPUTED, UNRESOLVED, MISSING_FACTS)

# Confidence weights used by the judge. Never over-claim: a disputed point is
# near a coin-flip and an unresolved one is almost no signal at all.
STATUS_WEIGHT = {
    SETTLED: 0.9,
    PROBABLE: 0.6,
    DISPUTED: 0.35,
    UNRESOLVED: 0.15,
    MISSING_FACTS: 0.3,
}

_CONTRARY_STANCES = frozenset({"contrary", "opposes", "against", "conflict"})

# Ghanaian authority-citation shapes: "Act 29", "PNDCL 305", "C.I. 7",
# "Article 19". Used only to detect a citation the model invented (one that
# matches no retrieved document).
_AUTHORITY_RE = re.compile(
    r"\b(?:Act|PNDCL|P\.N\.D\.C\.L|NLCD|N\.L\.C\.D|AFRCD|A\.F\.R\.C\.D|"
    r"NRC|N\.R\.C|CI|C\.I|LI|L\.I|EI|E\.I|SMCD|S\.M\.C\.D|"
    r"Article|Art\.?)\s*(?:No\.?\s*)?\d+[A-Za-z]?\b",
    re.IGNORECASE,
)
_MISSING_FACT_RE = re.compile(
    r"\[\s*missing\s+fact[s]?[^\]]*\]|\[\s*fact[s]?\s+unknown[^\]]*\]",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"[a-z0-9]+")


def _norm(text) -> str:
    return " ".join(_WORD_RE.findall(str(text or "").lower()))


def _identity(doc: dict) -> str:
    """Stable display identity for an authority: citation, else title, else id."""
    return str(doc.get("citation") or doc.get("title") or doc.get("id") or "").strip()


def _is_contrary(doc: dict) -> bool:
    """True when a document undermines rather than supports a proposition."""
    stance = str(doc.get("stance") or "").strip().lower()
    if stance in _CONTRARY_STANCES:
        return True
    if doc.get("contrary") is True:
        return True
    return str(doc.get("temporal_status") or "").strip().upper() == "REPEALED"


def _matches(proposition: str, proposition_tokens: set, doc: dict) -> bool:
    """Whether a retrieved document is referenced by a proposition.

    A citation/title that appears verbatim in the proposition is a direct hit;
    otherwise two distinctive (length > 3) shared title tokens count as a
    reference. Deliberately conservative — a weak single-word overlap is not a
    reference.
    """
    pnorm = _norm(proposition)
    for ident in (_identity(doc), doc.get("citation"), doc.get("title")):
        n = _norm(ident)
        if n and n in pnorm:
            return True
    title_tokens = {t for t in _WORD_RE.findall(
        str(doc.get("title") or "").lower()) if len(t) > 3}
    return len(title_tokens & proposition_tokens) >= 2


def _match_docs(proposition: str, docs: list) -> list:
    tokens = set(_WORD_RE.findall(str(proposition or "").lower()))
    return [d for d in docs or [] if _matches(proposition, tokens, d)]


def _unsupported_citations(proposition: str, docs: list) -> list:
    """Citations named in a proposition that no retrieved document backs."""
    doc_norms = {_norm(_identity(d) + " " + str(d.get("citation") or ""))
                 for d in docs or []}
    unsupported = []
    for cite in _AUTHORITY_RE.findall(proposition or ""):
        nc = _norm(cite)
        if not any(nc and (nc in dn or dn in nc) for dn in doc_norms if dn):
            unsupported.append(cite)
    return unsupported


def _distinct_identities(docs: list) -> list:
    out, seen = [], set()
    for doc in docs or []:
        key = _norm(_identity(doc))
        if key and key not in seen:
            seen.add(key)
            out.append(_identity(doc))
    return out


def _normalize_prop(raw):
    """Return ``(text, flags)`` for a string or dict proposition."""
    if isinstance(raw, dict):
        text = str(raw.get("text") or raw.get("proposition") or "").strip()
        flags = {"missing_facts": bool(raw.get("missing_facts"))}
        return text, flags
    return str(raw or "").strip(), {}


def classify(propositions, docs) -> list:
    """Classify each proposition against the retrieved ``docs``.

    Returns ``[{proposition, status, authorities, contrary, reason}]`` where
    ``authorities`` are the distinct supporting citations/titles and
    ``contrary`` the conflicting (or repealed) ones.
    """
    docs = list(docs or [])
    results = []
    for raw in propositions or []:
        prop, flags = _normalize_prop(raw)
        if not prop:
            continue

        matched = _match_docs(prop, docs)
        support = [d for d in matched if not _is_contrary(d)]
        contrary = [d for d in matched if _is_contrary(d)]
        unsupported = _unsupported_citations(prop, docs)
        support_ids = _distinct_identities(support)
        contrary_ids = _distinct_identities(contrary)

        if flags.get("missing_facts") or _MISSING_FACT_RE.search(prop):
            status = MISSING_FACTS
            reason = "depends on facts that were not supplied"
        elif support and contrary:
            status = DISPUTED
            reason = "retrieved authorities conflict (contrary or repealed)"
        elif support:
            if len(support_ids) >= 2 and not unsupported:
                status = SETTLED
                reason = f"{len(support_ids)} distinct authorities agree"
            else:
                status = PROBABLE
                if len(support_ids) <= 1:
                    reason = "single-source — at most probable"
                else:
                    reason = ("authorities agree but an unverifiable citation "
                              "remains")
        elif contrary:
            status = UNRESOLVED
            reason = "only contrary or repealed authority was retrieved"
        else:
            status = UNRESOLVED
            reason = "no supporting authority in the retrieved sources"

        if unsupported and status != MISSING_FACTS:
            reason += "; unverifiable citation(s): " + ", ".join(unsupported)
            if status == SETTLED:
                status = PROBABLE

        results.append({
            "proposition": prop,
            "status": status,
            "authorities": support_ids,
            "contrary": contrary_ids,
            "reason": reason,
        })
    return results


def confidence(results) -> float:
    """Mean status weight, capped low when anything is disputed/unknown."""
    results = list(results or [])
    if not results:
        return 0.0
    weights = [STATUS_WEIGHT.get(r.get("status"), 0.15) for r in results]
    base = sum(weights) / len(weights)
    if any(r.get("status") == DISPUTED for r in results):
        base = min(base, 0.5)
    if any(r.get("status") in (UNRESOLVED, MISSING_FACTS) for r in results):
        base = min(base, 0.45)
    return round(max(0.0, min(base, 0.95)), 2)


def summarize(results) -> dict:
    """Count statuses and derive an overall status + confidence."""
    results = list(results or [])
    counts = {status: 0 for status in STATUSES}
    for r in results:
        if r.get("status") in counts:
            counts[r["status"]] += 1

    if not results:
        overall = UNRESOLVED
    elif counts[DISPUTED]:
        overall = DISPUTED
    elif counts[MISSING_FACTS]:
        overall = MISSING_FACTS
    elif counts[UNRESOLVED] and not (counts[SETTLED] or counts[PROBABLE]):
        overall = UNRESOLVED
    elif counts[PROBABLE] or counts[UNRESOLVED]:
        overall = PROBABLE
    else:
        overall = SETTLED

    return {"counts": counts, "overall": overall,
            "confidence": confidence(results)}
