"""Progressive retrieval + grounding verdict for Juris Kai.

Retrieval supplies the verdict and citations; the model may only cite what
retrieval returned. No legal substance without a source (owner directive).

Query normalization lives on the legal-brain (CT100) side, so this client
drives retrieval by passing a ``mode`` — it never builds FTS operators itself.
The primary mode is ``hybrid`` (authority-aware BM25 + dense + RRF); the
staged modes (phrase/and/or/like) are the fallback when hybrid is unavailable
or inconclusive. The client reduces the query to its significant tokens first
(see ``significant_tokens``): the server keeps stopwords for the ``phrase``
stage, so stripping them here is a deliberate client-side choice, not server
normalization.
"""
from __future__ import annotations
import logging
import os
import re
import threading

from core.juris_kai.legal_context import MAX_CHUNK_LENGTH, _guard_chunks

logger = logging.getLogger("juris_kai.grounding")

MIN_SOURCE_CHARS = 400
# Rights-limited tiers (reference/search_only) store only a relevance-centered
# snippet, capped by the legal-brain's rights compliance. Requiring the full
# MIN_SOURCE_CHARS there would make them ungroundable (e.g. the Constitution),
# so they get a snippet floor that still rejects trivial stubs.
MIN_SNIPPET_CHARS = 120

# Pre-model jurisdiction check (see is_out_of_scope). The answer-generating
# prompt no longer embeds a refusal sentence, so out-of-scope questions are
# caught here before the model is called. Kept deliberately narrow: only an
# explicit non-Ghana jurisdiction named in a question that does NOT also
# reference Ghana is out of scope.
_FOREIGN_JURISDICTION_RE = re.compile(
    r"(?:\b(?:nigeria|nigerian|kenya|kenyan|"
    r"south\s+africa|south\s+african|"
    r"united\s+kingdom|uk|britain|british|england|"
    r"united\s+states|usa|america|american|"
    r"canada|canadian|australia|australian|"
    r"india|indian|china|chinese|"
    r"france|french|germany|german|"
    r"european\s+union)\b"
    r"|\bu\.s\.)",
    re.IGNORECASE,
)
_GHANA_RE = re.compile(r"\bghana(?:ian)?\b", re.IGNORECASE)


def is_out_of_scope(text: str) -> bool:
    """True when a question is clearly about a non-Ghana jurisdiction.

    Lightweight pre-model jurisdiction check. The answer prompt must not prime
    the model with a refusal sentence, so out-of-scope questions are refused
    here instead. A foreign jurisdiction is out of scope only when the question
    does NOT also reference Ghana: "Can a Nigerian citizen own land in Ghana?"
    is a Ghana-law question and must reach retrieval. Intentionally
    conservative — it catches explicit "law of country X" asks, not every
    passing foreign mention.
    """
    q = (text or "").strip()
    if not q:
        return False
    if _GHANA_RE.search(q):
        return False
    return bool(_FOREIGN_JURISDICTION_RE.search(q))


def _search(query: str, limit: int = 3, mode: str = "or",
            commercial: bool = False) -> list[dict]:
    """Pure transport seam over the legal-brain client (monkeypatched in tests).

    The non-commercial path keeps the legacy call shape (no ``commercial``
    kwarg), so the foundation arm's behaviour is byte-for-byte unchanged.
    """
    from core import legal_brain_client as lb
    if commercial:
        return lb.search(query, limit=limit, mode=mode, commercial=True) or []
    return lb.search(query, limit=limit, mode=mode) or []


# Generic jurisdiction/qualifier tokens carry no discriminating power for a
# Ghana-only corpus: "ghana"/"ghanaian" appear in nearly every document (the
# whole corpus is Ghanaian law) and "law"/"legal" appear in most titles. Left
# in, they let the stage-3 OR match a nonsense query ("Ghana xylophone zzz ...")
# on "ghana" alone, so a menu handler that synthesises "Ghana <topic>" could
# never be UNGROUNDED. They are stripped before retrieval.
#
# Deliberately NOT stripped: "act" (Act 29, Act 1034), "court", "section",
# "bill", "constitution", case names and years -- these identify a source and
# must keep their discriminating power.
_GENERIC_TOKENS = frozenset({"ghana", "ghanaian", "law", "legal"})

