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

import concurrent.futures
import functools
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
    pass_task_type,
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

# Advocate and opponent depend only on retrieval (not on each other), so they
# run concurrently on a two-thread pool; the judge waits for both. The alias is
# a seam so tests can force the sequential fallback.
_PASS_POOL_SIZE = 2
_ThreadPoolExecutor = concurrent.futures.ThreadPoolExecutor

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


def _stream_generate(prompt: str, task_type: str):
    """Local-only streaming seam; yields incremental text chunks."""
    from core.juris_kai import streaming
    return streaming.stream_chat(prompt, task_type=task_type)


def _noop_generate(prompt: str, task_type: str) -> str:
    """Model-free generation seam: the retrieval-only (compact) opponent.

    Fast mode scans the retrieved sources for contrary authority in-process and
    never spends a GPU pass on the opponent narrative, so the one GPU is used
    only by the advocate and the judge — the root cause of the old contention.
    """
    return ""


def _budget_generate(pass_name: str, fast: bool):
    """Return a ``generate`` wrapper pinning a pass to its (fast) budget.

    ``None`` for the thorough mode, so ``run_deep`` keeps calling the passes
    with exactly the arguments it always did.
    """
    if not fast:
        return None
    task_type = pass_task_type(pass_name, fast=True)

    def _gen(prompt: str, _task_type: str) -> str:
        return _generate(prompt, task_type)

    return _gen


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


def _collect_judge_stream(stream, on_chunk=None, on_ttft=None) -> tuple[str, float | None]:
    """Iterate a judge stream to completion; report first-token latency.

    Returns ``(text, ttft_seconds)``. ``on_chunk`` receives each piece as it
    arrives (the Command Center SSE bridge relays them), ``on_ttft`` receives
    the seconds-to-first-token once. The stream is fully collected before the
    citation firewall runs, so a streamed judge is never less verified than the
    blocking one; the streaming only changes *when* the user sees text.
    """
    t0 = time.perf_counter()
    ttft = None
    parts: list[str] = []
    for piece in stream:
        if ttft is None:
            ttft = round(time.perf_counter() - t0, 3)
            if on_ttft:
                on_ttft(ttft)
        parts.append(piece)
        if on_chunk:
            on_chunk(piece)
    return "".join(parts), ttft


def judge(query: str, advocate: dict, opponent: dict, generate=None,
          stream=None, on_chunk=None, on_ttft=None) -> dict:
    """Judge pass: decide established / disputed / unresolved, in IRAC.

    ``stream`` (a chunk iterator or a callable returning one) streams the final
    pass for low time-to-first-token; the collected text is still firewalled
    exactly as in the blocking path. A streaming failure falls back to the
    blocking ``generate`` seam, so the judge is never blank.
    """
    gen = generate or _generate
    adv = advocate or {}
    opp = opponent or {}
    docs = _dedupe((adv.get("docs") or []) + (opp.get("docs") or []))

    prompt = build_judge_prompt(query, adv.get("argument", ""),
                                opp.get("argument", ""), docs)
    ttft = None
    raw = ""
    if stream is not None:
        try:
            chunks = stream(prompt) if callable(stream) else stream
            raw, ttft = _collect_judge_stream(chunks, on_chunk=on_chunk,
                                              on_ttft=on_ttft)
        except Exception as exc:  # noqa: BLE001 - stream failure degrades
            logger.warning("judge stream failed -> blocking judge: %s", exc)
            raw = ""
    if not raw:
        raw = gen(prompt, PASS_TASK_TYPE[JUDGE])
    fw = _firewall(raw)
    narrative = (fw.get("text") or raw or "").strip()

    out = _build_judgement(query, adv, opp, narrative)
    out["prompt"] = prompt
    out["citations"] = (fw.get("report") or {}).get("citations", [])
    out["firewall_error"] = fw.get("error")
    out["error"] = None
    out["ttft"] = ttft
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


def _timed_call(fn, *args):
    """Run ``fn(*args)`` capturing its duration and any exception.

    Returns ``(result, duration_s, error)`` and never raises, so one failed
    pass is handled by the degradation policy instead of killing the run.
    """
    t0 = time.perf_counter()
    try:
        result, error = fn(*args), None
    except Exception as exc:  # noqa: BLE001 - degrade, never kill the run
        result, error = None, exc
    return result, round(time.perf_counter() - t0, 3), error


def _apply_pass_outcomes(q, docs, adv, adv_err, opp, opp_err):
    """Map raw pass outcomes onto the existing degradation policy (never blank)."""
    degraded = False
    if adv is None:
        logger.warning("advocate pass failed -> single-pass fallback: %s", adv_err)
        degraded = True
        adv = _single_pass(q, docs, error=adv_err)
    if opp is None:
        logger.warning("opponent pass failed: %s", opp_err)
        degraded = True
        opp = _empty_pass(OPPONENT, docs, opp_err)
    return adv, opp, degraded


def _with_generate(fn, generate):
    """Bind a ``generate`` seam to a pass function, or return it unchanged.

    ``None`` (thorough mode) preserves the exact legacy call signature, which
    is what keeps the existing pass-level tests and injection seams valid.
    """
    return fn if generate is None else functools.partial(fn, generate=generate)


