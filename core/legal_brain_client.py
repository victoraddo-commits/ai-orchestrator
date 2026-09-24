"""Client for the Kai Legal Brain retrieval API (CT 100 `kai-legal-brain`).

Read-only helpers agents use to search the Ghana legal corpus.
    KAI_LEGAL_BRAIN_URL  (default http://192.168.1.100:8100)
"""
import json
import os
import urllib.parse
import urllib.request

BASE = os.environ.get("KAI_LEGAL_BRAIN_URL", "http://192.168.1.100:8100")


def _get(path, timeout=8):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as resp:
        return json.load(resp)


def _get_auth(path, timeout=8):
    """GET with the write token (for token-gated read endpoints like /beliefs)."""
    headers = {}
    tok = _token()
    if tok:
        headers["X-Legal-Token"] = tok
    req = urllib.request.Request(BASE + path, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


TOKEN_FILE = os.environ.get("KAI_LEGAL_TOKEN_FILE",
                            "/opt/ai-orchestrator/memory/legal_brain.token")


def _token():
    try:
        with open(TOKEN_FILE) as fh:
            return fh.read().strip()
    except Exception:
        return ""


def _post(path, obj, timeout=20):
    data = json.dumps(obj).encode()
    headers = {"Content-Type": "application/json"}
    tok = _token()
    if tok:
        headers["X-Legal-Token"] = tok
    req = urllib.request.Request(BASE + path, data=data, headers=headers,
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def submit_harvest(query: str = None, limit: int = 10) -> dict:
    """Submit an async legal-repository harvest (skips if one is running)."""
    try:
        jobs = _get("/harvest/jobs", timeout=15).get("jobs", [])
    except Exception:
        jobs = []
    if any(j.get("status") == "running" for j in jobs):
        return {"skipped": "harvest already running"}
    return _post("/harvest/job", {"query": query or "Act", "limit": limit})


def graph_backfill() -> dict:
    """Rebuild the legal knowledge graph from the corpus."""
    return _post("/graph/backfill", {}, timeout=60)


def harvest_cycle(limit=5, stub_limit=3, delay=1.0, dry_run=False,
                  timeout=900) -> dict:
    """Trigger one bounded weekly harvest cycle on the legal brain (Task 8).

    The brain runs discovery → full-text resolution → rights-gate → ingest →
    dedup plus a bounded stub re-harvest, and returns its run report. A
    non-blocking lock on the brain means an overlapping trigger comes back as
    ``{"ok": False, "skipped": "harvest cycle already running"}`` rather than
    running twice. Unreachable/HTTP failures return ``{"ok": False, ...}`` and
    are never raised, so the scheduler timer can report an honest failure.
    """
    import urllib.error
    body = {"limit": int(limit), "stub_limit": int(stub_limit),
            "delay": float(delay), "dry_run": bool(dry_run)}
    try:
        return _post("/harvest/cycle", body, timeout=timeout)
    except urllib.error.HTTPError as exc:
        try:
            payload = json.load(exc)
        except Exception:
            payload = {"error": f"HTTP {exc.code}"}
        return {"ok": False, **(payload if isinstance(payload, dict) else {})}
    except Exception as exc:  # noqa: BLE001 - scheduler must not crash
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def health():
    return _get("/health")


def ingest(title, content, citation="", court="", year=0, doc_type="",
           jurisdiction="ghana", source_url="", timeout=60):
    """Submit a document to the legal brain (requires the write token).

    Returns the service JSON verbatim. A 4xx (validation / source not
    admitted) is returned as ``{"ok": False, ...}`` rather than raised, so
    the Command Center can show the service's validation feedback.
    """
    import urllib.error
    body = {
        "title": title,
        "content": content,
        "citation": citation,
        "court": court,
        "year": int(year or 0),
        "type": doc_type,
        "jurisdiction": jurisdiction,
        "source_url": source_url,
    }
    try:
        return _post("/ingest", body, timeout=timeout)
    except urllib.error.HTTPError as exc:
        try:
            payload = json.load(exc)
        except Exception:
            payload = {"error": f"HTTP {exc.code}"}
        return {"ok": False, **(payload if isinstance(payload, dict) else {})}


SEARCH_MODES = ("hybrid", "phrase", "and", "or", "like")


def search(query, limit=20, mode="or", commercial=False):
    """Search the legal brain, forwarding ``mode`` to ``/search``.

    ``mode="hybrid"`` is the authority-aware primary path (BM25 + dense + RRF);
    its results also carry ``score``, ``confidence``, ``authority_level`` and
    ``bm25_rank``. The staged modes (phrase/and/or/like) remain available as the
    grounding fallback. An unknown mode is passed through and the brain falls
    back to ``or`` rather than erroring.

    ``commercial=True`` forwards the commercial-use gate (``commercial=1``), so
    the brain excludes content whose source licence forbids commercial use.
    The foundation/mission arm leaves it ``False``.
    """
    q = urllib.parse.quote(query or "")
    m = urllib.parse.quote(mode or "or")
    path = f"/search?q={q}&limit={limit}&mode={m}"
    if commercial:
        path += "&commercial=1"
    return _get(path).get("results", [])


def search_hybrid(query, limit=3, commercial=False):
    """Authority-aware hybrid retrieval (BM25 + dense + RRF), the primary path."""
    return search(query, limit=limit, mode="hybrid", commercial=commercial)


def verify_citations(text, record=False):
    """Verify a generated answer's citations against the corpus (Phase 2 T1).

    Returns CT100's report ``{citations, summary, all_verified}`` for the text.
    Transport failures raise, so the caller's hallucination firewall can fail
    open rather than blank a legal answer.
    """
    return _post("/citations/verify",
                 {"text": text or "", "record": bool(record)})


def beliefs(limit=50):
    """Read the belief ledger (verified propositions) from the legal brain."""
    return _get_auth(f"/beliefs?limit={int(limit)}").get("beliefs", [])


def status(doc_id):
    """Temporal / current-law status of one document (Phase 3 T3).

    Token-gated read on CT100 (``GET /status/{id}``); returns
    ``{document_id, found, title, status, reasons, as_of, cached}``.
    """
    return _get_auth(f"/status/{int(doc_id)}")


def statuses(ids):
    """Bulk temporal status (``GET /status?ids=...``); returns a list."""
    clean = [int(i) for i in (ids or [])]
    if not clean:
        return []
    joined = ",".join(str(i) for i in clean)
    return _get_auth(f"/status?ids={joined}").get("statuses", [])


def relations(doc_id):
    """Typed knowledge-graph relations of one document (``GET /relations/{id}``)."""
    return _get_auth(f"/relations/{int(doc_id)}")


def document_authority(doc_id):
    """Combined relations + temporal status for one document (Phase 3 T3/T4).

    Both halves are best-effort: a failure is reported in ``*_error`` rather
    than raised, so a caller can degrade gracefully.
    """
    out = {"document_id": doc_id}
    try:
        out["relations"] = relations(doc_id)
    except Exception as exc:  # noqa: BLE001 - caller degrades
        out["relations_error"] = f"{type(exc).__name__}: {exc}"
    try:
        out["status"] = status(doc_id)
    except Exception as exc:  # noqa: BLE001 - caller degrades
        out["status_error"] = f"{type(exc).__name__}: {exc}"
    return out


def documents(jurisdiction=None, status=None, limit=50):
    parts = [f"limit={limit}"]
    if jurisdiction:
        parts.append("jurisdiction=" + urllib.parse.quote(jurisdiction))
    if status:
        parts.append("status=" + urllib.parse.quote(status))
    return _get("/documents?" + "&".join(parts)).get("documents", [])


def get_document(doc_id, commercial=False):
    """Fetch one document; ``commercial=True`` applies the commercial-use gate."""
    path = f"/document/{doc_id}"
    if commercial:
        path += "?commercial=1"
    return _get(path)


def versions(doc_id, commercial=False):
    path = f"/versions/{doc_id}"
    if commercial:
        path += "?commercial=1"
    return _get(path).get("versions", [])


def integrity(doc_id, commercial=False):
    path = f"/integrity/{doc_id}"
    if commercial:
        path += "?commercial=1"
    return _get(path)


def stats():
    return _get("/stats")


def source_health():
    """Probe the legal brain's approved sources; returns {total, ok, down, results}."""
    return _get("/source-health", timeout=40)


def legal_health(stale_days=None, timeout=30):
    """Corpus knowledge-health snapshot (Phase 6 T3), token-gated read.

    Returns the brain's ``/legal/health`` payload (docs, with_content, by_area,
    temporal_counts, unknown_status, stale_authorities, integrity, suspect_docs).
    The default timeout is generous (30s): the brain re-checks integrity over the
    whole corpus on this call. Transport failures raise, so a caller can degrade.
    """
    path = "/legal/health"
    if stale_days is not None:
        path += f"?stale_days={int(stale_days)}"
    return _get_auth(path, timeout=timeout)


def coverage(threshold=None, timeout=20):
    """Legal-area coverage + harvest priorities (Phase 0/T3), token-gated read."""
    path = "/coverage"
    if threshold is not None:
        path += f"?threshold={int(threshold)}"
    return _get_auth(path, timeout=timeout)


def licences(timeout=15):
    """Source commercial-use licence register (token-gated read).

    Returns the brain payload ``{version, register, markdown}`` so the Command
    Center can render the register as a read-only table.
    """
    return _get_auth("/licences", timeout=timeout)


def sources():
    return _get("/registry")


def monitor():
    """New-document monitor across configured sources (stateful on the brain)."""
    return _get("/monitor", timeout=120)


# --- Ask-to-Acquire gap queue (Phase 7, Task 3) -----------------------------

def record_gap(question, asker=None, timeout=8):
    """Record an unanswered question as an acquisition gap on the brain.

    Best-effort by contract: callers run it off the answer path. The brain
    dedups by normalised topic and routes the question to a domain/ministry.
    """
    return _post("/gaps", {"question": question,
                           "asker": "" if asker is None else str(asker)},
                 timeout=timeout)


def list_gaps(status=None, limit=50, timeout=8):
    """List gaps (optionally by status) from the brain."""
    path = f"/gaps?limit={int(limit)}"
    if status:
        path += "&status=" + urllib.parse.quote(status)
    return _get_auth(path, timeout=timeout).get("gaps", [])


def acquire_gap(gap_id, per_source=5, delay=0.5, timeout=600):
    """Run one bounded acquisition pass for a gap; returns the brain outcome.

    A transport failure returns ``{"ok": False, ...}`` rather than raising, so
    the scheduler can report an honest failure.
    """
    import urllib.error
    body = {"per_source": int(per_source), "delay": float(delay)}
    try:
        return _post(f"/gaps/{int(gap_id)}/acquire", body, timeout=timeout)
    except urllib.error.HTTPError as exc:
        try:
            payload = json.load(exc)
        except Exception:
            payload = {"error": f"HTTP {exc.code}"}
        return {"ok": False, **(payload if isinstance(payload, dict) else {})}
    except Exception as exc:  # noqa: BLE001 - scheduler must not crash
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def mark_gap_notified(gap_id, timeout=8):
    """Record the one-shot notification guard on a gap."""
    return _post(f"/gaps/{int(gap_id)}/notified", {}, timeout=timeout)


# --- Everyday Law (Phase 7, Task 4) -----------------------------------------

def everyday_topics(timeout=8):
    """The Everyday-Law topic catalogue from the legal brain (``GET /everyday``)."""
    return _get("/everyday", timeout=timeout).get("topics", [])


def everyday(topic, timeout=20):
    """One grounded plain-language explainer (``GET /everyday/{topic}``).

    Returns the brain payload ``{grounded, verdict, instrument, currency,
    explainer, sources, disclaimer, notice}``. Transport/HTTP failures raise so
    the caller can degrade honestly rather than guess.
    """
    return _get(f"/everyday/{urllib.parse.quote(str(topic or ''))}",
                timeout=timeout)