# Function words and anaphora carry no retrieval signal. Mirrors the
# legal-brain's own stopword list (core/legal/query_normalize.py) and adds the
# words that continue a turn rather than name a topic ("and", "more", "tell").
# Used to *measure* how substantive a query is and to extract prior-turn topic
# tokens -- the legal-brain already drops its own stopwords for AND/OR, so this
# mirrors rather than replaces server-side normalisation.
#
# Legal terms of art are deliberately NOT stopwords: "will" (a testament),
# "act", "court", "right", "trust", "party", "estate" and the like name a
# source or a legal concept and must keep their discriminating power. The modal
# "may"/"shall"/"must" are ambiguous but function-word-like and stay.
_STOPWORDS = frozenset({
    "the", "a", "an", "of", "in", "on", "at", "to", "for", "is", "are",
    "was", "were", "be", "been", "and", "or", "not", "with", "that", "this",
    "it", "its", "by", "from", "as", "but", "if", "so", "all", "any", "can",
    "has", "had", "have", "do", "does", "did", "would", "shall",
    "should", "may", "might", "i", "you", "he", "she", "we", "they", "me",
    "my", "what", "which", "who", "whom", "how", "when", "where", "about",
    "into", "over", "after", "under",
    "why", "then", "also", "more", "most", "really", "please", "tell",
    "explain", "elaborate", "continue", "give", "show", "want", "need",
    "know", "say", "said", "us", "them", "their", "his", "her", "our",
    "your", "no", "yes",
})
_NON_SUBSTANTIVE = _GENERIC_TOKENS | _STOPWORDS

_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)

# Follow-up expansion: a follow-up only borrows prior-turn context when it is
# genuinely anaphoric -- it has no significant tokens of its own, or it opens
# with an explicit continuation marker. The number of borrowed tokens is capped
# by the query's own significant-token count (never more than
# ``_FOLLOWUP_MAX_CONTEXT_TOKENS``), so context can never swamp a fresh topic.
_FOLLOWUP_ANAPHORIC_RE = re.compile(
    r"^\s*(?:and\b|what about\b|how about\b|why\b|then\b|also\b|more\b|"
    r"elaborate\b|continue\b)",
    re.IGNORECASE,
)
_FOLLOWUP_MAX_CONTEXT_TOKENS = 6


def significant_tokens(text: str) -> list[str]:
    """Ordered, de-duplicated tokens that carry retrieval signal.

    Drops the generic jurisdiction/qualifier tokens (``_GENERIC_TOKENS``) and
    function words (``_STOPWORDS``); single characters are kept (the legal-brain
    already drops them for the AND/OR modes, and the phrase stage may match
    them). This is the token set the staged retrieval searches on; an empty
    result means the query is too vague to ground.
    """
    seen: set[str] = set()
    out: list[str] = []
    for tok in _WORD_RE.findall((text or "").lower()):
        if tok in _NON_SUBSTANTIVE or tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


def _last_user_tokens(context: str) -> list[str]:
    """Substantive tokens from the most recent prior *user* turn in context.

    Returns ``[]`` when no user turn is present: tokenising a whole context
    blob (which may be dominated by the assistant's answer) would inject noise.
    """
    for line in reversed((context or "").splitlines()):
        match = re.match(r"\s*user\s*:\s*(.*)", line, re.IGNORECASE)
        if match:
            return significant_tokens(match.group(1))
    return []


