#!/usr/bin/env python3
"""KLAUS Homepage-PDF Harvest — added 2026-09-11.

Reingests reachable, non-paywalled Ghana government sites using the new
_discover_homepage_pdfs strategy. Downloads each discovered PDF, computes
SHA-256, stores under STORAGE_ROOT/<source_key>/<hash>.pdf, and inserts a
klaus_documents row.

Skipped: sources flagged PAYWALL in the previous backfill, or unreachable.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, "/opt/ai-orchestrator")

from core.klaus.source_registry import get_unverified_sources, get_permitted_sources
from core.klaus.generic_connector import multi_strategy_discover, HEADERS, REQUEST_TIMEOUT
from core.klaus.db_manager import (
    add_source,
    insert_document,
    get_document_by_hash,
    compute_file_hash,
    log_audit_event,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("klaus.homepage_ingest")

STORAGE_ROOT = Path("/var/lib/klaus/documents")
STORAGE_ROOT.mkdir(parents=True, exist_ok=True)

MAX_PDF_BYTES = 50 * 1024 * 1024   # 50 MB
PER_HOST_DELAY_SEC = 2.0
MAX_RUNTIME_SEC = 15 * 60
BACKFILL_LOG = "/var/log/klaus_backfill.log"


def _reachable_non_paywalled(sources):
    """Filter a list of GhanaLegalSource by parsing the prior backfill log."""
    reachable_keys = set()
    paywalled_keys = set()
    try:
        with open(BACKFILL_LOG) as f:
            log = f.read()
    except OSError:
        return list(sources)  # no log — process everything

    # Reachable lines look like: "[N/M] key (domain) tier=X" then "✅ reachable"
    # Paywall lines look like: "  💰 PAYWALL:"
    current_key = None
    for line in log.splitlines():
        m = re.match(r"\[\d+/\d+\]\s+(\S+)\s+\(", line)
        if m:
            current_key = m.group(1)
            continue
        if current_key and "✅ reachable" in line:
            reachable_keys.add(current_key)
        elif current_key and "PAYWALL" in line:
            paywalled_keys.add(current_key)

    return [
        s for s in sources
        if s.key in reachable_keys and s.key not in paywalled_keys
    ]


def _download_pdf(url: str) -> bytes | None:
    """HEAD-check then GET. Reject non-PDF or too-large."""
    try:
        head = requests.head(url, headers=HEADERS, timeout=REQUEST_TIMEOUT,
                             allow_redirects=True)
        ct = (head.headers.get("content-type") or "").lower()
        cl = int(head.headers.get("content-length") or 0)
        if cl and cl > MAX_PDF_BYTES:
            logger.warning(f"  skip (too large {cl} bytes): {url}")
            return None
        if ct and "pdf" not in ct and "octet-stream" not in ct:
            logger.warning(f"  skip (content-type={ct}): {url}")
            return None

        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT,
                            stream=True, allow_redirects=True)
        if resp.status_code != 200:
            logger.warning(f"  skip (HTTP {resp.status_code}): {url}")
            return None
        chunks = []
        total = 0
        for chunk in resp.iter_content(chunk_size=64 * 1024):
            if chunk:
                chunks.append(chunk)
                total += len(chunk)
                if total > MAX_PDF_BYTES:
                    logger.warning(f"  skip (stream >50MB): {url}")
                    return None
        return b"".join(chunks)
    except Exception as e:
        logger.warning(f"  download err ({type(e).__name__}): {url}")
        return None


def _guess_category(title: str, url: str) -> str:
    t = (title + " " + url).lower()
    if any(k in t for k in ("act", "law", "regulation", "code")):
        return "legislation"
    if "case" in t or "judgment" in t:
        return "case_law"
    if "policy" in t or "framework" in t:
        return "policy"
    if "guideline" in t or "guidance" in t:
        return "guideline"
    return "reference"


def run() -> dict:
    start = time.time()
    sources = list(get_unverified_sources()) + list(get_permitted_sources())
    # Dedupe by key
    by_key = {s.key: s for s in sources}
    sources = list(by_key.values())

    targets = _reachable_non_paywalled(sources)
    logger.info(f"=== KLAUS Homepage Ingest: {len(targets)} eligible sources "
                f"(out of {len(sources)}) ===")

    stats = {
        "sources_processed": 0,
        "docs_discovered": 0,
        "docs_inserted": 0,
        "docs_skipped_dupes": 0,
        "docs_download_failed": 0,
        "sources_zero_docs": [],
    }

    for src in targets:
        if time.time() - start > MAX_RUNTIME_SEC:
            logger.warning("Time budget exceeded — stopping")
            break

        stats["sources_processed"] += 1
        base_url = src.base_url or f"https://{src.domain}"
        logger.info(f"\n[{stats['sources_processed']}/{len(targets)}] "
                    f"{src.key} ({src.domain}) tier={src.tier}")

        try:
            docs = multi_strategy_discover(
                source_url=base_url,
                source_domain=src.domain,
                discovery_urls=src.discovery_urls,
                acquisition_status=src.acquisition_status,
            )
        except Exception as e:
            logger.warning(f"  discovery failed: {e}")
            docs = []

        stats["docs_discovered"] += len(docs)
        if not docs:
            stats["sources_zero_docs"].append(src.key)
            continue

        try:
            source_id = add_source(base_url, src.domain, src.tier, "Ghana")
        except Exception as e:
            logger.warning(f"  add_source err: {e}")
            continue

        for doc in docs[:20]:
            time.sleep(PER_HOST_DELAY_SEC)
            content = _download_pdf(doc["url"])
            if content is None:
                stats["docs_download_failed"] += 1
                continue

            file_hash = compute_file_hash(content)
            if get_document_by_hash(file_hash):
                stats["docs_skipped_dupes"] += 1
                continue

            src_dir = STORAGE_ROOT / src.key
            src_dir.mkdir(parents=True, exist_ok=True)
            file_path = src_dir / f"{file_hash}.pdf"
            file_path.write_bytes(content)

            try:
                insert_document(
                    source_id=source_id,
                    title=(doc.get("title") or file_hash)[:500],
                    file_hash=file_hash,
                    file_path=str(file_path),
                    category=_guess_category(doc.get("title", ""), doc["url"]),
                    jurisdiction="Ghana",
                    copyright_classification=src.rights_classification or "pending",
                    access_level="public",
                )
                stats["docs_inserted"] += 1
                logger.info(f"  ✓ inserted {file_hash[:8]}  {doc['url']}")
            except Exception as e:
                logger.warning(f"  insert err: {e}")
                stats["docs_download_failed"] += 1

    logger.info(f"\n{'='*60}")
    logger.info("SUMMARY")
    for k, v in stats.items():
        if isinstance(v, list):
            logger.info(f"  {k}: {len(v)} — {v}")
        else:
            logger.info(f"  {k}: {v}")
    try:
        log_audit_event("homepage_ingest", "info", f"Complete: {stats}")
    except Exception:
        pass
    return stats


if __name__ == "__main__":
    stats = run()
    sys.exit(0 if stats["docs_inserted"] > 0 else 0)
