#!/usr/bin/env python3
"""Kai Legal Brain — Ghana legal corpus + retrieval API.

Runs on CT 100 (`kai-legal-brain`). Stdlib only (no pip deps): imports the
portable `core.legal` corpus engine and exposes it over HTTP for the
orchestrator / juris_kai agents.

    python3 /opt/kai-legal-brain/legal_brain_api.py     # port 8100
"""
import json
import os
import re
import shutil
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

ROOT = "/opt/kai-legal-brain"
os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
sys.path.insert(0, ROOT)

from core.legal.engine import LegalEngine          # noqa: E402
from core.legal.schema import LegalDocument, validate_document  # noqa: E402
from core.legal.firewall import (                   # noqa: E402
    APPROVED_SOURCES, admit_document, classify_source, registry_snapshot,
)
from core.legal.classify import classify_document   # noqa: E402
from core.legal.compliance import (                  # noqa: E402
    allowed_use_mode, classify_rights_class, evaluate, parse_content_signals,
    robots_allows,
)
from core.legal.authority import (                   # noqa: E402
    good_law, rank_authority, verify_citation,
)
from core.legal.graph import LegalGraph              # noqa: E402
from core.legal.graphbuild import build_from_corpus  # noqa: E402
from core.legal.acquire import acquire_sources, http_fetch, http_fetch_bytes  # noqa: E402
from core.legal.pdf import (                          # noqa: E402
    extract_text as pdf_extract_text,
    extract_text_with_ocr as pdf_extract_ocr,
)
from core.legal.health import check_all              # noqa: E402
from core.legal.sources import ghalii                # noqa: E402
from core.legal.sources import sitemap as src_sitemap  # noqa: E402
from core.legal.registry import SourceRegistry, is_ai_denied  # noqa: E402
from core.legal import rights_engine                 # noqa: E402
from core.legal import restricted                    # noqa: E402
from core.legal import citations as citation_engine  # noqa: E402
from core.legal.research import run_research         # noqa: E402
from core.legal.lawchange import (                   # noqa: E402
    classify_changes, diff_texts, impact_analysis,
)
from core.legal.redteam import run_redteam           # noqa: E402
from core.legal import monitor as src_monitor        # noqa: E402
from core.legal.sources import oai as src_oai        # noqa: E402
from core.legal.sources import dspace as src_dspace  # noqa: E402
from core.legal import harvest as harvest_mod        # noqa: E402
from core.legal import harvest_sources as harvest_src  # noqa: E402
from core.legal import gaps as gap_queue             # noqa: E402
from core.legal.jobs import JobRegistry              # noqa: E402
from core.legal.status import infer_status           # noqa: E402
from core.legal.content_rules import is_junk_title   # noqa: E402
from core.legal.quality import metrics as quality_metrics  # noqa: E402
from core.legal.privacy import (                      # noqa: E402
    audit as privacy_audit, detect_privilege, find_pii, redact,
)
from core.legal import research_audit as src_research_audit  # noqa: E402
from core.legal.injection import scan as injection_scan  # noqa: E402
RESEARCH_AUDIT = os.path.join(ROOT, "data", "research_audit.jsonl")
PARL_OAI = "https://opac.parliament.gh/cgi-bin/koha/oai.pl"
REPO_OAI = "https://repository.parliament.gh/server/oai/request"
PARL_REPO = "https://repository.parliament.gh"
MONITOR_STATE = os.path.join(ROOT, "data", "monitor_seen.json")
from core.legal.workbench import (                    # noqa: E402
    MatterStore, argument_map, build_timeline, evidence_matrix, extract_entities,
)

DB = os.environ.get("KAI_LEGAL_DB", os.path.join(ROOT, "data", "legal_brain.db"))
TOKEN_FILE = os.environ.get("KAI_LEGAL_TOKEN_FILE",
                            os.path.join(ROOT, "data", ".token"))
PORT = int(os.environ.get("KAI_LEGAL_PORT", "8100"))


def _token():
    if os.path.exists(TOKEN_FILE):
        return open(TOKEN_FILE).read().strip()
    import secrets
    t = secrets.token_hex(24)
    open(TOKEN_FILE, "w").write(t)
    os.chmod(TOKEN_FILE, 0o600)
    return t


TOKEN = _token()
REGISTRY = SourceRegistry(os.path.join(ROOT, "data", "sources.json"))
MATTERS = MatterStore(os.path.join(ROOT, "data", "matters.json"))
JOBS = JobRegistry(os.path.join(ROOT, "data", "jobs.json"))
REPO_SEEN = os.path.join(ROOT, "data", "repo_seen.json")
HARVEST_CURSOR = os.path.join(ROOT, "data", "harvest_cursor.json")


