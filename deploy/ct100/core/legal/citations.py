"""Citation extraction + verification engine (Legal Brain 2.0, Phase 2 Task 1).

Answers must be auditable: "know where the answer came from". Retrieval gating
(:mod:`core.juris_kai.grounding`) decides whether a model may answer; this
module decides whether the citations *inside* that answer are real, in force,
and actually supported by the text we hold.

The engine is portable and stdlib-only. Verification is driven by an injected
``lookup(ctype, number) -> instrument | None`` so it is fully unit-testable;
:func:`make_lookup` supplies the SQLite-backed implementation the API uses.

Status vocabulary (exactly four, per the Phase 2 spec):

* ``VERIFIED``             — in the corpus, not repealed, and the text supports
  the claim (best-effort significant-token overlap).
* ``EXISTS_NOT_IN_CORPUS`` — a well-formed instrument/case citation with no
  matching document held, so nothing verifies it.
* ``UNVERIFIED``           — cannot be parsed/attributed, or exists but has no
  usable text to judge.
* ``MISMATCH``             — exists but does not support the claim, or is
  repealed/revoked yet presented as authority.

Only ``VERIFIED`` citations survive the firewall untouched.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from typing import Optional

try:  # optional: the sidecar may be absent on older databases
    from core.legal import document_meta as _document_meta
except Exception:  # noqa: BLE001 - engine must import standalone
    _document_meta = None

VERIFIED = "VERIFIED"
EXISTS_NOT_IN_CORPUS = "EXISTS_NOT_IN_CORPUS"
UNVERIFIED = "UNVERIFIED"
MISMATCH = "MISMATCH"

STATUSES = (VERIFIED, EXISTS_NOT_IN_CORPUS, UNVERIFIED, MISMATCH)

UNVERIFIED_MARKER = "[unverified — not found in database]"

# Best-effort support test calibration (see spec §6): a citation is supported
# when at least this many significant tokens of the sentence appear in the
# instrument text, OR this fraction of them does.
MIN_SUPPORT_OVERLAP = 2
MIN_SUPPORT_RATIO = 0.34

# Instrument classes keyed to the citation type; ``article`` and ``glr`` are
# verified against the Constitution / law reports respectively.
INSTRUMENT_TYPES = frozenset(
    {"act", "pndcl", "nrcd", "nlc", "nlcd", "smc", "afrcd",
     "ci", "li", "ei", "article", "glr"})

# Types that name an enacted instrument (a well-formed number that we simply
# do not hold is EXISTS_NOT_IN_CORPUS). ``article`` and ``glr`` also count.
_ABBREV_DISPLAY = {
    "act": "Act", "pndcl": "PNDCL", "nrcd": "NRCD", "nlc": "NLC",
    "nlcd": "NLCD", "smc": "SMC", "afrcd": "AFRCD",
    "ci": "C.I.", "li": "L.I.", "ei": "E.I.", "article": "Article",
}

_SUPPORT_STOPWORDS = frozenset({
    "the", "a", "an", "of", "in", "on", "at", "to", "for", "is", "are",
    "was", "were", "be", "been", "and", "or", "not", "with", "that", "this",
    "it", "its", "by", "from", "as", "but", "if", "so", "all", "any", "can",
    "has", "had", "have", "do", "does", "did", "would", "shall", "should",
    "may", "might", "under", "which", "who", "whom", "how", "when", "where",
    "into", "over", "after", "also", "more", "most", "such", "there", "their",
    "them", "they", "then", "than", "these", "those", "upon", "said", "same",
    "about", "section", "sections", "article", "articles",
})

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]{3,}")

# 4-digit numbers in this range are years, not Act numbers: "Criminal Offences
# Act, 1960 (Act 29)" must yield Act 29, never Act 1960. A leading "No." always
# means an instrument number ("Act No. 798").
_YEAR_LIKE = range(1900, 2100)


def _spaced(letters: str) -> str:
    """Regex for an abbreviation whose letters may be dot/space separated."""
    return r"\s?\.?\s?".join(letters)


# Ordered longest-prefix-first so NLCD beats NLC and NRCD is not split.
_PATTERNS = [
    ("glr", re.compile(r"[\[(](\d{4})[\])]\s*(\d+)\s*GLR\s*(\d+)", re.I)),
    ("afrcd", re.compile(rf"(?<![A-Za-z]){_spaced('AFRCD')}\.?\s*(\d{{1,4}})\b", re.I)),
    ("pndcl", re.compile(rf"(?<![A-Za-z]){_spaced('PNDCL')}\.?\s*(\d{{1,4}})\b", re.I)),
    ("nrcd", re.compile(rf"(?<![A-Za-z]){_spaced('NRCD')}\.?\s*(\d{{1,4}})\b", re.I)),
    ("nlcd", re.compile(rf"(?<![A-Za-z]){_spaced('NLCD')}\.?\s*(\d{{1,4}})\b", re.I)),
    ("nlc", re.compile(rf"(?<![A-Za-z]){_spaced('NLC')}\.?\s*(\d{{1,4}})\b", re.I)),
    ("smc", re.compile(rf"(?<![A-Za-z]){_spaced('SMC')}\.?\s*(\d{{1,4}})\b", re.I)),
    ("ci", re.compile(rf"(?<![A-Za-z]){_spaced('CI')}\.?\s*(\d{{1,4}})\b", re.I)),
    ("li", re.compile(rf"(?<![A-Za-z]){_spaced('LI')}\.?\s*(\d{{1,4}})\b", re.I)),
    ("ei", re.compile(rf"(?<![A-Za-z]){_spaced('EI')}\.?\s*(\d{{1,4}})\b", re.I)),
    ("act", re.compile(r"\bAct[,\s]+(No\.?\s*)?(\d{1,4})\b", re.I)),
    ("article", re.compile(r"\bArticle\s+(\d{1,3})\b", re.I)),
]


def _display(ctype, number, match=None, year=None):
    if ctype == "glr":
        return f"[{year}] {match.group(2)} GLR {match.group(3)}"
    return f"{_ABBREV_DISPLAY.get(ctype, ctype.title())} {number}"


def extract_citations(text: str) -> list[dict]:
    """Every citation occurrence in ``text``, ordered and non-overlapping.

    Each item carries ``{type, number, year, display, raw, start, end}`` so the
    firewall can rewrite the exact span. A bare 4-digit year after "Act" is a
    year, not an Act number, and is skipped (the "No." form is always kept).
    """
    text = text or ""
    found: list[dict] = []
    for ctype, pattern in _PATTERNS:
        for m in pattern.finditer(text):
            if ctype == "glr":
                year = int(m.group(1))
                found.append({
                    "type": ctype, "number": int(m.group(3)), "year": year,
                    "volume": int(m.group(2)),
                    "display": _display(ctype, None, m, year),
                    "raw": m.group(0), "start": m.start(), "end": m.end(),
                })
                continue
            if ctype == "act":
                has_no = bool(m.group(1))
                number = int(m.group(2))
                if not has_no and number in _YEAR_LIKE:
                    continue
            else:
                number = int(m.group(1))
            found.append({
                "type": ctype, "number": number, "year": None,
                "display": _display(ctype, number),
                "raw": m.group(0), "start": m.start(), "end": m.end(),
            })

    # Keep the longest match at each position; never emit overlapping spans.
    found.sort(key=lambda c: (c["start"], -(c["end"] - c["start"])))
    out: list[dict] = []
    for c in found:
        if out and c["start"] < out[-1]["end"]:
            continue
        out.append(c)
    return out


# ── support test ─────────────────────────────────────────────────────────

def _sentence_for(text: str, cit: dict) -> str:
    """The sentence containing ``cit`` (split on . ! ? and newlines)."""
    start = cit["start"]
    left = max((text.rfind(ch, 0, start) for ch in ".!?\n"), default=-1)
    right = min((i for i in (text.find(ch, cit["end"]) for ch in ".!?\n")
                 if i != -1), default=len(text))
    return text[left + 1:right + 1].strip()


def _significant(text: str) -> set[str]:
    return {w.lower() for w in _WORD_RE.findall(text or "")
            if w.lower() not in _SUPPORT_STOPWORDS}


def _supports(sentence: str, support_text: str) -> bool:
    toks = _significant(sentence)
    if not toks or not (support_text or "").strip():
        return False
    low = support_text.lower()
    hits = sum(1 for t in toks if t in low)
    return hits >= MIN_SUPPORT_OVERLAP or hits / len(toks) >= MIN_SUPPORT_RATIO


def _doc_matches(doc: dict, cit: dict) -> bool:
    label = f"{doc.get('citation') or ''} {doc.get('title') or ''}"
    for c in extract_citations(label):
        if c["type"] == cit["type"] and c["number"] == cit["number"]:
            return True
    if cit["type"] == "article":
        return "constitution" in label.lower()
    return False


def _support_text(cit: dict, instrument: dict | None,
                  grounded_docs) -> str:
    """Prefer a retrieved grounding chunk for the instrument, else its body."""
    for d in grounded_docs or []:
        if _doc_matches(d, cit):
            chunk = (d.get("chunk_content") or d.get("snippet")
                     or d.get("content") or "")
            if (chunk or "").strip():
                return chunk
    if instrument:
        return instrument.get("content") or ""
    return ""


def _repealed(instrument: dict | None) -> bool:
    if not instrument:
        return False
    # The temporal engine's verdict (a REPEALS edge / repeal_status) is
    # authoritative when present; the legacy flag scan is the fallback.
    if (instrument.get("temporal_status") or "").upper() == "REPEALED":
        return True
    meta = instrument.get("meta") or {}
    flag = f"{instrument.get('repeal_status') or ''} {meta.get('repeal_status') or ''}"
    status = (instrument.get("status") or "").lower()
    if status in ("repealed", "revoked", "spent", "overruled", "superseded"):
        return True
    return any(w in flag.lower()
               for w in ("repeal", "revoke", "spent", "supersed"))


def _support_status(sentence: str, support_text: str) -> bool | None:
    """True/False when support can be judged, ``None`` when there is no text."""
    if not (support_text or "").strip():
        return None
    return _supports(sentence, support_text)


def verify_citation(cit: dict, sentence: str, lookup,
                    grounded_docs=None) -> dict:
    """Verify one citation; returns ``{...cit, status, reason, support_found}``.

    Evidence priority: a **retrieved grounding chunk** for the cited
    instrument is the strongest signal (the answer was generated from it), so
    it is checked first; the corpus-wide lookup is the fallback for citations
    the model introduced beyond the retrieved set (existence + currency).
    """
    row = dict(cit)
    row["sentence"] = sentence
    row["support_found"] = False

    instrument = None
    lookup_error = None
    if lookup:
        try:
            instrument = lookup(cit["type"], cit["number"])
        except Exception as exc:  # noqa: BLE001 - a lookup failure is UNVERIFIED
            lookup_error = f"{type(exc).__name__}"

    row["temporal_status"] = (instrument or {}).get("temporal_status") or ""

    # 1) A retrieved grounding chunk that names this instrument decides it.
    grounded_text = _support_text(cit, None, grounded_docs)
    if (grounded_text or "").strip():
        row["support_found"] = True
        if not _supports(sentence, grounded_text):
            row["status"] = MISMATCH
            row["reason"] = "cited source does not support the claim"
            return row
        if _repealed(instrument):
            row["status"] = MISMATCH
            row["reason"] = "instrument is repealed/revoked but presented as law"
            return row
        row["status"] = VERIFIED
        row["reason"] = "supported by a retrieved grounding source"
        return row

    # 2) Corpus lookup fallback (citation not grounded in the retrieved set).
    if lookup_error is not None:
        row["status"] = UNVERIFIED
        row["reason"] = f"lookup failed: {lookup_error}"
        return row
    if instrument is None:
        if cit["type"] == "article":
            row["status"] = UNVERIFIED
            row["reason"] = "article not confirmed in the held Constitution text"
        else:
            row["status"] = EXISTS_NOT_IN_CORPUS
            row["reason"] = "no matching document in corpus"
        return row

    support = instrument.get("content") or ""
    supported = _support_status(sentence, support)
    row["support_found"] = bool(support.strip())

    if supported is None:
        row["status"] = UNVERIFIED
        row["reason"] = "instrument held but no text to judge support"
        return row
    if not supported:
        row["status"] = MISMATCH
        row["reason"] = "instrument text does not support the claim"
        return row
    if _repealed(instrument):
        row["status"] = MISMATCH
        row["reason"] = "instrument is repealed/revoked but presented as law"
        return row
    row["status"] = VERIFIED
    row["reason"] = "found in corpus and supports the claim"
    if instrument.get("meta", {}).get("amended_by"):
        row["reason"] += " (instrument amended)"
    return row


def verify_text(text: str, lookup, grounded_docs=None) -> dict:
    """Verify every citation in ``text``; returns a report summarising status."""
    citations = extract_citations(text)
    rows = [verify_citation(c, _sentence_for(text or "", c), lookup,
                            grounded_docs) for c in citations]
    summary = {s: 0 for s in STATUSES}
    for r in rows:
        summary[r["status"]] = summary.get(r["status"], 0) + 1
    return {
        "citations": rows,
        "summary": summary,
        "all_verified": bool(rows) and summary[VERIFIED] == len(rows),
    }


def apply_firewall(text: str, report: dict,
                   marker: str = UNVERIFIED_MARKER) -> str:
    """Replace every non-``VERIFIED`` citation span with ``marker``.

    The rewrite is span-based (right-to-left) so it is exact and does not
    corrupt surrounding prose; a fabricated citation is removed, not merely
    annotated, and its position is left visible as the marker.
    """
    text = text or ""
    edits = sorted(
        ((c["start"], c["end"]) for c in (report or {}).get("citations", [])
         if c.get("status") != VERIFIED),
        reverse=True)
    for start, end in edits:
        if 0 <= start < end <= len(text):
            text = text[:start] + marker + text[end:]
    return text


# ── SQLite corpus lookup ─────────────────────────────────────────────────

_CONFIRM = {ctype: pattern for ctype, pattern in _PATTERNS}

# Which regex group holds the instrument number, per type. ``act`` has an
# optional leading "No." group; ``glr`` ends with the page number.
_NUM_GROUP = {"act": 2, "glr": 3}
for _ctype, _ in _PATTERNS:
    _NUM_GROUP.setdefault(_ctype, 1)


def _confirm_matches(conn, ctype, number, *, limit=50):
    """Return ALL confirmed corpus matches for ``(ctype, number)``.

    Shared by ``make_lookup`` (first) and ``lookup_all`` (all, for
    disambiguation). Ordered best-first: content-rich ``full`` documents ahead
    of rights-limited stubs, then by id. The per-type regex confirms each
    candidate so "Act 992" never matches a 1992 instrument.
    """
    if conn is None or ctype not in INSTRUMENT_TYPES:
        return []
    pattern = _CONFIRM.get(ctype)
    if pattern is None:
        return []
    try:
        if ctype == "article":
            rows = conn.execute(
                "SELECT id, title, citation, status, store_mode "
                "FROM documents "
                "WHERE (lower(title) LIKE '%constitution%' "
                "       OR lower(citation) LIKE '%constitution%') "
                "  AND lower(title) NOT LIKE '%bill%' "
                "  AND lower(title) NOT LIKE '%report%' "
                "ORDER BY (lower(title) LIKE '%1992%') DESC, "
                "         (store_mode = 'full') DESC, id ASC LIMIT 50"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, title, citation, status, store_mode "
                "FROM documents WHERE citation LIKE ? OR title LIKE ? "
                "LIMIT 500",
                (f"%{number}%", f"%{number}%")).fetchall()
    except sqlite3.Error:
        return []

    out = []
    for row in rows:
        doc = dict(row)
        label = f"{doc.get('citation') or ''} {doc.get('title') or ''}"
        if ctype == "article":
            confirmed = ("constitution" in label.lower()
                         and _article_present(conn, doc.get("id"), number))
            window = _constitution_window(conn, doc.get("id"), number)
        else:
            try:
                confirmed = any(
                    int(m.group(_NUM_GROUP[ctype])) == number
                    for m in pattern.finditer(label))
            except (KeyError, IndexError):
                confirmed = bool(pattern.search(label))
            window = _latest_content(conn, doc.get("id"))
        if confirmed:
            doc["content"] = (window or "")[:20000]
            doc["meta"] = _meta_for(conn, doc.get("id"))
            doc["temporal_status"] = _temporal_for(conn, doc.get("id"))
            out.append(doc)

    # Prefer documents that actually hold text; a rights-limited stub must
    # never shadow the real instrument (the Act 992 stub-vs-full bug).
    out.sort(key=lambda d: (
        0 if d.get("store_mode") == "full" else 1,
        -len(d.get("content") or ""),
        d.get("id") or 0))
    return out[:limit]


def make_lookup(conn):
    """Return ``lookup(type, number)`` backed by the corpus, or ``None``.

    Returns the single best confirmed match (content-rich copy first). Use
    :func:`lookup_all` when the caller needs every match for disambiguation.
    """
    if conn is None:
        return None

    def lookup(ctype, number):
        matches = _confirm_matches(conn, ctype, number, limit=1)
        return matches[0] if matches else None

    return lookup


def lookup_all(conn, ctype, number):
    """Return every confirmed corpus match for ``(ctype, number)``.

    Used to detect ambiguity: if two distinct instruments/editions match a
    citation, the caller should ask the user which one they mean rather than
    silently picking or giving up.
    """
    return _confirm_matches(conn, ctype, number, limit=50)


def _latest_content(conn, doc_id) -> str:
    try:
        row = conn.execute(
            "SELECT content FROM document_versions WHERE document_id = ? "
            "ORDER BY version_number DESC LIMIT 1", (doc_id,)).fetchone()
        return (row[0] if row else "") or ""
    except sqlite3.Error:
        return ""


_ARTICLE_QUERY_RE = re.compile(r"^\s*article\s+(\d{1,3})\s*$", re.I)


def parse_article_query(query) -> Optional[int]:
    """Article number when ``query`` is exactly ``Article N``, else ``None``.

    Lets a retriever route a precise article lookup to
    :func:`make_lookup` instead of a lexical BM25 search, which cannot see a
    ``24. TITLE`` heading from the words "Article 24".
    """
    m = _ARTICLE_QUERY_RE.match(query or "")
    return int(m.group(1)) if m else None


def _article_re(number) -> re.Pattern:
    """Regex matching article ``number`` in either printed form.

    The held Constitution text writes article headings in the numbered form
    ``24. ECONOMIC RIGHTS`` (with the operative clause ``(1)`` on the next
    line/after it); the words "Article 24" only ever appear for cross-references.
    A literal-only match therefore never resolves Article 24, while an
    unbounded ``24`` would falsely resolve Article 243. Both forms are accepted,
    each with a digit boundary so ``24`` never matches ``243``/``240``:

    * literal form  — ``Article 24`` (``(?!\\d)`` blocks a longer number);
    * heading form  — a line starting with ``24. TITLE`` followed by ``(1)``,
      which distinguishes an operative article heading from an entry in the
      ARRANGEMENT OF CHAPTERS or a Schedule item.
    """
    n = str(int(number))
    return re.compile(
        rf"\barticle\s+{n}(?!\d)"
        rf"|^[ \t]*{n}[ \t]*\.[ \t]+[A-Z][A-Z0-9 ,.'()\-]{{2,}}"
        rf"\s{{0,6}}\(1\)",
        re.I | re.M)


def _article_match(conn, doc_id, number):
    content = _latest_content(conn, doc_id)
    if not content:
        return None
    return _article_re(number).search(content)


def _article_present(conn, doc_id, number) -> bool:
    return _article_match(conn, doc_id, number) is not None


def _constitution_window(conn, doc_id, number) -> str:
    m = _article_match(conn, doc_id, number)
    if not m:
        return ""
    content = m.string
    start = max(0, m.start() - 200)
    return content[start:start + 4000]


def _meta_for(conn, doc_id):
    if _document_meta is None:
        return {}
    try:
        return _document_meta.get_meta(conn, doc_id) or {}
    except Exception:  # noqa: BLE001 - sidecar optional
        return {}


def _temporal_for(conn, doc_id):
    """Temporal status of a resolved instrument (Phase 3 T3), or ``''``.

    Best-effort: a brain predating the temporal engine simply omits the field
    and the firewall keeps its Phase 2 behaviour.
    """
    try:
        from core.legal import temporal
        return temporal.status(conn, doc_id)["status"]
    except Exception:  # noqa: BLE001 - temporal layer optional
        return ""


# ── belief ledger (Phase 2 Task 3, minimal) ──────────────────────────────

LEDGER_SQL = """\
CREATE TABLE IF NOT EXISTS belief_ledger (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    proposition      TEXT NOT NULL,
    proposition_hash TEXT NOT NULL UNIQUE,
    authorities      TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'verified',
    last_verified    TEXT NOT NULL DEFAULT (datetime('now')),
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _prop_hash(proposition: str) -> str:
    norm = re.sub(r"\s+", " ", (proposition or "").strip().lower())
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()


def ensure_ledger(conn) -> None:
    conn.executescript(LEDGER_SQL)
    conn.commit()


def record_proposition(conn, proposition: str, authorities, status: str = "verified") -> dict:
    """Upsert a verified proposition; idempotent on the proposition text."""
    ensure_ledger(conn)
    if isinstance(authorities, (list, tuple)):
        authorities = "; ".join(str(a) for a in authorities)
    conn.execute(
        "INSERT INTO belief_ledger (proposition, proposition_hash, authorities,"
        " status, last_verified) VALUES (?, ?, ?, ?, datetime('now')) "
        "ON CONFLICT(proposition_hash) DO UPDATE SET "
        "authorities=excluded.authorities, status=excluded.status, "
        "last_verified=datetime('now')",
        (proposition, _prop_hash(proposition), authorities or "", status))
    conn.commit()
    row = conn.execute("SELECT * FROM belief_ledger WHERE proposition_hash = ?",
                       (_prop_hash(proposition),)).fetchone()
    return dict(row) if row else {}


def list_propositions(conn, limit: int = 50) -> list[dict]:
    ensure_ledger(conn)
    try:
        limit = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        limit = 50
    rows = conn.execute(
        "SELECT * FROM belief_ledger ORDER BY last_verified DESC, id DESC "
        "LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]