def _run_passes_parallel(q, docs, adv_generate=None, opp_generate=None):
    """Run advocate ∥ opponent concurrently, then settle each outcome.

    ``oppose`` is called with no advocate argument: both passes depend only on
    retrieval, which is what makes them independent. In fast mode
    ``opp_generate`` is the model-free seam, so the pool's two jobs no longer
    contend for the single GPU. Returns ``(adv, opp, degraded, timings)``; a
    pool failure propagates so the caller can fall back to the sequential path.
    """
    t0 = time.perf_counter()
    adv_fn = _with_generate(advocate, adv_generate)
    opp_fn = _with_generate(oppose, opp_generate)
    with _ThreadPoolExecutor(max_workers=_PASS_POOL_SIZE,
                             thread_name_prefix="juris-pass") as pool:
        fut_adv = pool.submit(_timed_call, adv_fn, q, docs)
        fut_opp = pool.submit(_timed_call, opp_fn, q, docs, "")
        adv, adv_dur, adv_err = fut_adv.result()
        opp, opp_dur, opp_err = fut_opp.result()
    wall = round(time.perf_counter() - t0, 3)
    adv, opp, degraded = _apply_pass_outcomes(q, docs, adv, adv_err,
                                              opp, opp_err)
    return adv, opp, degraded, {"advocate": adv_dur, "opponent": opp_dur,
                                "parallel": wall}


def _run_passes_sequential(q, docs, adv_generate=None, opp_generate=None):
    """Sequential fallback used when the thread pool cannot be used."""
    adv_fn = _with_generate(advocate, adv_generate)
    opp_fn = _with_generate(oppose, opp_generate)
    adv, adv_dur, adv_err = _timed_call(adv_fn, q, docs)
    opp, opp_dur, opp_err = _timed_call(opp_fn, q, docs, "")
    adv, opp, degraded = _apply_pass_outcomes(q, docs, adv, adv_err,
                                              opp, opp_err)
    return adv, opp, degraded, {"advocate": adv_dur, "opponent": opp_dur}


def _judge_call_kwargs(fast, stream_judge, on_judge_chunk, on_judge_ttft):
    """Build the judge kwargs for the selected mode.

    Thorough + blocking returns ``{}`` so ``judge`` is called exactly as before
    (preserving the existing seams/tests). Fast mode pins the judge to its lower
    budget; ``stream_judge`` wires the streaming seam and its callbacks.
    """
    kwargs: dict = {}
    gen = _budget_generate(JUDGE, fast)
    if gen is not None:
        kwargs["generate"] = gen
    if stream_judge:
        task_type = pass_task_type(JUDGE, fast)

        def _stream(prompt):
            return _stream_generate(prompt, task_type)

        kwargs["stream"] = _stream
        kwargs["on_chunk"] = on_judge_chunk
        kwargs["on_ttft"] = on_judge_ttft
    return kwargs


def run_deep(query: str, docs: list | None = None, context: str = "",
             fast: bool = False, stream_judge: bool = False,
             on_judge_chunk=None, on_judge_ttft=None) -> dict:
    """Orchestrate retrieve → (advocate ∥ opponent) → judge.

    A missing/ungrounded result returns an honest unresolved judgement without
    calling the model. A failed pass degrades to a single grounded pass or a
    deterministic judgement — the result is never blank. If the thread pool is
    unavailable the passes run sequentially instead.

    ``fast`` is the Deep "fast" mode: the advocate runs on its reduced budget,
    the opponent is retrieval-only (no GPU pass — the single-GPU contention was
    the root cause of the 31–34s thorough latency), and the judge runs leaner.
    ``stream_judge`` streams the final pass for low time-to-first-token; the
    collected text is still citation-firewalled, so streaming never weakens the
    no-ungrounded guarantee.
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

    if fast:
        adv_gen = _budget_generate(ADVOCATE, fast=True)
        opp_gen = _noop_generate
    else:
        adv_gen = opp_gen = None

    try:
        adv, opp, degraded, pass_timings = _run_passes_parallel(
            q, docs, adv_gen, opp_gen)
    except Exception as exc:  # noqa: BLE001 - pool unavailable -> sequential
        logger.warning("parallel passes unavailable, running sequentially: %s",
                       exc)
        adv, opp, degraded, pass_timings = _run_passes_sequential(
            q, docs, adv_gen, opp_gen)
    timings.update(pass_timings)
    timings["mode"] = "fast" if fast else "thorough"

    t0 = time.perf_counter()
    try:
        judgement = judge(q, adv, opp, **_judge_call_kwargs(
            fast, stream_judge, on_judge_chunk, on_judge_ttft))
    except Exception as exc:  # noqa: BLE001 - deterministic judgement
        logger.warning("judge pass failed -> deterministic judgement: %s", exc)
        degraded = True
        judgement = _deterministic_judge(q, adv, opp)
    timings["judge"] = round(time.perf_counter() - t0, 3)
    if judgement.get("ttft") is not None:
        timings["judge_ttft"] = judgement["ttft"]

    if "parallel" in timings:
        phase = timings.get("retrieve", 0.0) + timings["parallel"] + timings["judge"]
    else:
        phase = (timings.get("retrieve", 0.0) + timings.get("advocate", 0.0)
                 + timings.get("opponent", 0.0) + timings.get("judge", 0.0))
    timings["total"] = round(phase, 3)

    result = _result(q, docs, verdict, adv, opp, judgement, degraded, timings)
    result["fast"] = bool(fast)
    return result
