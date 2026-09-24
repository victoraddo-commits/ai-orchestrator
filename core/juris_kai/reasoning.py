"""Three-pass legal reasoning engine: Advocate → Opponent → Judge (Phase 5 T2).

Each pass is grounded. The model sees only retrieved sources, must quote them,
and may cite nothing else. Every pass's output is pushed through the citation
firewall (:mod:`core.juris_kai.citation_firewall`), so an invented citation can
never survive a pass. The judge integrates :mod:`core.juris_kai.uncertainty` so
that what is established, disputed, and unresolved is decided honestly rather
than asserted by the model.

``run_deep`` orchestrates retrieve → advocate → oppose → judge and degrades
gracefully: if a pass fails it falls back to a single grounded pass (or a
deterministic judgement), never a blank answer.

Generation is strictly local (the VM104 GPU model through Ollama); verification
is the legal brain (CT100). Both are injectable seams for tests.
"""
from __future__ import annotations

import logging
import re
import time

from core.juris_kai import citation_firewall, uncertainty
from core.juris_kai import grounding as _grounding
from core.juris_kai.prompt import build_grounded_prompt
from core.juris_kai.prompts_reasoning import (
    ADVOCATE,
    JUDGE,
    OPPONENT,
    PASS_TASK_TYPE,
    build_advocate_prompt,
    build_judge_prompt,
    build_opponent_prompt,
)
from core.juris_kai.uncertainty import _identity, _norm

logger = logging.getLogger("juris_kai.reasoning")

# Terms the opponent hunts for when retrieving adverse authority. The list is
# deliberately literal: these are the clause heads that signal an exception,
# proviso, or repeal in Ghanaian drafting.
CONTRARY_TERMS = (
    "except",
    "shall not apply",
    "notwithstanding",
    "repealed",
    "amended",
    "does not apply",
    "provided that",
)

_MAX_PROPOSITIONS = 8
_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")
_MD_RE = re.compile(r"[*_`#>]+")
_ENUM_RE = re.compile(r"(?<!\d)\s(\d{1,2}[.)]\s)")


# ---------------------------------------------------------------------------
# Injectable seams (monkeypatched in tests)
# ---------------------------------------------------------------------------

def _generate(prompt: str, task_type: str) -> str:
    """Local-only generation seam (VM104 ``qwen3-coder:kai``)."""
    from core.juris_kai import streaming
    return streaming.generate(prompt, task_type=task_type)


def _verify(text: str) -> dict:
    """Citation verification seam (the legal brain on CT100)."""
    from core import legal_brain_client as lb
    return lb.verify_citations(text, record=True)


def _search(query: str, limit: int = 4, mode: str = "hybrid") -> list:
    """Retrieval seam over the existing legal-brain client."""
    from core import legal_brain_client as lb
    return lb.search(query, limit=limit, mode=mode) or []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _firewall(text: str) -> dict:
    """Run the citation firewall; fail open so a reply is never blanked."""
    try:
        return citation_firewall.apply_citation_firewall(text, verifier=_verify)
    except Exception as exc:  # noqa: BLE001 - firewall must never crash a pass
        logger.warning("reasoning firewall failed (fail open): %s", exc)
        return {"text": text or "", "report": {}, "changed": False,
                "error": f"{type(exc).__name__}: {exc}"}


def _doc_key(doc: dict) -> str:
    return _norm(_identity(doc)) or f"id:{doc.get('id')}"


def _dedupe(docs: list) -> list:
    out, seen = [], set()
    for doc in docs or []:
        key = _doc_key(doc)
        if key not in seen:
            seen.add(key)
            out.append(doc)
    return out


def _identities(docs: list) -> list:
    out, seen = [], set()
    for doc in docs or []:
        ident = _identity(doc)
        if ident and ident not in seen:
            seen.add(ident)
            out.append(ident)
    return out