def _load_json(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return default


def _save_json(path, obj):
    with open(path, "w") as fh:
        json.dump(obj, fh, indent=2)
import logging  # noqa: E402
import threading  # noqa: E402
_local = threading.local()

# Non-blocking guard so two scheduler triggers can never run the (bounded but
# network-bound) weekly harvest cycle concurrently and hammer a source.
_CYCLE_LOCK = threading.Lock()

logger = logging.getLogger("kai.legal_brain")

# /search result window. Out-of-range or unparseable limits are clamped
# rather than raising, so a bad query param can never 500 the endpoint.
_LIMIT_DEFAULT = 20
_LIMIT_MIN = 1
_LIMIT_MAX = 50


def _clamp_limit(raw, default: int = _LIMIT_DEFAULT) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(_LIMIT_MIN, min(value, _LIMIT_MAX))


_COMMERCIAL_TRUE = frozenset({"1", "true", "yes", "on"})


def _truthy(value) -> bool:
    return str(value).strip().lower() in _COMMERCIAL_TRUE


def commercial_requested(query_params, headers=None) -> bool:
    """Whether a request asks for commercial mode.

    Accepts ``?commercial=1`` (or true/yes/on) or the ``X-Kai-Commercial: 1``
    header. Absent/blank/false => non-commercial mode (foundation arm).
    """
    if _truthy((query_params.get("commercial") or [""])[0]):
        return True
    if headers is not None and _truthy(headers.get("X-Kai-Commercial", "")):
        return True
    return False


def engine():
    e = getattr(_local, "engine", None)
    if e is None:
        e = LegalEngine(DB)
        e.connect()
        _local.engine = e
    return e


_embed = threading.local()


def embedding_index():
    """Lazy per-thread dense index (nomic-embed-text on VM104 via the tunnel).

    The client is local-only; if the tunnel/model is down the hybrid search
    degrades to BM25 rather than failing the request.
    """
    idx = getattr(_embed, "index", None)
    if idx is None:
        from core.legal.embeddings import EmbeddingIndex
        idx = EmbeddingIndex(engine().storage)
        _embed.index = idx
    return idx


def _authority_fields(doc, conn=None):
    """Authority label/score for a search row (additive; never reorders)."""
    from core.legal import document_meta, ranking
    if conn is None:
        conn = engine().storage.conn
    try:
        meta = document_meta.get_meta(conn, doc.get("id"))
    except Exception:  # noqa: BLE001 - sidecar optional
        meta = None
    return ranking.annotate(doc, meta)


def _annotate_temporal(storage, results):
    """Attach temporal currency (Phase 3 T3) to search rows, best-effort.

    Currency is additive: if the graph/temporal layer is unavailable the
    response is returned unchanged rather than failing the search.
    """
    if not results:
        return results
    conn = getattr(storage, "conn", None)
    if conn is None:
        return results
    try:
        from core.legal import temporal
        temporal.annotate(conn, results)
    except Exception:  # noqa: BLE001 - currency must never break search
        pass
    return results


_SEARCH_MODES = ("phrase", "and", "or", "like")


def search_dispatch(query, mode="or", limit=_LIMIT_DEFAULT, *,
                    storage, embed_index=None, commercial=False):
    """Route a ``/search`` request to the right retriever.

    Returns ``(results, effective_mode)``. ``hybrid`` fuses BM25 + dense with
    authority-aware ranking; an unknown mode falls back to ``or`` (never 500).
    Existing modes are annotated with authority fields but keep their order.
    When ``commercial`` is set, non-commercial content is excluded at
    retrieval (fail-closed).
    """
    limit = _clamp_limit(limit)

    # An exact citation query ("Article 24", "Act 843", "L.I. 2377") is a
    # lookup, not a lexical search: the Constitution prints headings as
    # "24. ECONOMIC RIGHTS", so BM25/ANN can never surface it from the words.
    # This MUST run for every mode — previously the hybrid branch returned
    # first, so Juris Kai's primary (hybrid) path answered "Article 24" with
    # an unrelated Act.
    cite = _citation_lookup(query, storage, commercial=commercial)
    if cite is not None:
        for d in cite:
            d["status"] = infer_status(d)
            d.update(_authority_fields(d, getattr(storage, "conn", None)))
        return _annotate_temporal(storage, cite), "citation"

    if mode == "hybrid":
        from core.legal.hybrid import hybrid_search
        results = hybrid_search(query, limit=limit, storage=storage,
                                embed_index=embed_index, commercial=commercial)
        conn = getattr(storage, "conn", None)
        for d in results:
            d["status"] = infer_status(d)
            d["provenance"] = _provenance(d, query, conn)
        return _annotate_temporal(storage, results), "hybrid"

    # Default for everything that is not an exact citation: hybrid
    # (BM25 + dense + authority). Plain FTS bm25() ranks by term density, so a
    # long Act scores below short unrelated Acts for a two-word keyword query
    # ("director duties" -> Internal Audit Agency Act, not the Companies Act).
    # Hybrid fixes relevance; `keyword` remains available for a fast, lexical
    # only lookup when latency matters.
    if mode == "keyword":
        results = _keyword_search(query, limit, storage, commercial,
                                  k_mode="and")
        conn = getattr(storage, "conn", None)
        return _finish_keyword(results, query, conn, storage,
                               _annotate_temporal), "keyword"
    if mode in ("or", "and", "phrase", "like"):
        # honour an explicit lexical mode when asked for, but as a first-class
        # keyword search (wider fetch + relevance ranking + provenance)
        results = _keyword_search(query, limit, storage, commercial,
                                  k_mode=mode)
        conn = getattr(storage, "conn", None)
        return _finish_keyword(results, query, conn, storage,
                               _annotate_temporal), mode

    # unknown mode -> hybrid (the safe, relevant default)
    from core.legal.hybrid import hybrid_search
    results = hybrid_search(query, limit=limit, storage=storage,
                            embed_index=embed_index, commercial=commercial)
    conn = getattr(storage, "conn", None)
    for d in results:
        d["status"] = infer_status(d)
        d["provenance"] = _provenance(d, query, conn)
    return _annotate_temporal(storage, results), "hybrid"


def _keyword_search(query, limit, storage, commercial, k_mode="and"):
    """Lexical retrieval with a wider fetch then relevance ranking.

    Falls back phrase -> and -> or so a phrase search never returns nothing.
    """
    fetch = max(limit, 40)
    if k_mode == "phrase":
        rows = storage.search(query, limit=fetch, mode="phrase",
                              commercial=commercial)
        if not rows:
            rows = storage.search(query, limit=fetch, mode="and",
                                  commercial=commercial)
            if not rows:
                rows = storage.search(query, limit=fetch, mode="or",
                                      commercial=commercial)
        return rows
    return storage.search(query, limit=fetch, mode=k_mode,
                          commercial=commercial)


def _finish_keyword(results, query, conn, storage, annotate_fn):
    """Rank, annotate and add provenance to a keyword result set."""
    ranked = _rank_keyword_results(results, conn)
    for d in ranked:
        d["status"] = infer_status(d)
        d.update(_authority_fields(d, conn))
        d["provenance"] = _provenance(d, query, conn)
    return annotate_fn(storage, ranked)


def _provenance(result: dict, query: str, conn) -> dict:
    """Where an answer came from — for auditing that sources are genuine.

    Returns the document identity, the exact matching sentence (with the
    keyword/phrase highlighted position), and the corpus URL so an operator can
    verify every answer traces to a real, held document.
    """
    title = result.get("title") or ""
    text = (result.get("snippet") or result.get("content") or "")
    hit = ""
    try:
        from core.legal.query_normalize import tokens as _toks
        kws = _toks(query) or []
    except Exception:  # noqa: BLE001
        kws = []
    # pick the sentence containing the most query terms
    best, best_n = "", -1
    for sent in __import__("re").split(r"(?<=[.;])\s+", text):
        s = sent.strip()
        if not s:
            continue
        n = sum(1 for k in kws if k in s.lower())
        if n > best_n:
            best, best_n = s, n
    hit = best[:400]
    return {
        "document_id": result.get("id") or result.get("doc_id"),
        "title": title,
        "citation": result.get("citation") or "",
        "year": result.get("year"),
        "store_mode": result.get("store_mode") or "",
        "match_strategy": result.get("match_strategy") or "",
        "matched_text": hit,
    }


def _rank_keyword_results(results, conn):
    """Order keyword/FTS results by relevance then authority.

    The keyword path returned rows in FTS/date order, so "director duties"
    surfaced the Internal Audit Agency Act ahead of the Companies Act. Reuse
    the same blend as hybrid so keyword and semantic paths agree on what is
    most relevant.
    """
    if not results:
        return results
    try:
        from core.legal import ranking
        from core.legal import document_meta
        out = []
        for r in results:
            d = dict(r)
            if "authority_score" not in d:
                d.update(ranking.annotate(d, None))
            # give the BM25 path an rrf-like signal so relevance_score works
            d.setdefault("rrf_score", 1.0)
            if "bm25_rank" not in d:
                d["bm25_rank"] = 1
            out.append(d)
        return ranking.rank_results(out)
    except Exception:  # noqa: BLE001 - never break search on ranking failure
        return results


def _citation_lookup(query, storage, *, commercial=False):
    """Resolve an exact citation for EVERY mode.

    Covers ``Article N`` (via the article window) and instruments
    (Act / L.I. / C.I. / P.N.D.C.L.) via ``citations.lookup_all``. Returns a
    result list shaped like ``/search`` rows, or ``None`` when the query is not
    an exact citation (so normal lexical/semantic search runs unchanged).

    When MORE THAN ONE distinct instrument matches, the first result carries a
    ``disambiguation`` block listing the alternatives so the caller (Juris Kai)
    can ask the user which one they mean instead of guessing or giving up.
    """
    # 1) article window ("Article 24")
    art = _article_lookup(query, storage, commercial=commercial)
    if art is not None:
        return art
    # 2) instrument lookup ("Act 992", "L.I. 2377", …)
    if storage is None:
        return None
    conn = getattr(storage, "conn", None)
    if conn is None:
        return None
    try:
        from core.legal import citations
        parsed = _parse_instrument(query)
        if not parsed:
            return None
        ctype, number = parsed
        matches = citations.lookup_all(conn, ctype, number)
        if not matches:
            return None
        rows = []
        for m in matches:
            rows.append({
                "id": m["id"], "doc_id": m["id"],
                "title": m.get("title") or "",
                "snippet": (m.get("content") or "")[:2000],
                "store_mode": m.get("store_mode") or "",
                "citation": m.get("citation") or "",
                "year": m.get("year"),
                "match_strategy": f"instrument:{ctype}",
            })
        # Distinct instruments = genuinely different documents, not editions,
        # stubs or differently-formatted citations of the SAME act. Key on the
        # TITLE with the act number and stop-words removed, so
        # "Companies Act 2019 (Act 992)" and "Companies Act, 2019 (ACT 992)"
        # collapse to one; a real second act (different name) keeps its own key.
        def _norm(title):
            t = (title or "").lower()
            t = re.sub(r"[^a-z ]+", " ", t)
            for junk in ("act", "no", "revised", "edition", "consolidated",
                         "as", "amended", "instrument", "of", "the"):
                t = re.sub(rf"\b{junk}\b", " ", t)
            return re.sub(r"\s+", " ", t).strip()

        distinct = {}
        for m in matches:
            key = _norm(m.get("title")) or (m.get("citation") or "").lower()
            # keep the most useful copy for the option list (prefer full)
            cur = distinct.get(key)
            if cur is None or (m.get("store_mode") == "full"
                               and cur.get("store_mode") != "full"):
                distinct[key] = m
        if len(distinct) > 1:
            rows[0]["disambiguation"] = {
                "type": ctype, "number": number,
                "count": len(distinct),
                "options": [
                    {"id": v["id"], "title": v.get("title") or "",
                     "citation": v.get("citation") or "",
                     "store_mode": v.get("store_mode") or ""}
                    for v in distinct.values()
                ],
                "question": f"I found {len(distinct)} different documents for "
                            f"'{_ABBREV.get(ctype, ctype)} {number}'. Which one "
                            f"do you mean?",
            }
        return rows
    except Exception:  # noqa: BLE001
        return None


_ABBREV = {"act": "Act", "li": "L.I.", "ci": "C.I.", "pndcl": "P.N.D.C.L.",
           "nrcd": "N.R.C.D.", "article": "Article"}


_INSTRUMENT_RE = re.compile(
    r"(?:p\.?\s*n\.?\s*d\.?\s*c\.?\s*l\.?|n\.?\s*r\.?\s*c\.?\s*d\.?|"
    r"l\.?\s*i\.?|c\.?\s*i\.?|act|a\.?\s*c\.?\s*t\.?)"
    r"\s*(?:no\.?\s*)?(\d{1,5})\b", re.I)


def _parse_instrument(query):
    """Return ``(ctype, number)`` for an exact instrument citation, else None.

    Only fires when the query is essentially just a citation (e.g. "Act 843",
    "L.I. 2377"), so normal questions containing "act" are untouched.
    """
    q = (query or "").strip()
    if len(q) > 40 or len(q.split()) > 4:
        return None
    m = _INSTRUMENT_RE.search(q)
    if not m:
        return None
    # normalise the matched prefix to a citation type
    prefix = m.group(0)[: m.start(1) - m.start(0)].replace(".", "")
    compact = re.sub(r"\s+", "", prefix).lower()
    if compact.startswith(("pndcl", "nrcd")):
        ctype = "pndcl" if compact.startswith("pndcl") else "nrcd"
    elif compact.startswith(("li",)):
        ctype = "li"
    elif compact.startswith("ci"):
        ctype = "ci"
    elif compact.startswith("act"):
        ctype = "act"
    else:
        return None
    return ctype, int(m.group(1))


def _article_lookup(query, storage, *, commercial=False):
    """Article-window result for an exact ``Article N`` query, else ``None``.

    Returns a one-row result list shaped like ``/search`` rows with the article
    text as the snippet, or ``None`` when the query is not an exact article
    reference / the article is not held (so normal search still runs).
    """
    from core.legal import citations
    number = citations.parse_article_query(query)
    if number is None or storage is None:
        return None
    conn = getattr(storage, "conn", None)
    if conn is None:
        return None
    try:
        inst = citations.make_lookup(conn)("article", number)
    except Exception:  # noqa: BLE001 - fall back to normal search
        return None
    if not inst:
        return None
    snippet = inst.get("content") or ""
    return [{
        "id": inst["id"], "doc_id": inst["id"],
        "title": inst.get("title") or "",
        "snippet": snippet,
        "store_mode": inst.get("store_mode") or "",
        "citation": inst.get("citation") or "",
        "type": inst.get("type") or "",
        "year": inst.get("year"),
        "status": inst.get("status") or "current",
        "match_strategy": "article",
    }]


def everyday_retrieve(query, commercial=False):
    """Retrieval seam for the Everyday-Law layer (``{docs, verdict, stage}``).

    Reuses the same authority-aware hybrid retrieval as ``/search`` (falling
    back to ``or``), hydrates each row with its snippet, and derives the
    grounding verdict the plain-language builder requires. A retrieval failure
    fails closed to UNGROUNDED — an unreachable source is not a source.
    """
    results, mode = [], "or"
    try:
        results, mode = search_dispatch(
            query, mode="hybrid", limit=5, storage=engine().storage,
            embed_index=embedding_index(), commercial=commercial)
    except Exception as exc:  # noqa: BLE001 - fail closed, never guess
        logger.warning("everyday retrieve hybrid failed: %s", exc)
    if not results:
        try:
            results, mode = search_dispatch(
                query, mode="or", limit=5, storage=engine().storage,
                commercial=commercial)
        except Exception as exc:  # noqa: BLE001
            logger.warning("everyday retrieve fallback failed: %s", exc)
            results = []
    docs = []
    for d in results:
        text = d.get("snippet") or d.get("content") or ""
        docs.append({**d, "chunk_content": text})
    if not docs:
        return {"docs": [], "verdict": "UNGROUNDED", "stage": 0}
    top = docs[0]
    level = (top.get("authority_level") or "").strip().lower()
    primary = level in ("constitution", "act", "instrument", "judgment")
    grounded = primary and (top.get("store_mode") == "full")
    return {"docs": docs, "verdict": "GROUNDED" if grounded else "PARTIAL",
            "stage": 1, "mode": mode}


GRAPH_DB = os.environ.get("KAI_LEGAL_GRAPH_DB",
                          os.path.join(ROOT, "data", "legal_graph.db"))
_g = threading.local()


def graph():
    g = getattr(_g, "g", None)
    if g is None:
        g = LegalGraph(GRAPH_DB)
        g.connect()
        _g.g = g
    return g


def find_by_citation(citation):
    if not citation:
        return None
    return harvest_mod.find_existing(engine().storage.conn, citation)


class RobotsCache:
    """Fetch + cache robots.txt per host; expose Content-Signal decisions."""

    def __init__(self, fetcher, ttl: int = 3600):
        self._fetch = fetcher
        self._ttl = ttl
        self._cache: dict = {}

    def get(self, url: str) -> str:
        host = urlparse(url or "").hostname or ""
        if not host:
            return ""
        now = time.time()
        hit = self._cache.get(host)
        if hit is not None and now - hit[0] < self._ttl:
            return hit[1]
        try:
            text = self._fetch(f"https://{host}/robots.txt")
        except Exception:  # noqa: BLE001 - unreachable robots.txt => no rules
            text = ""
        self._cache[host] = (now, text)
        return text

    def allows(self, url: str) -> bool:
        return robots_allows(self.get(url), url)

    def signals(self, url: str) -> dict:
        return parse_content_signals(self.get(url))


ROBOTS = RobotsCache(http_fetch)


class RightsGateError(Exception):
    """Raised when a document may not be stored at all (store_mode=none)."""


def _attr(doc, name, default=""):
    if isinstance(doc, dict):
        return doc.get(name, default) or default
    return getattr(doc, name, default) or default


def rights_gate(doc, content: str, *, operator: bool = False,
                metadata=None) -> dict:
    """Evaluate the copyright/licence gate for one document.

    Combines the source's robots.txt + Content-Signal with the allowlist rights
    basis and, when present, the document's own ``dc.*`` rights metadata. An
    explicit open licence in that metadata can grant full text; absent metadata
    leaves the conservative default (no basis => reference).
    """
    url = _attr(doc, "source_url")
    rights_class = classify_rights_class({
        "type": _attr(doc, "type"), "title": _attr(doc, "title")})
    # Restricted sources (GhaLII: ai_input_restricted; Laws.Africa:
    # permission_required) are a hard deny that overrides both the
    # content-aware REJECT exception and the operator bypass.
    if url and restricted.is_restricted(url):
        rbasis = restricted.basis(url) or restricted.RESTRICTED_BASIS
        return {"mode": "none", "store_mode": "none", "content_rule": "none",
                "allowed": False, "content_available": False,
                "rights_basis": rbasis,
                "rights_class": rights_class,
                "rights_metadata": {"matched": False,
                                    "rights_basis": rbasis,
                                    "store_mode": "none", "field": "", "raw": ""},
                "content": ""}
    if operator:
        # Operator-provided/licensed bytes were not crawled; the operator
        # asserts the rights basis, so only the rights ceiling applies.
        return evaluate({}, "operator_licensed", rights_class,
                        robots_allowed=True, content=content, metadata=metadata)
    # 2026-09-23 audit verdicts: an ``ai_input=no`` host must never be ingested,
    # and a GREEN_LAW_ONLY host (``ai_input=law_only``) may only contribute
    # primary enactments/judgments — never its editorial/commentary.
    entry = REGISTRY.resolve(url) if url else None
    if entry is not None and is_ai_denied(entry):
        return {"mode": "none", "store_mode": "none", "content_rule": "none",
                "allowed": False, "content_available": False,
                "rights_basis": entry.get("rights_basis") or "unknown",
                "rights_class": rights_class,
                "rights_metadata": {"matched": False, "rights_basis": "unknown",
                                    "store_mode": "none", "field": "", "raw": ""},
                "content": ""}
    if entry is not None and rights_engine.law_only_blocks(
            entry, {"type": _attr(doc, "type"), "title": _attr(doc, "title")}):
        return {"mode": "none", "store_mode": "none", "content_rule": "none",
                "allowed": False, "content_available": False,
                "rights_basis": entry.get("rights_basis") or "unknown",
                "rights_class": rights_class,
                "rights_metadata": {"matched": False, "rights_basis": "unknown",
                                    "store_mode": "none", "field": "", "raw": ""},
                "content": ""}
    basis = REGISTRY.rights_basis_for(url) if url else "unknown"
    if url:
        robots_ok = ROBOTS.allows(url)
        signals = ROBOTS.signals(url)
    else:
        robots_ok, signals = True, {}
    return evaluate(signals, basis, rights_class, robots_allowed=robots_ok,
                    content=content, metadata=metadata)


def gated_ingest(doc, content: str, constitution_refs=None,
                 operator: bool = False, metadata=None) -> int:
    """Rights-gate a document, then ingest only the permitted content."""
    decision = rights_gate(doc, content, operator=operator, metadata=metadata)
    if not decision["allowed"]:
        try:
            from core.legal.optional import RejectionLog
            RejectionLog().record(
                "rights", _attr(doc, "source_url") or _attr(doc, "citation"),
                f"store_mode=none rights_basis={decision['rights_basis']}")
        except Exception:  # noqa: BLE001 - logging must not break the gate
            pass
        raise RightsGateError(
            "rights gate: store_mode=none "
            f"(rights_basis={decision['rights_basis']})")
    doc_id = engine().ingest(doc, decision["content"],
                             constitution_refs=constitution_refs,
                             store_mode=decision["store_mode"],
                             rights_basis=decision["rights_basis"],
                             content_available=decision.get("content_available"))
    meta = decision.get("rights_metadata") or {}
    try:
        engine().storage.record_rights_audit(
            doc_id, action="ingest",
            from_store_mode="", to_store_mode=decision["store_mode"],
            rights_basis=decision["rights_basis"],
            rights_class=decision.get("rights_class", "unknown"),
            chars_before=len(content or ""),
            chars_after=len(decision["content"] or ""),
            reason=("metadata rights grant" if meta.get("matched")
                    else "no rights metadata"),
            rights_raw=meta.get("raw", ""))
    except Exception:  # noqa: BLE001 - audit must never break ingest
        pass
    return doc_id


def upgrade_stub(existing_id, doc, content, metadata=None) -> dict:
    """Upgrade an existing metadata-only stub with newly-resolved text.

    Idempotent: a document that already carries body text is left alone
    (``upgraded=False``), so re-ingesting a full record is a no-op. The rights
    gate decides the store mode for the resolved text. Shared by the HTTP
    handler and the bulk re-harvest CLI (``scripts/reharvest_stubs.py``).
    """
    info = engine().get(existing_id) or {}
    if (info.get("content") or "").strip():
        return {"upgraded": False, "reason": "already has content"}
    decision = rights_gate(doc, content, metadata=metadata)
    if not decision["allowed"]:
        return {"upgraded": False, "reason": "rights gate",
                "store_mode": "none"}
    engine().storage.set_rights_content(
        existing_id, decision["content"],
        store_mode=decision["store_mode"],
        rights_basis=decision["rights_basis"],
        rights_class=decision.get("rights_class", "unknown"),
        reason="full-text resolution upgrade",
        action="upgrade",
        rights_raw=(decision.get("rights_metadata") or {}).get("raw", ""))
    return {"upgraded": True, "store_mode": decision["store_mode"],
            "content_available": decision.get("content_available", True)}


def gated_admit(doc, allowlist=None) -> dict:
    """Firewall admission plus the robots.txt / Content-Signal crawl check."""
    url = _attr(doc, "source_url")
    if url and restricted.is_restricted(url):
        return {"admitted": False, "authority": "unverified",
                "reason": restricted.reason(url),
                "host": urlparse(url).hostname or ""}
    decision = admit_document(doc, allowlist=allowlist or REGISTRY.allowlist())
    if not decision.get("admitted"):
        return decision
    if not url:
        return decision
    host = urlparse(url).hostname or ""
    if not ROBOTS.allows(url):
        return {"admitted": False, "authority": "unverified",
                "reason": "robots.txt disallows", "host": host}
    if allowed_use_mode(ROBOTS.signals(url)) == "none":
        return {"admitted": False, "authority": "unverified",
                "reason": "content-signal forbids use", "host": host}
    return decision

_DOC_FIELDS = ("jurisdiction", "court", "year", "citation", "judge", "parties",
               "status", "title", "date", "type", "source_url")


DASHBOARD_HTML = """<!doctype html><html><head><meta charset="utf-8">
<title>Kai Legal Brain - Sources</title><meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{font-family:system-ui;background:#0d1117;color:#e6edf3;margin:0}
header{padding:14px 22px;background:#161b22;border-bottom:1px solid #30363d}
main{max-width:900px;margin:0 auto;padding:20px}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin-bottom:16px}
input,select{background:#0d1117;color:#e6edf3;border:1px solid #30363d;border-radius:6px;padding:7px;margin:4px 0;width:100%;box-sizing:border-box}
button{background:#238636;color:#fff;border:0;border-radius:6px;padding:8px 14px;cursor:pointer}button.sec{background:#30363d}
table{width:100%;border-collapse:collapse}td,th{padding:6px;border-bottom:1px solid #21262d;text-align:left;font-size:14px}
.tag{padding:2px 6px;border-radius:4px;font-size:12px;color:#fff}
.open{background:#1f6feb}.paywalled{background:#8b2f2f}.restricted{background:#6e5494}.licensed{background:#2d6a4f}
.muted{color:#8b949e}</style></head><body>
<header><b>Kai Legal Brain</b> &mdash; Source Registry <small>(add paywalled / authorized sites here)</small></header>
<main>
<div class="card"><label>Access token</label><input id="tok" type="password" placeholder="legal token">
<button class="sec" onclick="load()">Load</button> <span id="st" class="muted"></span></div>
<div class="card"><h3>Add a source</h3>
<input id="name" placeholder="Name (e.g. Ghana Law Finder)">
<input id="domain" placeholder="Domain (e.g. lawfinder.gov.gh)">
<select id="authority"><option value="primary">primary</option><option value="secondary">secondary</option><option value="tertiary" selected>tertiary</option></select>
<select id="access"><option value="open">open</option><option value="licensed">licensed</option><option value="paywalled">paywalled</option><option value="restricted">restricted</option></select>
<button onclick="add()">Add source</button></div>
        <div class="card"><h3>Add a document to the corpus</h3>
<input id="d_title" placeholder="Title">
<input id="d_citation" placeholder="Citation (e.g. [2024] GHSC 1 / Act 992)">
<input id="d_court" placeholder="Court / issuing body">
<input id="d_year" type="number" placeholder="Year">
<select id="d_type"><option value="judgment">judgment</option><option value="act">act</option><option value="bill">bill</option><option value="regulation">regulation</option><option value="gazette">gazette</option><option value="other">other</option></select>
<input id="d_source" placeholder="Source URL (allowlisted domain) — or blank = model-generated">
<textarea id="d_content" placeholder="Document text (or paste here)"></textarea>
<input id="d_file" type="file" accept=".pdf,.txt,.md,.html">
<label style="font-size:13px"><input id="d_op" type="checkbox" style="width:auto"> operator-provided / licensed (authoritative)</label>
<button onclick="addDoc()">Ingest pasted text</button>
<button class="sec" onclick="addFile()">Upload file</button> <span id="dstat" class="muted"></span></div>
        <div class="card"><h3>Sources</h3><table id="tbl"></table></div>
</main>
<script>
function tok(){return document.getElementById('tok').value}
async function api(p,body){const r=await fetch(p,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json','X-Legal-Token':tok()}:{},body:body?JSON.stringify(body):undefined});return r.json()}
async function load(){try{const s=await api('/registry');document.getElementById('st').textContent=s.sources.length+' source(s)';document.getElementById('st').className='muted';
 const rows=s.sources.map(x=>'<tr><td>'+x.name+'<br><span class="muted">'+x.domain+'</span></td><td><span class="tag '+x.access+'">'+x.access+'</span></td><td>'+x.authority+'</td><td>'+(x.enabled?'auto':'&mdash;')+'</td><td><button class="sec" onclick="rm(\\''+x.id+'\\')">remove</button></td></tr>').join('');
 document.getElementById('tbl').innerHTML='<tr><th>Source</th><th>Access</th><th>Authority</th><th>Auto</th><th></th></tr>'+rows;}
 catch(e){document.getElementById('st').textContent='enter token';}}
async function add(){await api('/registry/add',{name:document.getElementById('name').value,domain:document.getElementById('domain').value,authority:document.getElementById('authority').value,access:document.getElementById('access').value});load()}
async function rm(id){await api('/registry/remove',{id:id});load()}
async function addDoc(){const r=await api('/ingest',{title:document.getElementById('d_title').value,citation:document.getElementById('d_citation').value,court:document.getElementById('d_court').value,year:parseInt(document.getElementById('d_year').value||'0'),type:document.getElementById('d_type').value,jurisdiction:'ghana',source_url:document.getElementById('d_source').value,content:document.getElementById('d_content').value});
 document.getElementById('dstat').textContent=JSON.stringify(r).slice(0,140);load();}
async function addFile(){const f=document.getElementById('d_file').files[0];if(!f){document.getElementById('dstat').textContent='choose a file';return}
 const q=new URLSearchParams({title:document.getElementById('d_title').value||f.name,citation:document.getElementById('d_citation').value||f.name,court:document.getElementById('d_court').value,year:document.getElementById('d_year').value||'0',type:document.getElementById('d_type').value,source_url:document.getElementById('d_source').value,operator_provided:document.getElementById('d_op').checked?'1':'0'});
 const r=await fetch('/ingest/file?'+q.toString(),{method:'POST',headers:{'X-Legal-Token':tok()},body:f});
 const j=await r.json().catch(()=>({error:r.status}));
 document.getElementById('dstat').textContent=JSON.stringify(j).slice(0,160);load();}
load();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj, ctype="application/json"):
        body = (obj.encode() if isinstance(obj, str)
                else json.dumps(obj, default=str).encode())
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self):
        return bool(TOKEN) and self.headers.get("X-Legal-Token") == TOKEN

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        p = u.path
        try:
            if p == "/health":
                return self._send(200, {"ok": True, "db": DB,
                                        "documents": len(engine().list_documents(limit=100000))})
            if p == "/stats":
                docs = engine().list_documents(limit=100000)
                by = {"status": {}, "type": {}, "jurisdiction": {},
                      "store_mode": {}}
                avail = {True: 0, False: 0}
                for d in docs:
                    for k in by:
                        by[k][d.get(k) or "?"] = by[k].get(d.get(k) or "?", 0) + 1
                    avail[bool(d.get("content_available"))] += 1
                return self._send(200, {
                    "documents": len(docs), "by": by,
                    "content_available": {"true": avail[True],
                                          "false": avail[False]}})
            if p == "/api/legal/rejections":
                from core.legal.optional import RejectionLog
                return self._send(200, {"rejections": RejectionLog().read(limit=int(q.get("limit", [200])[0]))})
            if p == "/api/legal/policy/versions":
                from core.legal.optional import PolicyVersions
                pv = PolicyVersions()
                return self._send(200, {"current": pv.current(), "history": pv.history()})
            if p == "/api/legal/metrics":
                from core.legal.optional import metrics_snapshot
                return self._send(200, metrics_snapshot())
            if p == "/api/legal/mcp":
                from core.legal.optional import mcp_tools, mcp_call
                tool = q.get("tool", [""])[0]
                if tool:
                    return self._send(200, mcp_call(tool, {"query": q.get("q", [""])[0],
                                                           "limit": int(q.get("limit", [10])[0])}))
                return self._send(200, {"tools": mcp_tools()})
            if p == "/api/legal/customary":
                from core.legal.optional import customary_sources
                return self._send(200, {"sources": customary_sources()})
            if p == "/api/legal/comparative":
                from core.legal.optional import comparative_jurisdictions
                return self._send(200, {"jurisdictions": comparative_jurisdictions()})
            if p == "/api/legal/library":
                from core.legal.optional import library_catalog
                return self._send(200, {"catalog": library_catalog()})
            if p == "/api/legal/research/consensus":
                from core.legal.optional import research_consensus
                qq = q.get("q", [""])[0]
                docs = engine().list_documents(limit=100000)
                def r_text(x, limit):
                    xl = (x or "").lower()
                    return [{"document_id": d.get("id")} for d in docs
                            if xl and xl in ((d.get("title") or "") + " " + (d.get("citation") or "")).lower()][:limit]
                def r_cit(x, limit):
                    xl = (x or "").lower()
                    return [{"document_id": d.get("id")} for d in docs if xl and xl in (d.get("citation") or "").lower()][:limit]
                def r_any(x, limit):
                    xl = (x or "").lower()
                    return [{"document_id": d.get("id")} for d in docs if xl and xl in str(d).lower()][:limit]
                return self._send(200, research_consensus(qq, [r_text, r_cit, r_any]))
            if p == "/citation":
                corpus = [d.get("citation") for d in engine().list_documents(limit=100000)]
                return self._send(200, verify_citation(q.get("q", [""])[0], corpus))
            if p == "/citations/verify":
                # Read-only citation audit: extract + verify every citation in a
                # generated answer against the corpus (Phase 2 Task 1).
                lookup = citation_engine.make_lookup(engine().storage.conn)
                return self._send(200, citation_engine.verify_text(
                    q.get("q", [""])[0], lookup))
            if p == "/beliefs":
                # Belief ledger read (Phase 2 Task 3). Token-gated like the
                # other privileged reporting surfaces; never writes.
                if not self._authed():
                    return self._send(401, {"error": "unauthorized"})
                try:
                    limit = int(q.get("limit", [50])[0])
                except (TypeError, ValueError):
                    limit = 50
                return self._send(200, {"beliefs": citation_engine.list_propositions(
                    engine().storage.conn, limit=limit)})
            if p == "/gaps":
                # Ask-to-Acquire gap queue read (Phase 7, Task 3). Token-gated;
                # the CT 111 scheduler lists pending gaps and then triggers a
                # bounded acquire pass per gap. Read-only.
                if not self._authed():
                    return self._send(401, {"error": "unauthorized"})
                status = (q.get("status") or [None])[0]
                try:
                    limit = int((q.get("limit") or [50])[0])
                except (TypeError, ValueError):
                    limit = 50
                return self._send(200, {"gaps": gap_queue.list_gaps(
                    status=status, limit=limit)})
            if p == "/good-law" or p.startswith("/good-law/"):
                doc_id = int(p.rsplit("/", 1)[1]) if p != "/good-law" else 0
                doc = engine().get(doc_id) or {}
                return self._send(200, {"good_law": good_law(doc) if doc else "unknown"})
            if p == "/authority/rank":
                ranked = rank_authority(engine().list_documents(limit=100000))
                return self._send(200, {"ranked": [
                    d.get("citation") or d.get("title") for d in ranked]})
            if p == "/verify":
                docs = engine().list_documents(limit=100000)
                results = [engine().verify_integrity(d["id"]) for d in docs]
                failed = [r for r in results if not r.get("all_versions_intact")]
                return self._send(200, {"checked": len(results),
                                        "intact": len(results) - len(failed),
                                        "failed": failed})
            if p == "/graph/stats":
                return self._send(200, graph().stats())
            if p.startswith("/graph/neighbors/"):
                nid = p.rsplit("/", 1)[1]
                return self._send(200, {"neighbors": graph().neighbors(
                    nid, q.get("relation", [None])[0])})
            if p.startswith("/graph/cites/"):
                return self._send(200, {"cites": graph().citations(p.rsplit("/", 1)[1])})
            if p.startswith("/graph/cited-by/"):
                return self._send(200, {"cited_by": graph().cited_by(p.rsplit("/", 1)[1])})
            if p == "/relations" or p.startswith("/relations/"):
                # Read-only typed knowledge-graph relationships (Phase 3 T2).
                # Token-gated like the other privileged reporting surfaces;
                # never writes to the corpus or the graph.
                if not self._authed():
                    return self._send(401, {"error": "unauthorized"})
                raw = (p.rsplit("/", 1)[1] if p != "/relations"
                       else q.get("id", [""])[0])
                try:
                    doc_id = int(raw)
                except (TypeError, ValueError):
                    return self._send(400, {"error": "invalid document id"})
                from core.legal import knowledge_graph as legal_kg
                return self._send(200, legal_kg.relation_report(
                    engine().storage.conn, doc_id))
            if p == "/status" or p.startswith("/status/"):
                # Read-only temporal / current-law status (Phase 3 T3).
                # Token-gated like /relations; never writes to the corpus.
                if not self._authed():
                    return self._send(401, {"error": "unauthorized"})
                from core.legal import temporal
                conn = engine().storage.conn
                if p == "/status":
                    raw = (q.get("ids") or [""])[0]
                    ids = []
                    for part in raw.split(","):
                        part = part.strip()
                        if not part:
                            continue
                        try:
                            ids.append(int(part))
                        except (TypeError, ValueError):
                            return self._send(400, {"error":
                                                    f"invalid document id: {part}"})
                    if not ids:
                        return self._send(400, {"error": "missing ids"})
                    return self._send(200, {"statuses":
                                            temporal.bulk_report(conn, ids)})
                try:
                    doc_id = int(p.rsplit("/", 1)[1])
                except (TypeError, ValueError):
                    return self._send(400, {"error": "invalid document id"})
                return self._send(200, temporal.status_report(conn, doc_id))
            if p == "/source-health":
                urls = [s["base_url"] for s in REGISTRY.list(enabled_only=True)]
                return self._send(200, check_all(urls, http_fetch))
            if p == "/registry" or p == "/sources":
                return self._send(200, REGISTRY.snapshot())
            if p == "/rights/audit":
                # Read-only rights/source-policy audit for the Command Center
                # and admins. Uses the same X-Legal-Token gate as other
                # privileged surfaces; never writes to the corpus or DB.
                if not self._authed():
                    return self._send(401, {"error": "unauthorized"})
                from scripts.rights_audit import build_report
                return self._send(200, build_report(DB))
            if p == "/coverage":
                # Read-only legal-area coverage + harvest priorities. Same
                # X-Legal-Token gate as the other reporting surfaces; opens
                # the DB read-only and never writes.
                if not self._authed():
                    return self._send(401, {"error": "unauthorized"})
                from core.legal.coverage import build_report as coverage_report
                kwargs = {}
                raw = (q.get("threshold") or [None])[0]
                if raw is not None:
                    try:
                        kwargs["threshold"] = int(raw)
                    except (TypeError, ValueError):
                        pass
                return self._send(200, coverage_report(DB, **kwargs))
            if p == "/licences":
                # Source commercial-use licence register (Phase 8, Task 3).
                # Read-only policy metadata from core.legal.licenses; same
                # X-Legal-Token gate as the other reporting surfaces. Serves
                # the register as data (the CC table) plus its markdown doc.
                if not self._authed():
                    return self._send(401, {"error": "unauthorized"})
                from core.legal import licenses as licence_register
                return self._send(200, {
                    "version": licence_register.REGISTER_VERSION,
                    "register": licence_register.register_snapshot(),
                    "markdown": licence_register.render_markdown(),
                })
            if p == "/everyday":
                # Everyday-Law topic catalogue (Phase 7, Task 4). Read-only and
                # grounded: public like /search, since it only ever serves
                # published, firewalled law text.
                from core.legal.plain import list_topics
                return self._send(200, {"topics": list_topics()})
            if p.startswith("/everyday/"):
                # Grounded plain-language explainer for one topic. The
                # controlling instrument + currency are attached; an
                # ungrounded topic returns an honest notice, never a guess.
                from core.legal import plain
                from core.legal import temporal as temporal_mod
                topic = unquote(p.rsplit("/", 1)[1]).strip().lower()
                conn = engine().storage.conn
                commercial = commercial_requested(q, self.headers)
                try:
                    out = plain.build_explainer(
                        topic, retrieve=everyday_retrieve,
                        status_fn=lambda did: temporal_mod.status_report(conn, did),
                        lookup=citation_engine.make_lookup(conn),
                        commercial=commercial)
                except KeyError:
                    return self._send(404, {
                        "error": f"unknown topic: {topic}",
                        "topics": [t["key"] for t in plain.list_topics()]})
                # An ungrounded topic is demand the corpus cannot meet: record
                # it (deduped by topic) so Ask-to-Acquire can go and find the
                # missing instrument. Best-effort: never fail the reply.
                if not out.get("grounded"):
                    try:
                        spec = plain.TOPICS.get(topic) or {}
                        gap_queue.record_gap(spec.get("label") or topic,
                                             asker="everyday")
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("everyday gap record failed: %s", exc)
                out["ok"] = True
                return self._send(200, out)
            if p == "/legal/health":
                # Read-only knowledge-health snapshot (Phase 6 T3). Same
                # X-Legal-Token gate as the other reporting surfaces; opens the
                # DB read-only and never writes.
                if not self._authed():
                    return self._send(401, {"error": "unauthorized"})
                from core.legal.health import legal_health
                kwargs = {}
                raw = (q.get("stale_days") or [None])[0]
                if raw is not None:
                    try:
                        kwargs["stale_days"] = int(raw)
                    except (TypeError, ValueError):
                        pass
                return self._send(200, legal_health(DB, **kwargs))
            if p == "/dashboard":
                return self._send(200, DASHBOARD_HTML, "text/html; charset=utf-8")
            if p == "/ghalii/policy":
                return self._send(403, {"error": "ghalii.org is AI-input "
                                        "restricted and disabled",
                                        "reason": restricted.reason(
                                            "ghalii.org")})
            if p == "/ghalii/discover":
                return self._send(403, {"error": "ghalii.org is AI-input "
                                        "restricted and disabled",
                                        "reason": restricted.reason(
                                            "ghalii.org")})
            if p == "/admit":
                return self._send(200, classify_source(
                    q.get("url", [""])[0], REGISTRY.allowlist()))
            if p.startswith("/impact/"):
                return self._send(200, impact_analysis(
                    int(p.rsplit("/", 1)[1]), graph()))
            if p == "/oai/identify":
                return self._send(200, src_oai.identify(
                    http_fetch, q.get("base", [PARL_OAI])[0]))
            if p == "/oai/records":
                return self._send(200, src_oai.harvest(
                    http_fetch, q.get("base", [PARL_OAI])[0],
                    max_records=int(q.get("limit", ["20"])[0])))
            if p == "/repository/collections":
                return self._send(200, {"collections": src_dspace.collections(
                    http_fetch, q.get("base", [PARL_REPO])[0])})
            if p == "/harvest/jobs":
                return self._send(200, {"jobs": JOBS.list()})
            if p.startswith("/harvest/job/"):
                return self._send(200, JOBS.get(p.rsplit("/", 1)[1]) or
                                  {"error": "not found"})
            if p == "/monitor":
                return self._send(200, src_monitor.check_all(
                    http_fetch, state_path=MONITOR_STATE))
            if p == "/redteam":
                return self._send(200, run_redteam())
            if p == "/quality":
                return self._send(200, quality_metrics(
                    engine(), REGISTRY.list(), _load_json(REPO_SEEN, {})))
            if p.startswith("/matter/"):
                mid = p.rsplit("/", 1)[1]
                m = MATTERS.get(mid)
                if m is None:
                    return self._send(404, {"error": "matter not found"})
                details = []
                for d in m.get("documents", []):
                    rec = engine().get(d)
                    if rec:
                        rec.pop("content", None)
                        details.append(rec)
                return self._send(200, {**m, "document_details": details})
            if p == "/matters":
                return self._send(200, {"matters": MATTERS.list()})
            if p == "/injection/scan":
                return self._send(200, injection_scan(
                    (q.get("text") or [""])[0]))
            if p == "/injection/audit":
                rows = engine().list_documents(limit=1000)
                flagged = []
                for r in rows:
                    d = engine().get(r["id"])
                    if not d:
                        continue
                    res = injection_scan(d.get("content", ""))
                    if res["suspected"]:
                        flagged.append({"id": d["id"], "markers": res["markers"]})
                return self._send(200, {"documents": len(rows),
                                        "flagged": flagged})
            if p == "/privacy/audit":
                rows = engine().list_documents(limit=1000)
                full = [engine().get(r["id"]) for r in rows]
                return self._send(200, privacy_audit([d for d in full if d]))
            if p == "/research":
                memo = run_research(q.get("q", [""])[0], engine(), graph=graph())
                src_research_audit.record(
                    RESEARCH_AUDIT, q.get("q", [""])[0], memo,
                    user=(q.get("user") or ["operator"])[0])
                return self._send(200, memo)
            if p == "/research/history":
                return self._send(200, {"history": src_research_audit.history(
                    RESEARCH_AUDIT, limit=int(q.get("limit", ["20"])[0]))})
            if p == "/search":
                mode = (q.get("mode") or ["hybrid"])[0]
                limit = _clamp_limit((q.get("limit") or [_LIMIT_DEFAULT])[0])
                commercial = commercial_requested(q, self.headers)
                idx = embedding_index() if mode in ("hybrid", "keyword") else None
                if mode == "keyword":
                    idx = None  # keyword is lexical-only and must stay fast
                results, mode = search_dispatch(
                    q.get("q", [""])[0], mode=mode, limit=limit,
                    storage=engine().storage, embed_index=idx,
                    commercial=commercial)
                payload = {"results": results}
                payload["mode"] = mode
                return self._send(200, payload)
            if p == "/documents":
                commercial = commercial_requested(q, self.headers)
                docs = engine().list_documents(
                    jurisdiction=(q.get("jurisdiction") or [None])[0],
                    status=(q.get("status") or [None])[0],
                    limit=int(q.get("limit", ["50"])[0]),
                    commercial=commercial)
                for d in docs:
                    d["status"] = infer_status(d)   # reflect repealed law
                _annotate_temporal(engine().storage, docs)
                return self._send(200, {"documents": docs})
            if p.startswith("/document/"):
                commercial = commercial_requested(q, self.headers)
                doc = engine().get(int(p.rsplit("/", 1)[1]),
                                   commercial=commercial)
                if doc is None:
                    return self._send(404, {"error": "document not found"})
                _annotate_temporal(engine().storage, [doc])
                return self._send(200, doc)
            if p.startswith("/versions/"):
                doc_id = int(p.rsplit("/", 1)[1])
                commercial = commercial_requested(q, self.headers)
                if engine().get(doc_id, commercial=commercial) is None:
                    return self._send(404, {"error": "document not found"})
                return self._send(200, {"versions": engine().list_versions(doc_id)})
            if p.startswith("/integrity/"):
                doc_id = int(p.rsplit("/", 1)[1])
                commercial = commercial_requested(q, self.headers)
                if engine().get(doc_id, commercial=commercial) is None:
                    return self._send(404, {"error": "document not found"})
                return self._send(200, engine().verify_integrity(doc_id))
            return self._send(404, {"error": "not found"})
        except Exception as exc:
            return self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

    def do_POST(self):
        if self.path.split("?", 1)[0] == "/ingest/file":
            return self._handle_ingest_file()
        _p = self.path.split("?", 1)[0]
        if _p == "/gaps" or _p.startswith("/gaps/"):
            return self._handle_gap_post()
        if self.path in ("/graph/node", "/graph/edge", "/acquire",
                         "/acquire/ghalii", "/registry/add",
                         "/registry/remove", "/registry/enable",
                         "/matter", "/extract", "/timeline", "/evidence",
                         "/argument", "/diff", "/acquire/source",
                         "/acquire/pdf", "/harvest/oai", "/harvest/rotate",
                         "/repository/acquire", "/harvest/job",
                         "/repository/monitor", "/graph/backfill", "/backup",
                         "/redact", "/privilege", "/matter/link",
                         "/harvest/cycle", "/citations/verify"):
            if not self._authed():
                return self._send(401, {"error": "unauthorized"})
            try:
                n = int(self.headers.get("Content-Length", 0))
                if n > 5_000_000:
                    return self._send(413, {"error": "payload too large"})
                body = json.loads(self.rfile.read(n) or b"{}")
                if self.path == "/graph/node":
                    graph().add_node(body["id"], body.get("kind", "doc"),
                                     body.get("label", ""), body.get("props"),
                                     body.get("provenance"))
                    return self._send(200, {"ok": True})
                if self.path == "/graph/edge":
                    added = graph().add_edge(body["src"], body["dst"],
                                             body["relation"], body.get("provenance"))
                    return self._send(200, {"ok": True, "added": added})
                if self.path == "/diff":
                    d = diff_texts(body.get("old", ""), body.get("new", ""))
                    return self._send(200, {"diff": d,
                                            "changed_areas": classify_changes(d)})
                if self.path == "/matter/link":
                    ok = MATTERS.add_document(body.get("matter_id", ""),
                                              body.get("doc_id"))
                    return self._send(200, {"ok": ok})
                if self.path == "/matter":
                    return self._send(200, MATTERS.create(
                        body.get("name", "matter"),
                        parties=body.get("parties", ""), court=body.get("court", ""),
                        description=body.get("description", "")))
                if self.path == "/extract":
                    return self._send(200, extract_entities(body.get("text", "")))
                if self.path == "/timeline":
                    return self._send(200, {"timeline": build_timeline(
                        body.get("events", []))})
                if self.path == "/evidence":
                    return self._send(200, {"matrix": evidence_matrix(
                        body.get("items", []))})
                if self.path == "/argument":
                    return self._send(200, argument_map(
                        body.get("claim", ""), body.get("facts"),
                        body.get("rule", ""), body.get("authorities"),
                        body.get("counterargument", ""), body.get("rebuttal", "")))
                if self.path == "/repository/acquire" or self.path == "/repository/monitor":
                    return self._send(200, self._acquire_dspace(body))
                if self.path == "/harvest/job":
                    job = JOBS.submit("harvest_rotate", self._harvest_rotate, body)
                    return self._send(200, {"job_id": job["id"],
                                            "status": job["status"]})
                if self.path == "/harvest/rotate":
                    return self._send(200, self._harvest_rotate(body))
                if self.path == "/redact":
                    text = body.get("text", "")
                    return self._send(200, {"redacted": redact(text, body.get("patterns")),
                                            "pii": find_pii(text)})
                if self.path == "/privilege":
                    return self._send(200, detect_privilege(body.get("text", "")))
                if self.path == "/backup":
                    return self._send(200, self._backup())
                if self.path == "/citations/verify":
                    # Verify a generated answer's citations; optionally persist
                    # the proposition to the belief ledger when every citation
                    # verifies (Phase 2 Tasks 1 + 3).
                    conn = engine().storage.conn
                    report = citation_engine.verify_text(
                        body.get("text", ""), citation_engine.make_lookup(conn))
                    if body.get("record") and report.get("all_verified"):
                        authorities = sorted(
                            {c["display"] for c in report["citations"]
                             if c["status"] == citation_engine.VERIFIED})
                        citation_engine.record_proposition(
                            conn, body.get("text", ""), authorities,
                            status="verified")
                        report["recorded"] = True
                    return self._send(200, report)
                if self.path == "/graph/backfill":
                    return self._send(200, build_from_corpus(engine(), graph()))
                if self.path == "/harvest/oai":
                    return self._send(200, self._harvest_oai(body))
                if self.path == "/acquire/pdf":
                    return self._send(200, self._acquire_pdf(body))
                if self.path == "/acquire/source":
                    return self._send(200, self._acquire_source(body))
                if self.path == "/acquire/ghalii":
                    return self._send(200, self._acquire_ghalii(body))
                if self.path.startswith("/registry/"):
                    return self._send(200, self._registry_action(
                        self.path.rsplit("/", 1)[1], body))
                if self.path == "/harvest/cycle":
                    return self._send(200, self._harvest_cycle(body))
                return self._send(200, acquire_sources(
                    body.get("urls", []), engine(),
                    metadata_by_url=body.get("metadata_by_url"),
                    rights_gate=rights_gate))
            except Exception as exc:
                return self._send(500, {"error": f"{type(exc).__name__}: {exc}"})
        if self.path != "/ingest":
            return self._send(404, {"error": "not found"})
        if not self._authed():
            return self._send(401, {"error": "unauthorized"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            operator = bool(body.get("operator_provided"))
            decision = gated_admit(body)
            if not decision["admitted"] and not operator:
                return self._send(403, {"error": "source not admitted",
                                        "reason": decision["reason"],
                                        "host": decision.get("host")})
            kwargs = {k: body[k] for k in _DOC_FIELDS if k in body}
            kwargs.setdefault("jurisdiction", "ghana")
            kwargs["year"] = int(kwargs.get("year", 0))
            cls = classify_document(body, body.get("content", ""))
            if not kwargs.get("type"):
                kwargs["type"] = cls["type"]
            if not kwargs.get("status"):
                kwargs["status"] = cls["status"]
            from core.legal.validation import validate_ingest
            ok, reason = validate_ingest(
                {"title": kwargs.get("title")}, body.get("content", ""))
            if not ok:
                logger.warning("ingest validation rejected: %s title=%r",
                               reason, kwargs.get("title"))
                return self._send(422, {"error": f"validation failed: {reason}",
                                        "store_mode": "none", "reason": reason})
            existing = find_by_citation(kwargs.get("citation"))
            if existing is not None:
                return self._send(200, {"ok": True, "duplicate": True,
                                        "existing_id": existing,
                                        "dedup": "citation"})
            doc = LegalDocument(**kwargs)
            errors = validate_document(doc)
            if errors:
                return self._send(400, {"errors": errors})
            gate = rights_gate(doc, body.get("content", ""), operator=operator)
            if not gate["allowed"]:
                return self._send(403, {"error": "rights gate: not ingestable",
                                        "store_mode": "none",
                                        "rights_basis": gate["rights_basis"]})
            refs = body.get("constitution_refs") or None
            if refs:
                refs = [tuple(r) for r in refs]
            doc_id = engine().ingest(doc, gate["content"],
                                     constitution_refs=refs,
                                     store_mode=gate["store_mode"],
                                     rights_basis=gate["rights_basis"])
            return self._send(200, {"ok": True, "id": doc_id,
                                    "store_mode": gate["store_mode"],
                                    "rights_basis": gate["rights_basis"],
                                    "content_chars": len(gate["content"]),
                                    "injection": injection_scan(
                                        body.get("content", ""))})
        except Exception as exc:
            return self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

    def _handle_ingest_file(self):
        if not self._authed():
            return self._send(401, {"error": "unauthorized"})
        from urllib.parse import urlparse, parse_qs, unquote
        from core.legal.schema import LegalDocument, validate_document
        q = parse_qs(urlparse(self.path).query)
        n = int(self.headers.get("Content-Length", 0))
        MAX_UPLOAD = 30 * 1024 * 1024
        if n > MAX_UPLOAD:
            return self._send(413, {"error": "upload too large",
                                    "max_bytes": MAX_UPLOAD})
        data = self.rfile.read(n) if n else b""
        ctype = self.headers.get("Content-Type", "")
        if data[:4] == b"%PDF" or "pdf" in ctype.lower():
            text = pdf_extract_ocr(data)
        else:
            text = data.decode("utf-8", "replace")
        if not text.strip():
            return self._send(400, {"error": "no extractable text"})
        title = q.get("title", ["untitled"])[0]
        citation = q.get("citation", [title[:80]])[0]
        source_url = q.get("source_url", [""])[0]
        operator = q.get("operator_provided", ["0"])[0].lower() in ("1", "true", "yes")
        decision = gated_admit({"source_url": source_url})
        if not decision["admitted"] and not operator:
            return self._send(403, {"error": "source not admitted",
                                    "reason": decision["reason"]})
        from core.legal.validation import validate_ingest
        ok, reason = validate_ingest({"title": title}, text)
        if not ok:
            logger.warning("ingest/file validation rejected: %s title=%r",
                           reason, title)
            return self._send(422, {"error": f"validation failed: {reason}",
                                    "store_mode": "none", "reason": reason})
        existing = find_by_citation(citation)
        if existing is not None:
            return self._send(200, {"duplicate": True, "existing_id": existing})
        kwargs = {"jurisdiction": q.get("jurisdiction", ["ghana"])[0],
                  "court": q.get("court", [""])[0],
                  "year": int((q.get("year", ["0"])[0] or "0")),
                  "citation": citation, "title": title,
                  "type": q.get("type", ["other"])[0],
                  "status": q.get("status", ["current"])[0],
                  "source_url": source_url}
        errors = validate_document(LegalDocument(**kwargs))
        if errors:
            return self._send(400, {"errors": errors})
        try:
            doc_id = gated_ingest(LegalDocument(**kwargs), text,
                                  operator=operator)
        except RightsGateError as exc:
            return self._send(403, {"error": str(exc), "store_mode": "none"})
        if operator:
            prov = {"origin": "operator_upload", "authority": "operator-provided",
                    "operator_curated": True}
        elif decision["admitted"]:
            prov = decision
        else:
            prov = {"origin": "unknown"}
        return self._send(200, {"ok": True, "id": doc_id, "chars": len(text),
                                "provenance": prov})

    def _cursor(self, body=None):
        """Load the (migrated) rotation cursor, optionally from the request."""
        cursor = (body or {}).get("cursor")
        if not isinstance(cursor, dict):
            cursor = harvest_mod.load_cursor(HARVEST_CURSOR)
        return harvest_mod.migrate_cursor(cursor)

    def _acquire_dspace(self, body):
        from core.legal.schema import LegalDocument, validate_document
        base = body.get("base", PARL_REPO)
        limit = int(body.get("limit", 5))
        refresh = bool(body.get("refresh"))
        rotate = bool(body.get("rotate", True))
        cursor = self._cursor(body)
        lane = cursor.get("dspace") or harvest_mod.default_cursor()["dspace"]
        if rotate:
            # Query x year x page rotation: advance the persisted cursor so each
            # run explores a different slice instead of page 0 of one query.
            query, page, year, next_lane = harvest_mod.choose(lane)
        else:
            query = body.get("query")
            page = int(body.get("page", 0) or 0)
            year = body.get("year")
            next_lane = lane
        search_url = f"{base}/server/api/discover/search/objects"
        if not ROBOTS.allows(search_url):
            return {"error": "robots.txt disallows search endpoint",
                    "query": query, "page": page, "year": year}
        try:
            items = src_dspace.search_objects(http_fetch, base, query=query,
                                              size=limit, page=page, year=year)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {exc}",
                    "query": query, "page": page, "year": year}
        cursor["dspace"] = next_lane
        if rotate:
            harvest_mod.save_cursor(HARVEST_CURSOR, cursor)
        seen = _load_json(REPO_SEEN, {})
        services = {
            "item_bitstreams": src_dspace.item_bitstreams,
            "bitstream_url": src_dspace.bitstream_url,
            "admit": gated_admit,
            "find_citation": find_by_citation,
            "classify": classify_document,
            "validate": validate_document,
            "make_document": LegalDocument,
            "ingest": gated_ingest,
            "upgrade_stub": self._upgrade_stub,
            "fetch": http_fetch,
            "fetch_bytes": http_fetch_bytes,
            "extract_text": pdf_extract_ocr,
            "allowlist": REGISTRY.allowlist(),
        }
        results = harvest_mod.harvest_items(items, base=base, seen=seen,
                                            services=services, refresh=refresh)
        _save_json(REPO_SEEN, seen)
        return {"source": "parliament-dspace", "query": query, "page": page,
                "year": year, "items": len(items), "results": results,
                "cursor": cursor}

    def _backup(self):
        import glob
        ts = time.strftime("%Y%m%d-%H%M%S")
        dest = os.path.join(ROOT, "data", "backups", ts)
        os.makedirs(dest, exist_ok=True)
        copied = []
        for name in ("legal_brain.db", "legal_graph.db", "sources.json",
                     "matters.json", "repo_seen.json"):
            src = os.path.join(ROOT, "data", name)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(dest, name))
                copied.append(name)
        return {"ok": True, "backup_dir": dest, "files": copied}

    def _harvest_cycle(self, body):
        """Run one bounded weekly harvest cycle (Task 8).

        Guarded by a non-blocking lock so overlapping scheduler triggers are
        refused (HTTP-200 with ``skipped``) instead of hammering sources. The
        bounded limits keep a single run short and polite.
        """
        if not _CYCLE_LOCK.acquire(blocking=False):
            return {"ok": False, "skipped": "harvest cycle already running"}
        try:
            from scripts.harvest_cycle import run_live
            report = run_live(
                discover_limit=int(body.get("limit", 5)),
                stub_limit=int(body.get("stub_limit", 3)),
                delay=float(body.get("delay", 1.0)),
                dry_run=bool(body.get("dry_run")),
                no_backup=bool(body.get("no_backup")),
                db=DB)
            report["ok"] = True
            return report
        finally:
            _CYCLE_LOCK.release()

    def _handle_gap_post(self):
        """Ask-to-Acquire gap-queue endpoints (Phase 7, Task 3).

        ``POST /gaps`` records an unanswered question (dedup by topic);
        ``POST /gaps/{id}/acquire`` runs one bounded acquisition pass;
        ``POST /gaps/{id}/notified`` records the one-shot notification guard.
        All are token-gated; the record path is best-effort from CT 111.
        """
        if not self._authed():
            return self._send(401, {"error": "unauthorized"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            if n > 1_000_000:
                return self._send(413, {"error": "payload too large"})
            body = json.loads(self.rfile.read(n) or b"{}")
            path = self.path.split("?", 1)[0]
            if path == "/gaps":
                question = (body.get("question") or "").strip()
                if not question:
                    return self._send(400, {"error": "question is required"})
                gap_id = gap_queue.record_gap(question, asker=body.get("asker"))
                gap = gap_queue.get(gap_id) or {}
                return self._send(200, {"ok": True, "id": gap_id,
                                        "status": gap.get("status"),
                                        "domain": gap.get("domain")})
            parts = path.strip("/").split("/")
            if len(parts) == 3 and parts[0] == "gaps":
                try:
                    gap_id = int(parts[1])
                except (TypeError, ValueError):
                    return self._send(400, {"error": "invalid gap id"})
                if parts[2] == "acquire":
                    from scripts.gap_acquire import run_for_gap
                    out = run_for_gap(
                        gap_id,
                        per_source=int(body.get("per_source", 5)),
                        delay=float(body.get("delay", 0.5)),
                        no_backup=bool(body.get("no_backup")))
                    out["ok"] = not out.get("error")
                    return self._send(200, out)
                if parts[2] == "notified":
                    gap_queue.mark_notified(gap_id)
                    return self._send(200, {"ok": True, "id": gap_id})
            return self._send(404, {"error": "not found"})
        except Exception as exc:  # noqa: BLE001
            return self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

    def _ref_services(self):
        """Effectful callables shared by the reference-record processors."""
        from core.legal.schema import LegalDocument, validate_document
        return {
            "admit": gated_admit,
            "allowlist": REGISTRY.allowlist(),
            "find_citation": find_by_citation,
            "classify": classify_document,
            "validate": validate_document,
            "make_document": LegalDocument,
            "ingest": gated_ingest,
            "upgrade_stub": self._upgrade_stub,
        }

    def _upgrade_stub(self, existing_id, doc, content, metadata=None) -> dict:
        """Upgrade an existing metadata-only stub with newly-resolved text.

        Idempotent: a document that already carries body text is left alone
        (``upgraded=False``), so re-ingesting a full record is a no-op. The
        rights gate decides the store mode for the resolved text.
        """
        return upgrade_stub(existing_id, doc, content, metadata=metadata)

    @staticmethod
    def _oai_reference(rec, base):
        import re
        title = rec.get("title") or ""
        if is_junk_title(title):
            logger.debug("OAI harvest skipped junk title: %r", title)
            return None
        year = 0
        m = re.search(r"(19|20)\d{2}", (rec.get("date") or "") + title)
        if m:
            year = int(m.group(0))
        return {
            # The OAI endpoint is the authoritative, allowlisted source of the
            # record. Citation defaults to the title so it dedups against the
            # same item already fetched via the DSpace REST lane.
            "url": base,
            "title": title,
            "year": year,
            "type": "",  # let the classifier infer from the title
            "court": "Parliament",
            "content": "",  # resolved from the DSpace bitstream, never the abstract
            # Persistent handle(s) let the full-text resolver find the item.
            "identifiers": rec.get("identifiers") or [],
            "rights_metadata": rec.get("rights_metadata") or None,
        }

    def _oai_full_text_resolver(self):
        """Build a ``ref -> text`` resolver: handle → item UUID → bitstream.

        Uses the same DSpace REST source adapter as the discovery lane, so an
        OAI record resolves to the ORIGINAL PDF and is OCR-extracted exactly
        like a directly-discovered item. Network/parse failures degrade to
        ``{"error": ...}`` (metadata-only fallback), never an exception.
        """
        services = {
            "item_bitstreams": src_dspace.item_bitstreams,
            "bitstream_url": src_dspace.bitstream_url,
            "admit": gated_admit,
            "allowlist": REGISTRY.allowlist(),
            "fetch": http_fetch,
            "fetch_bytes": http_fetch_bytes,
            "extract_text": pdf_extract_ocr,
        }

        def resolve(ref):
            handle = src_dspace.extract_handle(ref.get("identifiers") or [])
            if not handle:
                return {"error": "no handle"}
            try:
                item_uuid = src_dspace.item_by_handle(http_fetch, PARL_REPO,
                                                      handle)
            except Exception as exc:  # noqa: BLE001
                return {"error": f"{type(exc).__name__}: {exc}"[:120]}
            if not item_uuid:
                return {"error": "handle unresolved"}
            return harvest_mod.resolve_item_text(services, PARL_REPO, item_uuid)

        return resolve

    def _harvest_oai(self, body):
        base = body.get("base", REPO_OAI)
        limit = int(body.get("limit", 50))
        rotate = bool(body.get("rotate", False))
        cursor = self._cursor(body)
        lane = cursor.get("oai") or {"token": None}
        token = lane.get("token")
        if not ROBOTS.allows(base):
            return {"error": "robots.txt disallows OAI endpoint", "token": token}
        try:
            page = (src_oai.resume(http_fetch, base, token) if token
                    else src_oai.list_records(http_fetch, base))
        except Exception as exc:  # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {exc}", "token": token}
        records = [r for r in page["records"]
                   if not r.get("deleted") and r.get("title")][:limit]
        refs = [self._oai_reference(r, base) for r in records]
        junk_skipped = sum(1 for ref in refs if ref is None)
        refs = [ref for ref in refs if ref is not None]
        if junk_skipped:
            logger.info("OAI harvest pre-filtered %d junk record(s)",
                        junk_skipped)
        # Full-text resolution is bounded and rate-limited: a single OAI batch
        # must not turn into an unbounded OCR crawl. Records that cannot be
        # resolved fall back to an honest metadata-only (search_only) entry.
        resolve = body.get("resolve_full_text", True)
        max_ft = body.get("max_full_text")
        max_ft = int(max_ft) if max_ft is not None else 5
        delay = float(body.get("delay", 0.5))
        results = harvest_src.process_references(
            refs, services=self._ref_services(),
            full_text=self._oai_full_text_resolver() if resolve else None,
            max_full_text=max_ft if resolve else None,
            delay=delay if resolve else 0.0)
        cursor["oai"] = {"token": page.get("resumptionToken")}
        if rotate:
            harvest_mod.save_cursor(HARVEST_CURSOR, cursor)
        return {"source": "parliament-oai", "records": len(records),
                "junk_skipped": junk_skipped,
                "token": page.get("resumptionToken"), "results": results,
                "cursor": cursor}

    def _acquire_pdf(self, body):
        from core.legal.schema import LegalDocument, validate_document
        url = body.get("url", "")
        decision = gated_admit({"source_url": url})
        if not decision["admitted"]:
            return {"url": url, "rejected": decision["reason"]}
        if not ROBOTS.allows(url):
            return {"url": url, "rejected": "robots.txt disallows"}
        try:
            data = http_fetch_bytes(url)
        except Exception as exc:  # noqa: BLE001
            return {"url": url, "error": f"{type(exc).__name__}: {exc}"}
        text = pdf_extract_ocr(data)
        if not text:
            return {"url": url, "error": "no extractable text"}
        title = body.get("title") or url.rsplit("/", 1)[-1]
        citation = body.get("citation") or title[:80]
        existing = find_by_citation(citation)
        if existing is not None:
            return {"url": url, "duplicate": True, "existing_id": existing}
        kwargs = {"jurisdiction": body.get("jurisdiction", "ghana"),
                  "court": body.get("court", ""),
                  "year": int(body.get("year") or 0), "citation": citation,
                  "title": title, "type": body.get("type") or "act",
                  "status": body.get("status") or "current", "source_url": url}
        errors = validate_document(LegalDocument(**kwargs))
        if errors:
            return {"url": url, "error": "validation", "errors": errors}
        gate = rights_gate(LegalDocument(**kwargs), text)
        if not gate["allowed"]:
            return {"url": url, "rejected": "rights gate",
                    "store_mode": "none", "rights_basis": gate["rights_basis"]}
        doc_id = engine().ingest(LegalDocument(**kwargs), gate["content"],
                                 store_mode=gate["store_mode"],
                                 rights_basis=gate["rights_basis"])
        return {"url": url, "id": doc_id, "chars": len(gate["content"]),
                "store_mode": gate["store_mode"],
                "version": body.get("version", ""), "citation": citation}

    def _acquire_source(self, body):
        from core.legal.schema import LegalDocument, validate_document
        domain = body.get("domain", "")
        limit = int(body.get("limit", 5))
        res = src_sitemap.acquire_reference(engine(), domain, http_fetch, limit=limit)
        ingested = []
        for item in res.get("fetched", []):
            meta = item.get("meta")
            if not meta:
                ingested.append(item)
                continue
            url = item["url"]
            decision = gated_admit({**meta, "source_url": url})
            if not decision["admitted"]:
                ingested.append({"url": url, "rejected": decision["reason"]})
                continue
            if not ROBOTS.allows(url):
                ingested.append({"url": url, "rejected": "robots.txt disallows"})
                continue
            citation = meta.get("citation") or meta.get("title", "")[:80]
            if find_by_citation(citation):
                ingested.append({"url": url, "duplicate": True})
                continue
            kwargs = {"jurisdiction": "ghana", "court": meta.get("court") or "",
                      "year": int(meta.get("year") or 0), "citation": citation,
                      "title": meta.get("title", ""),
                      "type": meta.get("type") or "other",
                      "status": "current", "source_url": url}
            if validate_document(LegalDocument(**kwargs)):
                ingested.append({"url": url, "skipped": "validation"})
                continue
            gate = rights_gate(LegalDocument(**kwargs), meta.get("content", ""))
            if not gate["allowed"]:
                ingested.append({"url": url, "rejected": "rights gate",
                                 "store_mode": "none"})
                continue
            doc_id = engine().ingest(LegalDocument(**kwargs), gate["content"],
                                     store_mode=gate["store_mode"],
                                     rights_basis=gate["rights_basis"])
            ingested.append({"url": url, "id": doc_id,
                             "store_mode": gate["store_mode"],
                             "license": meta.get("license")})
        return {"domain": domain, "discovered": res.get("discovered"),
                "policy": res.get("policy"), "ingested": ingested}

    def _registry_action(self, action, body):
        if action == "add":
            try:
                return REGISTRY.add(body)
            except ValueError as exc:
                return {"error": str(exc)}
        if action == "remove":
            return {"removed": REGISTRY.remove(body.get("id", ""))}
        if action == "enable":
            try:
                return REGISTRY.set_enabled(body["id"], bool(body.get("enabled", True)))
            except KeyError:
                return {"error": "unknown source id"}
        return {"error": "unknown registry action"}

    def _acquire_ghalii(self, body):
        """Harvest GhaLII legislation at reference tier.

        GhaLII detail pages return Cloudflare 403, but the public
        ``/legislation/`` listing is fetchable and carries title + URL, so the
        harvester walks the listing (robots-allowed) and stores metadata only
        (content-signal ``search=yes`` → index tier).
        """
        if restricted.is_restricted("ghalii.org"):
            return {"source": "ghalii-legislation",
                    "error": "ghalii.org is AI-input restricted and disabled",
                    "reason": restricted.reason("ghalii.org")}
        limit = int(body.get("limit", 50))
        rotate = bool(body.get("rotate", False))
        cursor = self._cursor(body)
        lane = cursor.get("ghalii") or {"page": 1}
        page_no = max(1, int(lane.get("page", 1)))
        listing = body.get("listing", ghalii.LISTING_LEGISLATION)
        url = listing if page_no <= 1 else f"{listing}?page={page_no}"
        if not ROBOTS.allows(url):
            return {"source": "ghalii-legislation", "page": page_no,
                    "error": "robots.txt disallows listing"}
        try:
            html = http_fetch(url)
        except Exception as exc:  # noqa: BLE001
            return {"source": "ghalii-legislation", "page": page_no,
                    "error": f"{type(exc).__name__}: {exc}"}
        policy = ghalii.policy(http_fetch)
        entries = ghalii.parse_legislation_listing(html)
        refs = [ghalii.listing_reference_record(e, policy) for e in entries][:limit]
        results = harvest_src.process_references(refs,
                                                 services=self._ref_services())
        pages = ghalii.listing_page_numbers(html)
        nxt_page = page_no + 1 if page_no < max(pages or [page_no]) else 1
        cursor["ghalii"] = {"page": nxt_page}
        if rotate:
            harvest_mod.save_cursor(HARVEST_CURSOR, cursor)
        return {"source": "ghalii-legislation", "page": page_no,
                "discovered": len(entries), "policy": policy,
                "results": results, "cursor": cursor}

    # Per-lane batch sizes: DSpace fetches + OCRs PDFs (slow), so it takes a
    # small batch; OAI/GhaLII are metadata-only and can take a large one.
    ROTATION_LIMITS = {"dspace": 10, "oai": 100, "ghalii": 60}

    def _harvest_rotate(self, body):
        """Advance the source rotation and harvest the next lane."""
        cursor = self._cursor(body)
        source, cursor = harvest_src.choose_source(
            cursor, harvest_src.SOURCE_ROTATION)
        if restricted.is_restricted(source.get("domain")
                                    or source.get("base")):
            return {"source": source.get("id"),
                    "error": "source is AI-input restricted and disabled",
                    "reason": restricted.reason(source.get("domain")
                                                or source.get("base"))}
        limit = int(body.get("limit")
                    or self.ROTATION_LIMITS.get(source["kind"], 10))
        call = {**body, "limit": limit, "rotate": True, "cursor": cursor}
        kind = source["kind"]
        if kind == "dspace":
            res = self._acquire_dspace(call)
        elif kind == "oai":
            res = self._harvest_oai(call)
        elif kind == "ghalii":
            res = self._acquire_ghalii(call)
        else:
            res = {"error": f"unknown source kind: {kind}"}
        # Handlers return their advanced lane cursor; keep the source index.
        cur = res.pop("cursor", None) if isinstance(res, dict) else None
        cur = harvest_mod.migrate_cursor(cur) if cur else self._cursor(call)
        cur["source_index"] = cursor["source_index"]
        harvest_mod.save_cursor(HARVEST_CURSOR, cur)
        if isinstance(res, dict):
            res["source"] = source["id"]
        return res

    def log_message(self, *a):
        pass


def _warmup_once():
    """Serve the first real /search without the cold-start cost.

    The embedding path loads the VM104 model over the tunnel (~0.9s first
    query after a restart, measured 2026-09-26); a background warmup query
    pays that once at boot so the first caller does not.
    """
    try:
        engine()
        # Build + use the real dense index (this thread-local is not shared
        # with request threads, but the embedding model load on VM104, the
        # ANN mmap and the SQLite page cache are process-wide).
        search_dispatch("warmup", mode="hybrid", limit=1,
                        storage=engine().storage,
                        embed_index=embedding_index())
        print("warmup complete", flush=True)
    except Exception as exc:  # noqa: BLE001 - warmup is best effort
        print(f"warmup skipped: {exc}", flush=True)


if __name__ == "__main__":
    print(f"kai-legal-brain on :{PORT}  db={DB}", flush=True)
    import threading
    threading.Thread(target=_warmup_once, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
