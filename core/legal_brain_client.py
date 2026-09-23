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


def search(query, limit=20, mode="or"):
    q = urllib.parse.quote(query or "")
    m = urllib.parse.quote(mode or "or")
    return _get(f"/search?q={q}&limit={limit}&mode={m}").get("results", [])


def documents(jurisdiction=None, status=None, limit=50):
    parts = [f"limit={limit}"]
    if jurisdiction:
        parts.append("jurisdiction=" + urllib.parse.quote(jurisdiction))
    if status:
        parts.append("status=" + urllib.parse.quote(status))
    return _get("/documents?" + "&".join(parts)).get("documents", [])


def get_document(doc_id):
    return _get(f"/document/{doc_id}")


def versions(doc_id):
    return _get(f"/versions/{doc_id}").get("versions", [])


def integrity(doc_id):
    return _get(f"/integrity/{doc_id}")


def stats():
    return _get("/stats")


def source_health():
    """Probe the legal brain's approved sources; returns {total, ok, down, results}."""
    return _get("/source-health", timeout=40)


def sources():
    return _get("/registry")


def monitor():
    """New-document monitor across configured sources (stateful on the brain)."""
    return _get("/monitor", timeout=120)