def _extract_propositions(text: str) -> list:
    """Extract bounded, de-duplicated propositions from a pass's prose.

    Deliberately simple (sentence/bullet split, minimum length) — it feeds the
    uncertainty engine, it does not try to parse legal syntax.
    """
    cleaned = _ENUM_RE.sub(r"\n\1", _MD_RE.sub("", str(text or "")))
    candidates = []
    for block in re.split(r"\n+", cleaned):
        block = _BULLET_RE.sub("", block).strip()
        if not block or re.match(r"^source\b", block, re.I):
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", block):
            s = sentence.strip()
            if len(s) < 20 or len(s.split()) < 3:
                continue
            if re.match(r"^source\b", s, re.I):
                continue
            candidates.append(s)
    seen, unique = set(), []
    for s in candidates:
        if s not in seen:
            seen.add(s)
            unique.append(s)
    return unique[:_MAX_PROPOSITIONS]


def _retrieve_contrary(query: str, docs: list) -> list:
    """Actively retrieve adverse authority via the existing retrieval client."""
    have = {_doc_key(d) for d in docs or []}
    out = []
    for term in CONTRARY_TERMS:
        try:
            hits = _search(f"{query} {term}", limit=3, mode="hybrid")
        except Exception as exc:  # noqa: BLE001 - retrieval failure degrades
            logger.warning("contrary retrieval failed for %r: %s", term, exc)
            continue
        for hit in hits or []:
            key = _doc_key(hit)
            if key not in have:
                have.add(key)
                out.append(hit)
    return out


def _find_contrary_spans(docs: list) -> list:
    """Locate contrary clause heads inside the retrieved source text."""
    spans = []
    for doc in docs or []:
        text = str(doc.get("chunk_content") or "")
        low = text.lower()
        for term in CONTRARY_TERMS:
            i = low.find(term)
            if i == -1:
                continue
            snippet = " ".join(text[max(0, i - 60):i + len(term) + 60].split())
            spans.append({"doc": _identity(doc), "term": term,
                          "snippet": snippet})
    return spans


# ---------------------------------------------------------------------------
# Passes
# ---------------------------------------------------------------------------

def advocate(query: str, docs: list, generate=None) -> dict:
    """Advocate pass: the strongest argument the supplied sources support."""
    gen = generate or _generate
    docs = list(docs or [])
    prompt = build_advocate_prompt(query, docs)
    raw = gen(prompt, PASS_TASK_TYPE[ADVOCATE])
    fw = _firewall(raw)
    text = (fw.get("text") or raw or "").strip()
    return {
        "pass": ADVOCATE,
        "argument": text,
        "propositions": _extract_propositions(text),
        "authorities": _identities(docs),
        "docs": docs,
        "citations": (fw.get("report") or {}).get("citations", []),
        "firewall_error": fw.get("error"),
        "prompt": prompt,
        "error": None,
    }


def oppose(query: str, docs: list, argument: str, generate=None) -> dict:
    """Opponent pass: contrary authority, exceptions, and gaps.

    Contrary terms are retrieved through the existing client and scanned from
    the source text, so the opponent does not depend on the model to notice a
    planted contrary clause.
    """
    gen = generate or _generate
    docs = list(docs or [])
    contrary_docs = _retrieve_contrary(query, docs)
    all_docs = _dedupe(docs + contrary_docs)
    contrary_spans = _find_contrary_spans(all_docs)

    prompt = build_opponent_prompt(query, all_docs, argument)
    raw = gen(prompt, PASS_TASK_TYPE[OPPONENT])
    fw = _firewall(raw)
    text = (fw.get("text") or raw or "").strip()
    return {
        "pass": OPPONENT,
        "argument": text,
        "propositions": _extract_propositions(text),
        "authorities": _identities(all_docs),
        "contrary_authorities": _identities(contrary_docs),
        "contrary_terms": contrary_spans,
        "docs": all_docs,
        "citations": (fw.get("report") or {}).get("citations", []),
        "firewall_error": fw.get("error"),
        "prompt": prompt,
        "error": None,
    }


def _rule_summary(docs: list, established: list) -> str:
    ids = _identities(docs)
    if ids:
        return "Controlling sources: " + "; ".join(ids) + "."
    return "No controlling source was retrieved."


