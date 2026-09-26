#!/usr/bin/env python3
"""KLAUS corpus growth cycle — one full discovery + ingest pass.

Run on a schedule by kai-corpus-growth.timer. For every ACTIVE source:
  1. discover candidate documents (domain handler, else generic)
  2. follow one extra hop from any landing page to find real document links
  3. download + ingest through the normal KLAUS pipeline (which now clamps
     jurisdiction and rejects HTML-as-PDF)

Idempotent: process_document dedupes by content hash, so re-running only adds
genuinely new material. Emits a summary line for the journal.
"""
from __future__ import annotations

import os
import re
import sys
import time
from urllib.parse import urljoin, urlparse

sys.path.insert(0, "/opt/ai-orchestrator")

# Load .env first so KLAUS_STORAGE_ROOT / DB settings match the API service
# (previously this script ran without it and wrote raw files to /var/lib
# while the API used /mnt/legal-brain — a split-brain storage tree).
try:
    from dotenv import load_dotenv as _ld
    _ld("/opt/ai-orchestrator/.env")
except Exception:  # noqa: BLE001
    pass

# DB env (mirrors the API service)
for _line in open("/etc/ai-orchestrator.env"):
    if _line.startswith("KLAUS_DB_PASSWORD="):
        os.environ["KLAUS_DB_PASSWORD"] = _line.split("=", 1)[1].strip()
os.environ.setdefault("KLAUS_DB_NAME", "klaus_db")
os.environ.setdefault("KLAUS_DB_USER", "klaus_user")

from core.klaus.db_manager import get_connection  # noqa: E402
from core.klaus.background_workers import (  # noqa: E402
    _gh_get, _resolve_url, HEADERS, discover_source_content,
    download_document_content, process_document,
)

DOC_RE = re.compile(r'href=["\']([^"\']+\.(?:pdf|docx?|odt))["\']', re.I)
LEG_RE = re.compile(
    r"\b(act|bill|legislation|judgment|judgement|gazette|statutory|l\.?i\.?|"
    r"constitution|regulation|amendment|ordinance|ruling|by-?law)\b", re.I)
SECOND_HOP_PATHS = ["", "/publications", "/downloads", "/documents", "/legal",
                    "/acts", "/bills", "/legislation", "/laws", "/resources",
                    "/regulations", "/guidelines", "/policies", "/reports",
                    "/public-notices", "/regulatory-framework"]
MAX_PER_SOURCE = int(os.environ.get("KAI_CORPUS_MAX_PER_SOURCE", "40"))


def _candidate_docs(url: str, domain: str) -> list[str]:
    urls: set[str] = set()
    try:
        from core.klaus.source_registry import is_blocked_domain
        if is_blocked_domain(domain):
            return []
    except Exception:  # noqa: BLE001
        pass
    # 1) the source's own discovery handler
    try:
        for d in discover_source_content(url, domain) or []:
            u = d.get("url")
            if u and (u.lower().endswith((".pdf", ".doc", ".docx")) or LEG_RE.search(u)):
                urls.add(u)
    except Exception as e:  # noqa: BLE001
        print(f"    handler error: {type(e).__name__}", flush=True)
    # 2) one extra hop from common landing paths
    for p in SECOND_HOP_PATHS:
        try:
            r = _gh_get(url.rstrip("/") + p, headers=HEADERS, timeout=20)
            if r.status_code != 200:
                continue
            for h in DOC_RE.findall(r.text):
                full = urljoin(url.rstrip("/") + p, h)
                if not LEG_RE.search(full):
                    continue
                try:
                    from core.klaus.source_registry import is_blocked_domain
                    if is_blocked_domain(urlparse(full).hostname or ""):
                        continue
                except Exception:  # noqa: BLE001
                    pass
                urls.add(full)
        except Exception:  # noqa: BLE001
            continue
    return list(urls)[:MAX_PER_SOURCE]


def _finish(source_id: int, source_url: str, domain: str, doc_id: int) -> None:
    """Post-ingest: quality agents, then embed regardless of status.

    Mirrors core.klaus.background_workers.process_discovered_documents so the
    scheduled path and the in-process worker behave identically.
    """
    from core.klaus.quality_agents import run_all_agents
    from core.klaus.vector_indexer import index_document_chunks
    try:
        result = run_all_agents(doc_id)
        overall = (result or {}).get("overall")
    except Exception:  # noqa: BLE001
        overall = None
    try:
        index_document_chunks(doc_id)
    except Exception:  # noqa: BLE001
        pass
    if overall and overall != "approved":
        print(f"    doc {doc_id}: quality={overall}", flush=True)


def main() -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id,url,domain FROM klaus_sources WHERE status='active' ORDER BY id")
    sources = cur.fetchall()
    print(f"corpus-growth cycle: {len(sources)} active sources", flush=True)

    new_docs = 0
    errors = 0
    t0 = time.time()
    for sid, url, domain in sources:
        found = 0
        try:
            cands = _candidate_docs(url, domain)
            for u in cands:
                try:
                    res = download_document_content(u)
                    if not res:
                        continue
                    content, filename = res
                    if len(content) < 2000:
                        continue
                    out = process_document(
                        content=content, filename=filename, source_id=sid,
                        source_url=u, bypass_copyright=True)
                    if out.get("status") == "ingested":
                        new_docs += 1
                        found += 1
                        # Run the SAME post-ingest pipeline the in-process
                        # worker uses (quality agents -> review status ->
                        # tier + authority record -> embeddings). Previously
                        # the timer stopped at process_document, so every
                        # harvested doc stayed `pending` with tier_id NULL and
                        # was excluded from search.
                        try:
                            _finish(sid, u, domain, out["document_id"])
                        except Exception as _e:  # noqa: BLE001
                            print(f"    finish error: {type(_e).__name__}", flush=True)
                except Exception:  # noqa: BLE001
                    errors += 1
        except Exception as e:  # noqa: BLE001
            errors += 1
            print(f"  {domain}: cycle error {type(e).__name__}", flush=True)
        if found:
            print(f"  {domain}: +{found} new", flush=True)

    cur.execute("SELECT count(*) FROM klaus_documents")
    total = cur.fetchone()[0]
    conn.close()
    secs = round(time.time() - t0, 1)
    print(f"corpus-growth DONE: +{new_docs} new docs, {errors} errors, "
          f"corpus={total}, {secs}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
