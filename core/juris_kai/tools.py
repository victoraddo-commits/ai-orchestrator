"""Practice + research tools for Juris Kai (Legal Brain 2.0 Phase 6, Task 4).

Four practitioner tools and the research modes, all with the same guarantees:

* **Authority-only** — every authority comes from retrieval (the legal brain);
  nothing is invented. Tools that have no authority to show say so honestly.
* **Grounded + citation-firewalled** — every rendered output is passed through
  the AgentGuard legal output gate
  (:meth:`core.agentguard.legal_policy.LegalGuard.guard_output`), which runs the
  citation firewall and the injection guard. The gate is injectable for tests.
* **Zero-trust** — :func:`contract_analysis` operates only on the user's
  uploaded document text (extracted in the workspace sandbox); it never touches
  the authoritative corpus, and a user document can never enter it.

Tools:
  * :func:`contract_analysis` — clauses / obligations / risks / termination /
    liabilities from a user-uploaded document.
  * :func:`issue_matrix` — Issue | Law | Authority | Facts | Counterargument |
    Status, from retrieval + the Deep reasoning engine.
  * :func:`legal_chronology` — a dated, source-labelled timeline from
    user-provided facts.
  * :func:`authority_bundle` — structured authorities per issue (retrieval only).
  * :func:`research` — Quick / Deep (existing) + Statute / Case-law modes.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger("juris_kai.tools")

RESEARCH_MODES = ("quick", "deep", "statute", "case_law")

# Enactments / instruments the Statute mode may surface (Bill ≠ law).
STATUTE_TYPES = frozenset({
    "act", "regulation", "order", "instrument", "legislative_instrument",
    "li", "ci", "ei", "constitution", "pndcl", "nlcd", "smcd", "afrcd",
})
# Reported judgments. The authoritative corpus currently holds none; Case-law
# mode says so honestly rather than inventing a case.
CASE_TYPES = frozenset({"judgment", "ruling", "decision", "case", "case_law"})

NO_CASE_CORPUS_REPLY = (
    "⚖️ *Case-law mode*: the authoritative corpus currently holds no reported "
    "judgments, so there is no case-law to ground an answer. I will not invent "
    "cases. Try *Statute* mode, or ask about an Act, LI or the Constitution."
)
NO_STATUTE_REPLY = (
    "⚖️ *Statute mode*: no enactment or instrument in the corpus matched that "
    "query. Try rephrasing, or name a specific Act or LI."
)

DISCLAIMER = "_Advisory only — not legal advice._"


# ---------------------------------------------------------------------------
# Injectable seams (monkeypatched in tests)
# ---------------------------------------------------------------------------

def _retrieve(query, limit=6):
    """Retrieval seam over the legal-brain client."""
    from core import legal_brain_client as lb
    return lb.search(query, limit=limit, mode="hybrid") or []


def _run_deep(query, docs=None):
    """Deep-reasoning seam (three grounded passes)."""
    from core.juris_kai import reasoning
    return reasoning.run_deep(query, docs=docs)


def _generate(prompt, task_type="juris_research"):
    """Local-only generation seam (Quick mode)."""
    from core.ai.ai_router import delegate
    result = delegate(prompt, task_type=task_type, capability="text_task") or {}
    return result.get("response") or ""


def _default_guard():
    from core.agentguard.legal_policy import get_legal_guard
    return get_legal_guard()


def _gate(text, guard, source):
    """Run an output through the AgentGuard legal output gate (fail-closed)."""
    g = guard if guard is not None else _default_guard()
    try:
        result = g.guard_output(text, source=source)
        return result.get("text", text)
    except Exception as exc:  # noqa: BLE001 - never crash a tool on the gate
        logger.warning("tool output gate failed for %s: %s", source, exc)
        from core.legal.injection import SAFE_FALLBACK
        return SAFE_FALLBACK


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _identity(doc):
    from core.juris_kai.uncertainty import _identity as ident
    return ident(doc)


def _doc_types(doc):
    return {str((doc or {}).get(k) or "").strip().lower()
            for k in ("type", "source_type", "authority_level")}


def _authority_line(doc):
    title = (doc.get("title") or "Untitled").strip()
    cite = (doc.get("citation") or "").strip()
    year = doc.get("year")
    status = (doc.get("temporal_status") or "").strip().upper()
    bits = [title]
    if cite and cite != title:
        bits.append(cite)
    if year:
        bits.append(str(year))
    line = " — ".join(bits)
    if status:
        line += f" [{status}]"
    return line


def _authority_dict(doc):
    return {
        "title": (doc.get("title") or "").strip(),
        "citation": (doc.get("citation") or "").strip(),
        "year": doc.get("year"),
        "authority_level": (doc.get("authority_level") or "").strip(),
        "temporal_status": (doc.get("temporal_status") or "").strip(),
        "store_mode": (doc.get("store_mode") or "").strip(),
    }


def _dedupe_docs(docs):
    seen, out = set(), []
    for doc in docs or []:
        ident = _identity(doc)
        key = ident or f"id:{doc.get('id')}"
        if key and key not in seen:
            seen.add(key)
            out.append(doc)
    return out


def _render_authorities(heading, query, docs):
    lines = [f"*{heading}* — _{query}_", ""]
    for i, doc in enumerate(docs, 1):
        lines.append(f"{i}. {_authority_line(doc)}")
    lines += ["", DISCLAIMER]
    return "\n".join(lines)


def _finish(mode, rendered, authorities, guard, source, honest=False):
    return {
        "mode": mode,
        "rendered": _gate(rendered, guard, source),
        "authorities": authorities,
        "honest": honest,
    }


# ---------------------------------------------------------------------------
# Research modes
# ---------------------------------------------------------------------------

def research(query, mode="quick", *, retrieve=None, deep=None, generate=None,
             guard=None):
    """Run a research mode. Returns ``{mode, rendered, authorities, honest}``.

    Quick and Deep delegate to the existing grounded/three-pass engines; Statute
    and Case-law filter retrieval to the relevant authority class and never
    invent a source. Every rendered answer passes the AgentGuard output gate.
    """
    q = (query or "").strip()
    mode = (mode or "quick").strip().lower()
    if mode not in RESEARCH_MODES:
        mode = "quick"
    if not q:
        return {"mode": mode, "rendered": "Usage: provide a legal query.",
                "authorities": [], "honest": True}

    if mode == "deep":
        runner = deep or _run_deep
        try:
            result = runner(q) or {}
        except Exception as exc:  # noqa: BLE001 - honest failure, never blank
            logger.warning("deep research failed: %s", exc)
            return {"mode": "deep", "rendered": "⚠️ Deep research unavailable.",
                    "authorities": [], "honest": True}
        from core.juris_kai import reasoning
        return _finish("deep", reasoning.render_deep(result),
                       result.get("authorities") or [], guard,
                       "juris_tool:research:deep")

    if mode == "case_law":
        docs = _filter(_retrieve_seam(retrieve), q, CASE_TYPES)
        if not docs:
            return _finish("case_law", NO_CASE_CORPUS_REPLY, [], guard,
                           "juris_tool:research:case_law", honest=True)
        return _finish("case_law", _render_authorities("Case-law", q, docs),
                       docs, guard, "juris_tool:research:case_law")

    if mode == "statute":
        docs = _filter(_retrieve_seam(retrieve), q, STATUTE_TYPES)
        if not docs:
            return _finish("statute", NO_STATUTE_REPLY, [], guard,
                           "juris_tool:research:statute", honest=True)
        return _finish("statute",
                       _render_authorities("Statutes & instruments", q, docs),
                       docs, guard, "juris_tool:research:statute")

    # Quick (existing grounded path).
    from core.juris_kai import grounding
    plan = grounding.build_grounded_plan(q)
    if plan["refusal"]:
        return _finish("quick", plan["refusal"], [], guard,
                       "juris_tool:research:quick", honest=True)
    gen = generate or _generate
    try:
        answer = gen(plan["prompt"]) or ""
    except Exception as exc:  # noqa: BLE001 - honest failure
        logger.warning("quick research generation failed: %s", exc)
        answer = ""
    if not answer.strip():
        answer = "Retrieved authorities:\n" + "\n".join(
            f"• {_authority_line(d)}" for d in plan["docs"])
    rendered = plan["banner"] + answer + plan["footer"]
    return _finish("quick", rendered, plan["docs"], guard,
                   "juris_tool:research:quick")


def _retrieve_seam(retrieve):
    return retrieve or _retrieve


def _filter(retrieve, query, wanted):
    try:
        docs = retrieve(query, limit=8) or []
    except Exception as exc:  # noqa: BLE001 - retrieval failure = no authority
        logger.warning("tool retrieval failed for %r: %s", query, exc)
        return []
    return _dedupe_docs([d for d in docs if _doc_types(d) & wanted])


# ---------------------------------------------------------------------------
# Authority Bundle
# ---------------------------------------------------------------------------

def authority_bundle(issues, *, retrieve=None, guard=None):
    """Structured authorities per issue, from retrieval only."""
    if isinstance(issues, str):
        issues = [issues]
    clean = [str(i).strip() for i in (issues or []) if str(i).strip()]
    ret = _retrieve_seam(retrieve)

    bundle, all_auth = [], []
    for issue in clean:
        docs = _dedupe_docs(_safe_retrieve(ret, issue))
        entries = [_authority_dict(d) for d in docs]
        bundle.append({"issue": issue, "authorities": entries})
        all_auth.extend(docs)

    rendered = _render_bundle(clean, bundle)
    gated = _gate(rendered, guard, "juris_tool:authority_bundle")
    return {"bundle": bundle, "rendered": gated, "authorities": all_auth}


def _safe_retrieve(retrieve, query):
    try:
        return retrieve(query, limit=6) or []
    except Exception as exc:  # noqa: BLE001 - retrieval failure = no authority
        logger.warning("authority bundle retrieval failed for %r: %s", query, exc)
        return []


def _render_bundle(issues, bundle):
    lines = ["*Authority Bundle*",
             "_Authorities below are retrieved from the legal database only._",
             ""]
    if not issues:
        lines.append("_No issue provided._")
    for i, entry in enumerate(bundle, 1):
        lines.append(f"*Issue {i}:* {entry['issue']}")
        if entry["authorities"]:
            for j, a in enumerate(entry["authorities"], 1):
                line = a["title"] or "Untitled"
                if a["citation"] and a["citation"] != a["title"]:
                    line += f" — {a['citation']}"
                if a["year"]:
                    line += f" ({a['year']})"
                if a["temporal_status"]:
                    line += f" [{a['temporal_status']}]"
                lines.append(f"  {j}. {line}")
        else:
            lines.append("  _No authority found in the database for this issue._")
        lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Legal Issue Matrix
# ---------------------------------------------------------------------------

def issue_matrix(query, facts="", *, deep=None, guard=None):
    """Issue | Law | Authority | Facts | Counterargument | Status.

    Rows are built from the Deep reasoning judgement (retrieval-grounded); no
    authority is ever invented. If retrieval grounds nothing, the matrix says so.
    """
    q = (query or "").strip()
    if not q:
        return {"rows": [], "rendered": "Usage: provide a legal issue.",
                "authorities": []}
    runner = deep or _run_deep
    try:
        result = runner(q) or {}
    except Exception as exc:  # noqa: BLE001 - honest failure
        logger.warning("issue matrix deep reasoning failed: %s", exc)
        result = {}
    judge = result.get("judge") or {}
    opponent = result.get("opponent") or {}
    index = _doc_index(result)
    contrary = "; ".join(opponent.get("contrary_authorities") or [])

    rows = []
    for label, key in (("ESTABLISHED", "established"),
                       ("DISPUTED", "disputed"),
                       ("UNRESOLVED", "unresolved")):
        for prop in judge.get(key) or []:
            auths = [a for a in (prop.get("authorities") or []) if a]
            rows.append({
                "issue": prop.get("proposition") or q,
                "law": _law_of(auths, index),
                "authority": "; ".join(auths) or "—",
                "facts": facts.strip() or "—",
                "counterargument": contrary or "—",
                "status": (prop.get("status") or label).upper(),
            })
    if not rows:
        rows = [{"issue": q, "law": "—", "authority": "—",
                 "facts": facts.strip() or "—", "counterargument": "—",
                 "status": "NO_AUTHORITY"}]

    rendered = _render_matrix(rows)
    gated = _gate(rendered, guard, "juris_tool:issue_matrix")
    return {"rows": rows, "rendered": gated,
            "authorities": list(index.values())}


def _doc_index(result):
    index = {}
    for doc in (result.get("docs") or []):
        if isinstance(doc, dict):
            index[_identity(doc)] = doc
    return index


def _law_of(auths, index):
    if not auths:
        return "—"
    doc = index.get(auths[0])
    if doc and doc.get("title"):
        return doc["title"]
    return auths[0]


def _render_matrix(rows):
    headers = ("Issue", "Law", "Authority", "Facts", "Counterargument", "Status")
    lines = ["*Legal Issue Matrix*", "",
             "| " + " | ".join(headers) + " |",
             "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        cells = [_cell(r["issue"]), _cell(r["law"]), _cell(r["authority"]),
                 _cell(r["facts"]), _cell(r["counterargument"]),
                 _cell(r["status"])]
        lines.append("| " + " | ".join(cells) + " |")
    lines += ["", DISCLAIMER]
    return "\n".join(lines)


def _cell(value, limit=80):
    text = " ".join(str(value or "—").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


# ---------------------------------------------------------------------------
# Legal Chronology
# ---------------------------------------------------------------------------

_MONTHS = ("january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december")
_MONTH_NUM = {m: i + 1 for i, m in enumerate(_MONTHS)}
_MONTH_RE = "|".join(_MONTHS)

_DATE_RES = (
    re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),
    re.compile(rf"\b(\d{{1,2}})\s+({_MONTH_RE})\.?,?\s+(\d{{4}})\b", re.I),
    re.compile(rf"\b({_MONTH_RE})\.?\s+(\d{{1,2}}),?\s+(\d{{4}})\b", re.I),
    re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"),
)


def _iso(year, month, day):
    try:
        y, m, d = int(year), int(month), int(day)
    except (TypeError, ValueError):
        return None
    if not (1 <= m <= 12 and 1 <= d <= 31 and 1900 <= y <= 2200):
        return None
    return f"{y:04d}-{m:02d}-{d:02d}"


def _sentence_around(text, start, end):
    left = max(text.rfind(".", 0, start), text.rfind("\n", 0, start),
               text.rfind(";", 0, start))
    rights = [p for p in (text.find(".", end), text.find("\n", end),
                          text.find(";", end)) if p != -1]
    right = min(rights) if rights else len(text)
    return " ".join(text[left + 1:right].split())


def extract_events(text):
    """Deterministically extract dated events from user-provided facts."""
    text = text or ""
    events, seen = [], set()
    for pattern in _DATE_RES:
        for match in pattern.finditer(text):
            groups = match.groups()
            if pattern is _DATE_RES[0]:
                iso = _iso(groups[0], groups[1], groups[2])
            elif pattern is _DATE_RES[1]:
                iso = _iso(groups[2], _MONTH_NUM.get(groups[1].lower(), 0),
                           groups[0])
            elif pattern is _DATE_RES[2]:
                iso = _iso(groups[2], _MONTH_NUM.get(groups[0].lower(), 0),
                           groups[1])
            else:
                iso = _iso(groups[2], groups[1], groups[0])
            if not iso:
                continue
            event = _sentence_around(text, match.start(), match.end())
            key = (iso, event)
            if key in seen:
                continue
            seen.add(key)
            events.append({"date": iso, "event": event or match.group(0),
                           "source": "User-provided facts"})
    events.sort(key=lambda e: (e["date"], e["event"]))
    return events


def legal_chronology(facts, *, retrieve=None, guard=None):
    """Litigation-ready timeline (dates + sources) from user-provided facts."""
    events = extract_events(facts or "")
    authorities = []
    if retrieve is not None:
        authorities = _dedupe_docs(_safe_retrieve(retrieve, facts or ""))
    rendered = _render_chronology(events, authorities)
    gated = _gate(rendered, guard, "juris_tool:legal_chronology")
    return {"events": events, "rendered": gated, "authorities": authorities}


def _render_chronology(events, authorities):
    lines = ["*Legal Chronology*",
             "_Built from the facts you provided; each entry cites its source._",
             ""]
    if not events:
        lines.append("_No dates were found in the provided facts._")
    for i, e in enumerate(events, 1):
        lines.append(f"{i}. *{e['date']}* — {e['event']}")
        lines.append(f"   Source: {e['source']}")
    if authorities:
        lines += ["", "*Authorities retrieved*"]
        lines += [f"• {_authority_line(d)}" for d in authorities]
    lines += ["", DISCLAIMER]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Contract Analysis (zero-trust: user document only)
# ---------------------------------------------------------------------------

_CLAUSE_SPLIT_RE = re.compile(
    r"\n\s*\n|(?=^\s*(?:\d+[.)]|\([a-z0-9]\)|[A-Z][.)])\s)", re.M)

_OBLIGATION_TERMS = (
    "shall", "must ", "agrees to", "agree to", "undertakes", "undertake",
    "is obliged", "obliged to", "responsible for", "will provide", "covenant",
)
_RISK_TERMS = (
    "penalty", "penalties", "liquidated damages", "indemnif", "indemnity",
    "liable", "liability", "breach", "damages", "forfeit", "default",
    "without prejudice", "risk",
)
_TERMINATION_TERMS = (
    "terminat", "expiry", "expire", "expiration", "notice period", "renewal",
    "renew", "cancel", "rescind", "notice of",
)
_LIABILITY_TERMS = (
    "liab", "indemnif", "indemnity", "hold harmless", "limitation of liability",
    "limitation of liab", "joint and several",
)

_MIN_CLAUSE_CHARS = 15
_MAX_CLAUSES = 200


def _split_clauses(text):
    parts = _CLAUSE_SPLIT_RE.split(text or "")
    clauses, seen = [], set()
    for part in parts:
        clause = " ".join((part or "").split())
        if len(clause) < _MIN_CLAUSE_CHARS or clause in seen:
            continue
        seen.add(clause)
        clauses.append(clause)
        if len(clauses) >= _MAX_CLAUSES:
            break
    return clauses


def _matches_any(low, terms):
    return any(term in low for term in terms)


def contract_analysis(text, *, title="Uploaded document", guard=None):
    """Analyse a user-uploaded contract: clauses/obligations/risks/etc.

    Operates only on the supplied text (zero-trust workspace extraction); it
    never reads from or writes to the authoritative corpus.
    """
    clauses = _split_clauses(text or "")
    obligations, risks, termination, liabilities = [], [], [], []
    for clause in clauses:
        low = clause.lower()
        if _matches_any(low, _OBLIGATION_TERMS):
            obligations.append(clause)
        if _matches_any(low, _RISK_TERMS):
            risks.append(clause)
        if _matches_any(low, _TERMINATION_TERMS):
            termination.append(clause)
        if _matches_any(low, _LIABILITY_TERMS):
            liabilities.append(clause)

    sections = {
        "obligations": obligations,
        "risks": risks,
        "termination": termination,
        "liabilities": liabilities,
    }
    rendered = _render_contract(title, clauses, sections)
    gated = _gate(rendered, guard, "juris_tool:contract_analysis")
    return {
        "title": title,
        "clauses": clauses,
        "obligations": obligations,
        "risks": risks,
        "termination": termination,
        "liabilities": liabilities,
        "rendered": gated,
        "workspace_only": True,
    }


def _render_contract(title, clauses, sections):
    lines = [f"*Contract Analysis — {title}*",
             "_Analysed in the zero-trust workspace; this document is not added "
             "to the knowledge base._",
             f"Clauses detected: {len(clauses)}", ""]
    labels = (("obligations", "Obligations"),
              ("risks", "Risks"),
              ("termination", "Termination"),
              ("liabilities", "Liabilities"))
    for key, label in labels:
        items = sections.get(key) or []
        lines.append(f"*{label}* ({len(items)})")
        if items:
            lines += [f"• {_cell(item, 160)}" for item in items]
        else:
            lines.append("_None detected._")
        lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines)