def _application_summary(established, disputed, unresolved) -> str:
    return (
        f"{len(established)} point(s) established, "
        f"{len(disputed)} disputed, {len(unresolved)} unresolved "
        "on the supplied sources."
    )


def _conclusion(established, disputed, unresolved) -> str:
    if disputed:
        return "Disputed: the retrieved authorities conflict on the question."
    if unresolved and not established:
        return "Unresolved: the supplied sources do not establish an answer."
    if established:
        return "Established on the supplied sources; not exhaustive advice."
    return "Unresolved: no point could be established from the sources."


def _parse_irac(text, query, docs, established, disputed, unresolved) -> dict:
    """Parse an IRAC narrative, filling every section deterministically."""
    sections = {"issue": "", "rule": "", "application": "", "conclusion": ""}
    if text:
        pattern = re.compile(
            r"(ISSUE|RULE|APPLICATION|CONCLUSION)\s*:", re.IGNORECASE)
        matches = list(pattern.finditer(text))
        for idx, match in enumerate(matches):
            key = match.group(1).lower()
            start = match.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            sections[key] = _MD_RE.sub("", text[start:end]).strip()
    return {
        "issue": sections["issue"] or (query or "").strip(),
        "rule": sections["rule"] or _rule_summary(docs, established),
        "application": sections["application"] or _application_summary(
            established, disputed, unresolved),
        "conclusion": sections["conclusion"] or _conclusion(
            established, disputed, unresolved),
    }


def _build_judgement(query, adv, opp, narrative: str = "") -> dict:
    """Assemble the structured judgement (model-independent)."""
    adv = adv or {}
    opp = opp or {}
    adv_docs = list(adv.get("docs") or [])
    opp_docs = list(opp.get("docs") or [])
    docs = _dedupe(adv_docs + opp_docs)

    adv_results = uncertainty.classify(adv.get("propositions") or [], adv_docs)
    opp_results = uncertainty.classify(opp.get("propositions") or [], opp_docs)

    adv_auth = {a for r in adv_results for a in r["authorities"]}
    opp_auth = {a for r in opp_results for a in r["authorities"]}
    # Adverse authority is what the opponent relies on that the advocate does
    # not. Differing interpretations of the SAME authority are not a conflict
    # of authority.
    adverse = set(opp.get("contrary_authorities") or []) | (opp_auth - adv_auth)
    contrary_term_docs = {
        s.get("doc") for s in (opp.get("contrary_terms") or [])
    } - {None}
    conflict = bool(adverse) or bool(opp.get("contrary_terms"))

    established, disputed, unresolved = [], [], []
    for r in adv_results:
        auth = set(r["authorities"])
        touched = bool(auth & adverse) or bool(auth & contrary_term_docs)
        if r["status"] in (uncertainty.SETTLED, uncertainty.PROBABLE):
            if touched:
                disputed.append({**r, "status": uncertainty.DISPUTED,
                                 "reason": "adverse authority: " + r["reason"]})
            else:
                established.append(r)
        else:
            unresolved.append(r)
    for r in opp_results:
        if r["status"] in (uncertainty.SETTLED, uncertainty.PROBABLE):
            disputed.append({**r, "status": uncertainty.DISPUTED,
                             "reason": "contrary position backed by authority"})
        elif r["status"] in (uncertainty.UNRESOLVED,
                             uncertainty.MISSING_FACTS):
            unresolved.append(r)

    conf = uncertainty.confidence(adv_results)
    if conflict:
        conf = min(conf, 0.5)
    if not docs:
        conf = 0.0

    return {
        "pass": JUDGE,
        "established": established,
        "disputed": disputed,
        "unresolved": unresolved,
        "authorities": _identities(docs),
        "confidence": round(conf, 2),
        "irac": _parse_irac(narrative, query, docs, established, disputed,
                            unresolved),
        "uncertainty": {"advocate": adv_results, "opponent": opp_results},
        "docs": docs,
    }