def _is_anaphoric(query: str, query_tokens: list[str]) -> bool:
    """True when the query cannot stand on its own and continues the prior turn.

    Either it has no significant tokens at all ("and for that?"), or it opens
    with an explicit continuation marker ("and the penalty?", "why?"). A fresh
    single topic word ("bail", "theft") is NOT anaphoric and keeps its own
    retrieval.
    """
    if not query_tokens:
        return True
    return bool(_FOLLOWUP_ANAPHORIC_RE.match((query or "").strip()))


def _expand_followup(query: str, context: str) -> str:
    """Prepend prior-turn topic tokens to a genuinely anaphoric follow-up.

    A self-contained question is never steered by the prior turn. For an
    anaphoric follow-up the borrowed context is capped by the query's own
    significant-token count (with a floor of one for a tokenless "why?"), so a
    1-token follow-up borrows at most 1 prior token rather than swamping the
    query 6:1. The result is de-duplicated and order-stable.
    """
    if not context:
        return query
    query_tokens = significant_tokens(query)
    if not _is_anaphoric(query, query_tokens):
        return query
    cap = min(_FOLLOWUP_MAX_CONTEXT_TOKENS, max(1, len(query_tokens)))
    prior = _last_user_tokens(context)[:cap]
    if not prior:
        return query
    merged: list[str] = []
    for tok in prior + query_tokens:
        if tok not in merged:
            merged.append(tok)
    return " ".join(merged)