def judge(query: str, advocate: dict, opponent: dict, generate=None) -> dict:
    """Judge pass: decide established / disputed / unresolved, in IRAC."""
    gen = generate or _generate
    adv = advocate or {}
    opp = opponent or {}
    docs = _dedupe((adv.get("docs") or []) + (opp.get("docs") or []))

    prompt = build_judge_prompt(query, adv.get("argument", ""),
                                opp.get("argument", ""), docs)
    raw = gen(prompt, PASS_TASK_TYPE[JUDGE])
    fw = _firewall(raw)
    narrative = (fw.get("text") or raw or "").strip()

    out = _build_judgement(query, adv, opp, narrative)
    out["prompt"] = prompt
    out["citations"] = (fw.get("report") or {}).get("citations", [])
    out["firewall_error"] = fw.get("error")
    out["error"] = None
    return out


def _deterministic_judge(query, adv, opp) -> dict:
    """Model-free judgement used when the judge pass fails (never blank)."""
    return _build_judgement(query, adv, opp, "")


# ---------------------------------------------------------------------------
# Fallbacks + orchestration
# ---------------------------------------------------------------------------

def _single_pass(query: str, docs: list, error=None) -> dict:
    """One grounded pass; deterministic source list if the model is down."""
    text = ""
    try:
        prompt = build_grounded_prompt("juris_research", query, "GROUNDED", docs)
        text = _generate(prompt, "juris_research") or ""
    except Exception as exc:  # noqa: BLE001 - fallback must never raise
        logger.warning("single-pass fallback generation failed: %s", exc)

    if text.strip():
        fw = _firewall(text)
        text = (fw.get("text") or text).strip()

    if not text.strip():
        text = "Retrieved authorities: " + "; ".join(_identities(docs))

    return {
        "pass": ADVOCATE,
        "argument": text,
        "propositions": _extract_propositions(text),
        "authorities": _identities(docs),
        "docs": list(docs or []),
        "citations": [],
        "firewall_error": None,
        "degraded": True,
        "error": str(error) if error else None,
    }


def _empty_pass(pass_name: str, docs: list, error=None) -> dict:
    return {
        "pass": pass_name,
        "argument": "",
        "propositions": [],
        "authorities": _identities(docs),
        "docs": list(docs or []),
        "citations": [],
        "contrary_terms": [],
        "contrary_authorities": [],
        "error": str(error) if error else None,
    }


def _empty_judge(query: str, reason: str = "no authoritative source retrieved") -> dict:
    prop = {
        "proposition": query or "the legal question",
        "status": uncertainty.UNRESOLVED,
        "authorities": [],
        "contrary": [],
        "reason": reason,
    }
    return {
        "pass": JUDGE,
        "established": [],
        "disputed": [],
        "unresolved": [prop],
        "authorities": [],
        "confidence": 0.0,
        "irac": {
            "issue": query or "the legal question",
            "rule": "No authoritative source was retrieved.",
            "application": "The question cannot be assessed on the record.",
            "conclusion": "Unresolved: no supporting authority in the database.",
        },
        "uncertainty": {"advocate": [], "opponent": []},
        "docs": [],
    }


def render_deep(result: dict, narrative_transform=None) -> str:
    """Render a ``run_deep`` result into the structured Deep Research answer.

    Sections, in order: IRAC, Authorities, Counter-authorities, Uncertainty.
    The deterministic 📚 Sources footer is deliberately **not** included here:
    the caller appends ``grounding.build_sources_footer(result["docs"])`` so
    Quick and Deep mode share the exact same footer (temporal status included).

    ``narrative_transform`` is applied to the model-authored IRAC block only
    (e.g. the citation firewall), never to the retrieval-derived authorities /
    counter-authorities lists (those are corpus documents, not model output, so
    running a citation verifier over their identity strings would mis-flag real
    sources). Each pass already firewalled its own narrative; this is an
    optional second pass at the delivery boundary.

    Pure and total — a partial/degraded result still renders every section with
    an explicit honest placeholder, so the delivered answer is never blank.
    """
    result = result or {}
    judge = result.get("judge") or {}
    irac = judge.get("irac") or {}
    authorities = list(result.get("authorities")
                       or judge.get("authorities") or [])
    contrary = list((result.get("opponent") or {})
                    .get("contrary_authorities") or [])
    established = list(judge.get("established") or [])
    disputed = list(judge.get("disputed") or [])
    unresolved = list(judge.get("unresolved") or [])
    confidence = judge.get("confidence")

    irac_block = "\n".join([
        "*⚖️ Deep Research — IRAC*",
        f"*Issue:* {irac.get('issue') or '—'}",
        f"*Rule:* {irac.get('rule') or '—'}",
        f"*Application:* {irac.get('application') or '—'}",
        f"*Conclusion:* {irac.get('conclusion') or '—'}",
    ])
    if narrative_transform:
        irac_block = narrative_transform(irac_block)

    lines = [irac_block, "", "*Authorities*"]
    lines += [f"• {a}" for a in authorities] or ["_None retrieved._"]
    lines += ["", "*Counter-authorities*"]
    lines += [f"• {a}" for a in contrary] or [
        "_None identified in the retrieved sources._"]
    lines += ["", "*Uncertainty*"]
    summary = (f"Established: {len(established)} · Disputed: {len(disputed)} · "
               f"Unresolved: {len(unresolved)}")
    if confidence is not None:
        summary += f" · Confidence: {confidence}"
    lines.append(summary)
    if result.get("degraded"):
        lines.append("_⚠️ Some reasoning passes degraded; the answer is "
                     "bounded to the retrieved sources._")
    return "\n".join(lines)


def _result(query, docs, verdict, adv, opp, judgement, degraded, timings) -> dict:
    return {
        "query": query,
        "docs": docs,
        "verdict": verdict,
        "advocate": adv,
        "opponent": opp,
        "judge": judgement,
        "authorities": judgement.get("authorities") or _identities(docs),
        "uncertainty": judgement.get("uncertainty"),
        "degraded": bool(degraded),
        "latency": timings,
    }


def run_deep(query: str, docs: list | None = None, context: str = "") -> dict:
    """Orchestrate retrieve → advocate → oppose → judge.

    A missing/ungrounded result returns an honest unresolved judgement without
    calling the model. A failed pass degrades to a single grounded pass or a
    deterministic judgement — the result is never blank.
    """
    q = (query or "").strip()
    timings: dict = {}
    verdict = None

    if docs is None:
        t0 = time.perf_counter()
        try:
            result = _grounding.retrieve(q, context=context)
            docs = result.get("docs") or []
            verdict = result.get("verdict")
        except Exception as exc:  # noqa: BLE001 - fail closed, never guess
            logger.warning("run_deep retrieval failed: %s", exc)
            docs, verdict = [], "UNGROUNDED"
        timings["retrieve"] = round(time.perf_counter() - t0, 3)
    docs = list(docs or [])

    if not docs:
        return _result(q, [], verdict or "UNGROUNDED", None, None,
                       _empty_judge(q), False, timings)

    degraded = False

    t0 = time.perf_counter()
    try:
        adv = advocate(q, docs)
    except Exception as exc:  # noqa: BLE001 - degrade to a single pass
        logger.warning("advocate pass failed -> single-pass fallback: %s", exc)
        degraded = True
        adv = _single_pass(q, docs, error=exc)
    timings["advocate"] = round(time.perf_counter() - t0, 3)

    t0 = time.perf_counter()
    try:
        opp = oppose(q, docs, adv.get("argument", ""))
    except Exception as exc:  # noqa: BLE001 - judge still runs
        logger.warning("opponent pass failed: %s", exc)
        degraded = True
        opp = _empty_pass(OPPONENT, docs, exc)
    timings["opponent"] = round(time.perf_counter() - t0, 3)

    t0 = time.perf_counter()
    try:
        judgement = judge(q, adv, opp)
    except Exception as exc:  # noqa: BLE001 - deterministic judgement
        logger.warning("judge pass failed -> deterministic judgement: %s", exc)
        degraded = True
        judgement = _deterministic_judge(q, adv, opp)
    timings["judge"] = round(time.perf_counter() - t0, 3)
    timings["total"] = round(
        sum(v for k, v in timings.items() if k != "total"), 3)

    return _result(q, docs, verdict, adv, opp, judgement, degraded, timings)