def _relevance_window(content: str, query: str, width: int) -> str:
    """Return a ``width``-char window of ``content`` around the first query hit.

    A full-tier document's stored body can be large (an Act's front matter, a
    long judgment); truncating from the head can miss the very section the query
    is about (e.g. "rape" deep in the Criminal Offences Act). When a query token
    occurs in the body, centre the window on its first occurrence (with a small
    lead-in so the hit is visible); otherwise fall back to the head.
    """
    text = content or ""
    if not text:
        return ""
    tokens = significant_tokens(query)
    if not tokens:
        return text[:width]
    low = text.lower()
    pos = -1
    for tok in tokens:
        i = low.find(tok)
        if i != -1 and (pos == -1 or i < pos):
            pos = i
    if pos == -1:
        return text[:width]
    lead = min(pos, width // 4)
    start = pos - lead
    return text[start:start + width]


def _hydrate(hit: dict, query: str = "", commercial: bool = False) -> dict:
    """Return ``hit`` with a content-bearing ``chunk_content``, capped.

    CT100's ``/search`` returns a relevance-centered bounded ``snippet``. That
    snippet is the best available text for ``reference``/``search_only``
    records — their stored ``content`` is an arbitrary head truncation (or
    empty) and would be worse than the snippet — so only ``full``-tier records
    are hydrated from ``/document/{id}``. The full body is windowed around the
    query (``_relevance_window``) so a large Act does not lose the relevant
    section to a head truncation. Every result is capped at
    ``legal_context.MAX_CHUNK_LENGTH`` so prompts stay bounded.
    """
    content = (hit.get("chunk_content") or hit.get("snippet")
               or hit.get("content") or "")
    if hit.get("store_mode") == "full" and len(content.strip()) < MIN_SOURCE_CHARS:
        doc_id = hit.get("id")
        if doc_id is not None:
            try:
                from core import legal_brain_client as lb
                doc = (lb.get_document(doc_id, commercial=True) if commercial
                       else lb.get_document(doc_id))
                full = (doc or {}).get("content") or ""
                if len(full.strip()) > len(content.strip()):
                    content = _relevance_window(full, query, MAX_CHUNK_LENGTH)
            except Exception as exc:  # noqa: BLE001 - retrieval must never crash
                logger.warning(
                    "grounding: full-document fetch failed for id=%s: %s",
                    doc_id, exc)
    return {**hit, "chunk_content": content[:MAX_CHUNK_LENGTH]}


def _stage(query: str, limit: int, mode: str,
           commercial: bool = False) -> list[dict]:
    """Run one retrieval stage: transport, hydrate, guard, then keep usable.

    The injection guard runs *after* hydration so it scans the exact text that
    would enter the prompt -- including full-document content fetched from the
    legal-brain. A suspected chunk is replaced with ``WITHHELD`` (and flagged
    ``injection_suspected``); that sentinel is shorter than every usability
    floor, so ``_usable`` drops it and it cannot ground an answer.

    ``commercial`` is threaded through both the search and the full-document
    hydration so a commercial request never pulls a non-commercial document.
    """
    hits = (_search(query, limit, mode=mode, commercial=True) if commercial
            else _search(query, limit, mode=mode))
    return _usable(_guard_chunks(
        [_hydrate(h, query, commercial=commercial) for h in hits]))


def _usable(docs: list[dict]) -> list[dict]:
    """Keep docs whose text is substantial enough to ground an answer.

    The floor is tier-aware: ``full`` docs must clear ``MIN_SOURCE_CHARS``,
    while rights-limited ``reference``/``search_only`` docs — which by design
    hold only a relevance-centered snippet — clear ``MIN_SNIPPET_CHARS``. An
    unknown/blank tier is treated conservatively as needing ``MIN_SOURCE_CHARS``.
    """
    usable = []
    for d in docs:
        length = len((d.get("chunk_content") or "").strip())
        if d.get("store_mode") == "full":
            threshold = MIN_SOURCE_CHARS
        elif d.get("store_mode") in ("reference", "search_only"):
            threshold = MIN_SNIPPET_CHARS
        else:
            threshold = MIN_SOURCE_CHARS
        if length >= threshold:
            usable.append(d)
    return usable


# --- Hybrid (primary) grounding calibration ---------------------------------
# ``/search?mode=hybrid`` (CT100) is authority-aware: each result carries
# ``score`` (0.7·relevance + 0.3·authority), ``confidence`` (similarity +
# authority + match stage), ``authority_level`` and ``bm25_rank``. Hybrid is the
# primary strategy; the staged modes remain the fallback when it is unavailable
# or inconclusive. The thresholds *strengthen* grounding: a hybrid hit reaches
# GROUNDED only with primary authority, strong score/confidence AND a real
# lexical (BM25) match. A dense-only or secondary hit is at most PARTIAL, so
# semantic adjacency can never turn a nonsense query into an answer.
HYBRID_MIN_SCORE = 0.45
HYBRID_GROUNDED_SCORE = 0.70
HYBRID_GROUNDED_CONFIDENCE = 0.65
# Dense (nomic cosine) floor for GROUNDED. BM25 alone is not trustworthy: a
# single junk token can match OCR noise in a real statute ("xylophone zzz"
# matching "zzz'9" in the Appropriation Act), so a strong semantic match is
# required. A pure lexical hit (no dense side) still grounds only when it is
# the top BM25 result and carries primary authority.
HYBRID_MIN_DENSE = 0.55
HYBRID_MAX_BM25_RANK = 3
PRIMARY_AUTHORITY = frozenset(
    {"constitution", "act", "instrument", "judgment"})


def _as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _hybrid_verdict(docs: list[dict]):
    """Verdict for a usable hybrid result set, or ``None`` to fall back.

    ``None`` means "no reliable hybrid grounding" — the caller then runs the
    existing staged modes. GROUNDED requires primary authority plus a strong
    score/confidence and a lexical BM25 match; anything else usable is PARTIAL.
    """
    if not docs:
        return None
    top = docs[0]
    score = _as_float(top.get("score"))
    conf = _as_float(top.get("confidence"))
    authority = (top.get("authority_level") or "").strip().lower()
    if score is None and conf is None and not authority:
        # A brain that predates the calibration fields: the hybrid match is
        # still authority-aware, so preserve the old exact-hit GROUNDED.
        return "GROUNDED"
    if score is not None and score < HYBRID_MIN_SCORE:
        return None
    dense = _as_float(top.get("dense_sim"))
    bm25_rank = _as_float(top.get("bm25_rank"))
    semantic = dense is not None and dense >= HYBRID_MIN_DENSE
    lexical_only = (dense is None and bm25_rank is not None
                    and bm25_rank <= HYBRID_MAX_BM25_RANK)
    strong = (
        authority in PRIMARY_AUTHORITY
        and (score is None or score >= HYBRID_GROUNDED_SCORE)
        and (conf is None or conf >= HYBRID_GROUNDED_CONFIDENCE)
        and (semantic or lexical_only)
    )
    return "GROUNDED" if strong else "PARTIAL"


def retrieve(query: str, limit: int = 3, context: str = "",
             commercial: bool = False) -> dict:
    """Progressive retrieval. Returns {docs, verdict, stage}.

    Primary strategy is authority-aware ``hybrid`` retrieval (BM25 + dense +
    RRF); the staged modes are the fallback when hybrid is unavailable or
    inconclusive. The query is reduced to its significant tokens before
    searching: generic jurisdiction tokens ("ghana", "law", ...) are stripped
    so they cannot ground a nonsense query. A short anaphoric follow-up may
    borrow bounded topic tokens from ``context`` (the recent prior turn) so
    "and the penalty?" searches the topic under discussion. An empty
    significant-token set is UNGROUNDED and never searches.

    ``commercial=True`` applies the commercial-use gate at every stage: the
    company arm only ever grounds on commercially-licensed content.
    """
    q = _expand_followup((query or "").strip(), context)
    tokens = significant_tokens(q)
    if not tokens:
        return {"docs": [], "verdict": "UNGROUNDED", "stage": 0}
    q = " ".join(tokens)

    # Stage 1 (primary): authority-aware hybrid retrieval.
    docs = _stage(q, limit, "hybrid", commercial=commercial)
    if docs:
        verdict = _hybrid_verdict(docs)
        if verdict:
            return {"docs": docs, "verdict": verdict, "stage": 1}

    # Stage 2 (fallback): exact phrase (server builds the FTS phrase query)
    docs = _stage(q, limit, "phrase", commercial=commercial)
    if docs:
        return {"docs": docs, "verdict": "GROUNDED", "stage": 2}

    # Stage 3: AND of tokens
    docs = _stage(q, limit, "and", commercial=commercial)
    if docs:
        return {"docs": docs, "verdict": "GROUNDED", "stage": 3}

    # Stage 4: OR of tokens
    docs = _stage(q, limit, "or", commercial=commercial)
    if docs:
        return {"docs": docs, "verdict": "PARTIAL", "stage": 4}

    # Stage 5: raw keyword fallback (title/citation LIKE)
    docs = _stage(q, limit, "like", commercial=commercial)
    if docs:
        return {"docs": docs, "verdict": "PARTIAL", "stage": 5}

    return {"docs": [], "verdict": "UNGROUNDED", "stage": 0}


def source_signature(docs: list[dict], verdict: str = "") -> str:
    """Stable signature of the retrieved source set for cache-binding.

    A cached answer may only be replayed when retrieval would return the same
    sources, so the signature folds the verdict and the sorted source
    identity (id, else citation, else title). Different sources -> different
    signature -> cache miss (no stale answer attributed to fresh sources).
    """
    if not docs:
        return ""
    import hashlib
    parts = []
    for d in docs:
        ident = str(d.get("id") or d.get("citation") or d.get("title") or "")
        title = str(d.get("title") or "")
        parts.append(f"{ident}|{title}")
    raw = "\n".join(sorted(parts))
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"{verdict or ''}:{digest}"


# Strict-grounding reply texts (owner directive). Kept here so every
# legal-answer surface — the bot free-text path, the Telegram menu handlers,
# the slash commands and the Command Center test query — shares the exact same
# refusals and PARTIAL banner. ``bot`` re-exports these names for compatibility.
UNGROUNDED_REPLY = (
    "⚖️ I couldn't find an authoritative Ghanaian source for that in my legal "
    "database, so I won't guess. Try rephrasing, or ask about a topic I cover "
    "(e.g. Criminal Offences Act, Contracts Act, Land Act, the 1992 Constitution)."
)
JURISDICTION_REFUSAL = (
    "⚖️ I only handle Ghana legal matters. Please ask a question about Ghana law."
)
PARTIAL_BANNER = "ℹ️ _Limited sources — some points may be general._\n\n"


# ---------------------------------------------------------------------------
# Ask-to-Acquire gap recording (Phase 7, Task 3)
# ---------------------------------------------------------------------------

def _has_on_topic_primary(docs: list) -> bool:
    """True when the result set includes a primary authority on the topic.

    Staged-mode docs carry no ``authority_level``; only a hybrid result is
    authority-annotated. Absence therefore counts as "no on-topic primary",
    which is the conservative choice for gap recording.
    """
    for d in docs or []:
        level = (d.get("authority_level") or "").strip().lower()
        if level in PRIMARY_AUTHORITY:
            return True
    return False


def needs_acquisition(verdict: str, docs: list) -> bool:
    """Whether a verdict should be logged as an acquisition gap.

    UNGROUNDED always; PARTIAL only when it has **no on-topic primary** source
    (a PARTIAL anchored on an Act/Constitution is already useful and must not
    spawn a crawl).
    """
    if verdict == "UNGROUNDED":
        return True
    if verdict == "PARTIAL":
        return not _has_on_topic_primary(docs)
    return False


def record_gap_best_effort(question: str, asker=None):
    """Send one gap to the legal brain; swallow every error (best-effort)."""
    try:
        from core import legal_brain_client as lb
        return lb.record_gap(question, asker=asker)
    except Exception as exc:  # noqa: BLE001 - never break the answer
        logger.warning("grounding: gap recording failed (ignored): %s", exc)
        return None


def maybe_record_gap(question: str, verdict: str, docs: list, asker=None,
                     blocking: bool = False) -> bool:
    """Record an unanswered question as an acquisition gap (best-effort).

    Never blocks the answer: the network call runs on a daemon thread unless
    ``blocking`` is set (tests). Disabled entirely by ``KAI_LEGAL_GAP_RECORD=0``
    and when no ``asker`` is known. Returns True when a gap was (or will be)
    recorded.
    """
    if not asker:
        return False
    if os.environ.get("KAI_LEGAL_GAP_RECORD", "1") == "0":
        return False
    if not needs_acquisition(verdict, docs):
        return False
    if blocking:
        record_gap_best_effort(question, asker)
    else:
        threading.Thread(target=record_gap_best_effort,
                         args=(question, asker), daemon=True,
                         name="juris-gap-record").start()
    return True


def build_grounded_plan(query: str, task_type: str = "juris_research",
                        context: str = "", commercial: bool = False,
                        asker=None) -> dict:
    """Retrieval-gated plan for a legal answer. Never calls a model.

    ``asker`` (the Telegram chat id / CC user) is optional: when given, an
    UNGROUNDED question — or a PARTIAL with no on-topic primary — is recorded
    as an acquisition gap on the legal brain (best-effort, off the answer
    path), so the demand-driven harvester can go and find the missing law.

    This is the single gate every legal-answer surface routes through, so the
    same rules apply everywhere: a non-Ghana question and an UNGROUNDED query
    both yield a ``refusal`` (the caller must not reach a model), while
    GROUNDED/PARTIAL yield a ``prompt`` built with
    :func:`core.juris_kai.prompt.build_grounded_prompt`, the deterministic
    Sources ``footer``, and (for PARTIAL) a ``banner``. Retrieval failure
    fails closed: an unreachable source is not a source.

    ``commercial=True`` is passed by the commercial (company) arm so its
    retrieval only ever grounds on commercially-licensed content; the
    foundation arm leaves it ``False``. The strict no-ungrounded rule is
    unchanged either way.

    Returns a dict with keys:
      ``groundable`` (bool), ``out_of_scope`` (bool), ``verdict`` (str),
      ``docs`` (list), ``prompt`` (str), ``banner`` (str), ``footer`` (str),
      ``source_key`` (str) and ``refusal`` (str | None).
    """
    q = (query or "").strip()

    if is_out_of_scope(q):
        return {"groundable": False, "out_of_scope": True,
                "verdict": "OUT_OF_SCOPE", "docs": [], "prompt": "",
                "banner": "", "footer": "", "source_key": "",
                "refusal": JURISDICTION_REFUSAL}

    try:
        # Thread follow-up context into retrieval only when present, so a
        # standalone question keeps the exact previous call shape (and its
        # cache/source-signature behaviour). Context expansion changes the
        # retrieved docs, so it is reflected in ``source_key`` below. The
        # commercial flag is likewise forwarded only when set, so the
        # foundation arm's call shape is unchanged.
        extra = {"commercial": True} if commercial else {}
        if context:
            result = retrieve(q, context=context, **extra)
        else:
            result = retrieve(q, **extra)
        verdict = result["verdict"]
        docs = result["docs"]
    except Exception as exc:  # noqa: BLE001 - fail closed, never answer ungrounded
        logger.warning("grounding plan retrieval failed (fail closed): %s", exc)
        verdict, docs = "UNGROUNDED", []

    if verdict == "UNGROUNDED" or not docs:
        maybe_record_gap(q, "UNGROUNDED", [], asker=asker)
        return {"groundable": False, "out_of_scope": False,
                "verdict": "UNGROUNDED", "docs": [], "prompt": "",
                "banner": "", "footer": "", "source_key": "",
                "refusal": UNGROUNDED_REPLY}

    # PARTIAL with no on-topic primary source is still an acquisition gap: it
    # is logged (best-effort) but the answer is delivered as usual.
    maybe_record_gap(q, verdict, docs, asker=asker)

    from core.juris_kai.prompt import build_grounded_prompt
    prompt = build_grounded_prompt(task_type, q, verdict, docs, context=context)
    return {
        "groundable": True,
        "out_of_scope": False,
        "verdict": verdict,
        "docs": docs,
        "prompt": prompt,
        "banner": PARTIAL_BANNER if verdict == "PARTIAL" else "",
        "footer": build_sources_footer(docs),
        "source_key": source_signature(docs, verdict),
        "refusal": None,
    }


def build_sources_footer(docs: list[dict]) -> str:
    """Deterministic Sources block built from retrieval (never the model).

    Currency is additive (Phase 3 T4): when a source carries an ``amended_by``
    string the footer says "as amended by …", and a ``temporal_status`` is shown
    as a ``[STATUS]`` tag (e.g. ``[AMENDED]`` / ``[REPEALED]``). Sources without
    those fields render exactly as before.
    """
    if not docs:
        return ""
    lines = ["\n\n📚 *Sources*"]
    for i, d in enumerate(docs, 1):
        title = (d.get("title") or "Untitled").strip()
        cite = (d.get("citation") or "").strip()
        year = d.get("year")
        mode = (d.get("store_mode") or "").strip()
        bits = [title]
        if cite and cite != title:
            bits.append(cite)
        if year:
            bits.append(str(year))
        if mode:
            bits.append(f"_{mode}_")
        line = " — ".join(bits)
        amendment = _footer_amendment(d.get("amended_by") or "")
        if amendment:
            line += f" — as amended by {amendment}"
        status = (d.get("temporal_status") or "").strip().upper()
        if status:
            line += f" [{status}]"
        lines.append(f"{i}. " + line)
    return "\n".join(lines)


_FOOTER_AMENDMENT_MAX = 180


def _footer_amendment(text: str) -> str:
    """Whitespace-normalize + bound the amendment clause for the footer."""
    collapsed = " ".join((text or "").split())
    if len(collapsed) <= _FOOTER_AMENDMENT_MAX:
        return collapsed
    return collapsed[: _FOOTER_AMENDMENT_MAX - 1].rstrip() + "…"
