"""
KLAUS Legal Knowledge Acquisition System - Background Workers

Implements the discovery and ingestion pipeline workers that run
as background processes to crawl sources, discover documents, and
process them through the quality control pipeline.
"""

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests

from core.klaus.db_manager import (
    get_cursor,
    add_source,
    get_source,
    list_sources,
    update_source_status,
    log_audit_event,
    get_documents_flagged_for_review,
    get_tier_coverage_stats,
    update_tier_acquisition_count,
    count_documents_by_tier,
)
from core.klaus.schema import ACQUISITION_TIERS, get_tier_priority_band
from core.klaus.document_processor import (
    process_document,
    extract_text_from_pdf,
    extract_text_from_txt,
)
from core.klaus.quality_agents import run_all_agents
from core.klaus.vector_indexer import index_document_chunks

logger = logging.getLogger(__name__)

# Worker thread pool
_worker_pool = ThreadPoolExecutor(max_workers=4)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


GHANA_TLD_SUFFIXES = (".gov.gh", ".org.gh", ".edu.gh", ".com.gh", ".gh")


def _gh_get(url: str, *, headers: dict | None = None, timeout: int = 30):
    """GET with a one-shot insecure retry for Ghanaian government hosts.

    Several .gov.gh sites serve expired or self-signed TLS certificates.
    A strict verification failure is retried once with verify=False, but ONLY
    for Ghana TLD hosts — never for arbitrary third-party domains.
    """
    try:
        return requests.get(url, headers=headers, timeout=timeout)
    except requests.exceptions.SSLError:
        host = urlparse(url).hostname or ""
        if not host.endswith(GHANA_TLD_SUFFIXES):
            raise
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        return requests.get(url, headers=headers, timeout=timeout, verify=False)


def _resolve_url(href: str, base_url: str) -> str:
    """Resolve an href against a base URL, robustly.

    Uses urljoin so protocol-relative ("//host/path"), root-relative and
    relative links all resolve correctly. The previous naive concatenation
    produced malformed URLs like "https://www.nand" + "https://www.…" when a
    page embedded an absolute URL inside an href, which made downloads fail.
    """
    from urllib.parse import urljoin
    if not href:
        return base_url
    h = href.strip()
    if h.startswith(("http://", "https://")):
        return h
    try:
        return urljoin(base_url, h)
    except Exception:  # noqa: BLE001
        base = base_url.rstrip("/")
        return base + ("/" if not h.startswith("/") else "") + h.lstrip("/")


def _discover_parliament_gh(source_url: str, source_domain: str) -> List[Dict]:
    """Ghana-specific: scrape parliament.gh for Acts and Bills.

    Parliament.gh serves legislation lists via server-side rendered tables.
    PDF links are wrapped in showPDF() JS calls pointing to /epanel/docs/<file>.
    """
    documents = []
    for doctype in ("Acts", "Bills"):
        try:
            url = f"https://www.parliament.gh/docs?type={doctype}&OT"
            response = requests.get(url, headers=HEADERS, timeout=30)
            response.raise_for_status()
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(response.text, "html.parser")

            # Pattern 1: showPDF('filename.pdf', 'title') in onclick/href attributes
            import re
            from urllib.parse import quote
            showpdf_pattern = re.compile(r"showPDF\('([^']+\.pdf)'\s*,\s*'([^']*)'\)")
            for match in showpdf_pattern.finditer(response.text):
                filename = match.group(1)
                title = match.group(2) or filename.replace(".pdf", "").replace("_", " ")
                # URL-encode the filename (handles spaces, commas, etc.)
                pdf_url = f"https://www.parliament.gh/epanel/docs/{quote(filename)}"
                documents.append({
                    "title": f"[{doctype}] {title}",
                    "url": pdf_url,
                    "type": "pdf",
                    "source_domain": source_domain,
                })

            # Pattern 2: Direct links to PDFs
            for link in soup.find_all("a", href=lambda x: x and x.lower().endswith(".pdf")):
                href = link.get("href")
                if href and not href.startswith("#"):
                    title = link.get_text().strip() or "Unknown Document"
                    documents.append({
                        "title": f"[{doctype}] {title}",
                        "url": _resolve_url(href, url),
                        "type": "pdf",
                        "source_domain": source_domain,
                    })
        except Exception as e:
            logger.warning(f"Parliament.gh {doctype} scan failed: {e}")

    return documents


def _discover_ghalii(source_url: str, source_domain: str) -> List[Dict]:
    """GhaLII (PeachJam/LII) discovery for Ghana legislation + judgments.

    Live behaviour (verified 2026-09-26):
      * ``/legislation/all`` and ``/legislation/subsidiary`` return 200 and list
        ~50 real acts / L.I.s each as ``/akn/gh/act/...`` links — this is the
        authoritative primary-legislation index.
      * ``/judgments/`` and ``/akn/`` return 403, and individual act pages are
        Cloudflare-gated (403 "Just a moment..."), so full text cannot be
        fetched with plain HTTP. We therefore record the authoritative
        *reference* (canonical GhaLII URL + title) as a ``reference`` document;
        the fetch layer may later resolve it via a headless browser. We never
        fabricate text.

    Falls back to keyword search when the browse pages yield nothing.
    """
    import urllib.parse

    documents: List[Dict] = []
    seen: set[str] = set()

    def _add(url: str, title: str) -> None:
        if not url or url in seen:
            return
        seen.add(url)
        documents.append({
            "title": title or url.rstrip("/").split("/")[-1],
            "url": url,
            "type": "reference",          # canonical legal reference (akn)
            "source_domain": source_domain,
            "store_mode": "reference",
        })

    # 1) Authoritative browse indexes (these work; /akn pages do not).
    browse_paths = [
        ("/legislation/all", "Ghana Act"),
        ("/legislation/subsidiary", "Ghana Subsidiary Legislation (L.I.)"),
        ("/legislation/aa-au/", "AU Charter/Treaty"),
    ]
    try:
        from bs4 import BeautifulSoup
    except Exception:  # noqa: BLE001
        return documents

    for path, label in browse_paths:
        try:
            url = f"https://ghalii.org{path}"
            response = requests.get(url, headers=HEADERS, timeout=30)
            if response.status_code != 200:
                continue
            soup = BeautifulSoup(response.text, "html.parser")
            for link in soup.find_all("a", href=True):
                href = link.get("href", "")
                if "/akn/gh/" not in href:
                    continue
                title = link.get_text().strip()
                full = _resolve_url(href, "https://ghalii.org")
                # Derive a readable title from the akn path when the anchor is
                # empty (e.g. /akn/gh/act/2010/796/eng@2010-04-16 -> Act 796).
                if not title:
                    parts = [p for p in href.split("/") if p]
                    try:
                        kind_i = parts.index("act")
                        title = f"{label} {parts[kind_i + 1]}/{parts[kind_i + 2]}"
                    except (ValueError, IndexError):
                        title = label
                _add(full, title)
        except Exception as e:  # noqa: BLE001
            logger.warning("GhaLII browse %s failed: %s", path, e)

    if documents:
        logger.info("GhaLII browse indexes found %d legislation references", len(documents))
        return documents

    # 2) Fallback: keyword search (kept from the previous implementation).
    search_terms = [
        "ghana constitution", "act of parliament ghana", "criminal ghana",
        "land ghana", "tax ghana", "employment ghana", "human rights ghana",
    ]
    for term in search_terms:
        try:
            q = urllib.parse.quote(term)
            url = f"https://ghalii.org/search/?q={q}"
            response = requests.get(url, headers=HEADERS, timeout=30)
            if response.status_code != 200:
                continue
            soup = BeautifulSoup(response.text, "html.parser")
            for link in soup.find_all("a", href=True):
                href = link.get("href", "")
                if "/akn/gh/" not in href:
                    continue
                _add(_resolve_url(href, "https://ghalii.org"),
                     link.get_text().strip() or "GhaLII legislation")
        except Exception as e:  # noqa: BLE001
            logger.warning("GhaLII search '%s' failed: %s", term, e)

    return documents


def _discover_judicial_gh(source_url: str, source_domain: str) -> List[Dict]:
    """Ghana-specific: scrape judicial.gov.gh for court rulings/publications.

    Judicial.gov.gh runs Joomla CMS. Focus on publications sections.
    """
    documents = []
    scan_paths = [
        "/index.php/publications",
        "/index.php/publications/judgments",
    ]
    for path in scan_paths:
        try:
            url = f"https://judicial.gov.gh{path}"
            response = requests.get(url, headers=HEADERS, timeout=30)
            if response.status_code != 200:
                continue
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(response.text, "html.parser")

            for link in soup.find_all("a", href=True):
                href = link.get("href", "")
                title = link.get_text().strip()
                if not title or len(title) < 10:
                    continue
                if any(href.lower().endswith(ext) for ext in (".pdf", ".doc", ".docx")):
                    documents.append({
                        "title": title,
                        "url": _resolve_url(href, url),
                        "type": "pdf" if href.lower().endswith(".pdf") else "doc",
                        "source_domain": source_domain,
                    })
        except Exception as e:
            logger.warning(f"Judicial.gh {path} scan failed: {e}")

    return documents


def _discover_ejudgment_gh(source_url: str, source_domain: str) -> List[Dict]:
    """Ghana e-Judgment Portal scraper — https://www.ejudgment.judicial.gov.gh/

    NOTE: The eJudgment portal is primarily login-walled (requires judge/lawyer
    credentials). This handler:
    1. Attempts public pages and alternative access paths
    2. Tries the parent judicial.gov.gh domain for public judgments
    3. Returns what's publicly accessible; login-walled content must be acquired
       via credentialed access (manual or API key).

    SSL verification is disabled because the eJudgment cert has hostname mismatch.
    """
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    documents = []
    session = requests.Session()
    session.headers.update(HEADERS)
    session.verify = False  # SSL cert has hostname mismatch

    # Strategy 1: Try main eJudgment portal pages (limited — login-walled)
    ejudgment_paths = ["/", "/index.php", "/about", "/contact"]
    for path in ejudgment_paths:
        try:
            url = f"https://ejudgment.judicial.gov.gh{path}"
            response = session.get(url, timeout=15)
            if response.status_code != 200:
                continue
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(response.text, "html.parser")
            for link in soup.find_all("a", href=True):
                href = link.get("href", "")
                title = link.get_text().strip()
                if not title or len(title) < 5:
                    continue
                if any(href.lower().endswith(ext) for ext in (".pdf", ".doc", ".docx")):
                    documents.append({
                        "title": title,
                        "url": _resolve_url(href, url),
                        "type": "pdf",
                        "source_domain": source_domain,
                    })
        except Exception as e:
            logger.debug(f"eJudgment {path}: {e}")

    # Strategy 2: Try judicial.gov.gh (parent domain) for public judgments
    try:
        response = session.get("https://judicial.gov.gh", timeout=15)
        if response.status_code == 200:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(response.text, "html.parser")

            # Follow links to publications, judgments, media sections
            for link in soup.find_all("a", href=True):
                href = link.get("href", "")
                text = link.get_text().strip().lower()
                if any(kw in text for kw in ("publication", "judgment", "ruling", "media", "download")):
                    try:
                        sub_url = _resolve_url(href, "https://judicial.gov.gh")
                        sub_r = session.get(sub_url, timeout=15)
                        if sub_r.status_code == 200:
                            sub_soup = BeautifulSoup(sub_r.text, "html.parser")
                            for sub_link in sub_soup.find_all("a", href=True):
                                sub_href = sub_link.get("href", "")
                                sub_title = sub_link.get_text().strip()
                                if sub_title and len(sub_title) > 5:
                                    if any(sub_href.lower().endswith(ext) for ext in (".pdf", ".doc", ".docx")):
                                        documents.append({
                                            "title": sub_title,
                                            "url": _resolve_url(sub_href, sub_url),
                                            "type": "pdf",
                                            "source_domain": source_domain,
                                        })
                    except Exception:
                        pass
    except Exception as e:
        logger.debug(f"Judicial.gov.gh fallback: {e}")

    if not documents:
        logger.info(
            "eJudgment: No publicly accessible documents found. "
            "The portal requires authentication (login-walled). "
            "Consider obtaining API credentials or manual bulk export."
        )

    return documents


def _discover_parliament_repository(source_url: str, source_domain: str) -> List[Dict]:
    """Ghana Parliament Repository (DSpace 7) discovery.

    Verified live 2026-09-26: the legacy ``/rest/`` API is gone; DSpace 7 uses
    ``/server/``. Discovery path that works without credentials:

      1. OAI-PMH ListRecords (oai_dc) — 100 records/page, gives dc:title and a
         handle URL (e.g. http://hdl.handle.net/123456789/762).
      2. Resolve each handle to an item uuid via
         ``/server/api/pid/find?id=hdl:<handle>``.
      3. ``/server/api/core/items/<uuid>/bundles`` → ORIGINAL bundle →
         ``/server/api/core/bundles/<uuid>/bitstreams`` → the PDF.
      4. Bitstream download:
         ``/server/api/core/bitstreams/<uuid>/content``.

    Newer DSpace exposes the first bitstream directly on the item as
    ``_embedded.bitstreams``; both are handled. Never fabricates a URL.
    """
    documents: List[Dict] = []
    session = requests.Session()
    session.headers.update({**HEADERS, "Accept": "application/json, text/xml, */*"})
    base = "https://repository.parliament.gh/server"
    seen_items: set[str] = set()

    def _pdf_from_item(item: dict) -> Optional[Dict]:
        uuid = item.get("uuid") or item.get("id")
        name = item.get("name") or "Parliament record"
        if not uuid or uuid in seen_items:
            return None
        seen_items.add(uuid)
        # 1) inline bitstreams (DSpace 7 sometimes embeds them)
        try:
            emb = (item.get("_embedded") or {}).get("bitstreams") or {}
            for bs in emb.get("bitstreams", []) or []:
                nm = (bs.get("name") or "").lower()
                if nm.endswith(".pdf"):
                    return {"title": f"[Parliament] {name}",
                            "url": f"{base}/api/core/bitstreams/{bs.get('uuid')}/content",
                            "type": "pdf", "source_domain": source_domain}
        except Exception:  # noqa: BLE001
            pass
        # 2) bundles → ORIGINAL → bitstreams
        try:
            rb = session.get(f"{base}/api/core/items/{uuid}/bundles", timeout=20)
            if rb.status_code == 200:
                for b in (rb.json().get("_embedded", {}) or {}).get("bundles", []) or []:
                    if b.get("name") != "ORIGINAL":
                        continue
                    rbs = session.get(f"{base}/api/core/bundles/{b['uuid']}/bitstreams",
                                      timeout=20)
                    if rbs.status_code == 200:
                        for bs in (rbs.json().get("_embedded", {}) or {}).get("bitstreams", []) or []:
                            if (bs.get("name") or "").lower().endswith(".pdf"):
                                return {"title": f"[Parliament] {name}",
                                        "url": f"{base}/api/core/bitstreams/{bs['uuid']}/content",
                                        "type": "pdf", "source_domain": source_domain}
        except Exception:  # noqa: BLE001
            pass
        return None

    # Strategy 1: OAI-PMH ListRecords (one page; the worker can resume later).
    handles: list[str] = []
    try:
        import xml.etree.ElementTree as ET
        r = session.get(f"{base}/oai/request?verb=ListRecords&metadataPrefix=oai_dc",
                        timeout=30)
        if r.status_code == 200:
            root = ET.fromstring(r.text)
            # dc:identifier lives in the Dublin Core namespace (the oai_dc
            # wrapper uses oai_dc, but the elements themselves are dc:).
            for ident in root.iter("{http://purl.org/dc/elements/1.1/}identifier"):
                # handles look like http://hdl.handle.net/123456789/762
                m = re.search(r"(?:hdl\.handle\.net|ir\.parliament\.gh/handle)/"
                              r"([0-9]+/[0-9]+)", ident.text or "")
                if m:
                    handles.append(m.group(1))
    except Exception as e:  # noqa: BLE001
        logger.info("Parliament OAI discovery failed: %s", e)

    # Strategy 2: resolve handles → items → PDFs (bounded batch)
    for handle in list(dict.fromkeys(handles))[:40]:
        try:
            rp = session.get(f"{base}/api/pid/find?id=hdl:{handle}", timeout=20)
            if rp.status_code != 200:
                continue
            doc = _pdf_from_item(rp.json())
            if doc:
                documents.append(doc)
        except Exception as e:  # noqa: BLE001
            logger.debug("Parliament handle %s: %s", handle, e)

    # Strategy 3: HTML fallback for the home page, then legacy parliament.gh.
    if len(documents) < 5:
        try:
            response = session.get("https://repository.parliament.gh/home", timeout=30,
                                   headers={**HEADERS, "Accept": "text/html"})
            if response.status_code == 200:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(response.text, "html.parser")
                for link in soup.find_all("a", href=True):
                    href = link.get("href", "")
                    title = link.get_text().strip()
                    if title and len(title) > 5 and href.lower().endswith(".pdf"):
                        documents.append({
                            "title": title,
                            "url": _resolve_url(href, "https://repository.parliament.gh"),
                            "type": "pdf",
                            "source_domain": source_domain,
                        })
        except Exception as e:  # noqa: BLE001
            logger.debug("Parliament HTML fallback: %s", e)

    legacy = _discover_parliament_gh("https://www.parliament.gh", source_domain)
    documents.extend(legacy)

    return documents


def _discover_ghanapublishing_gh(source_url: str, source_domain: str) -> List[Dict]:
    """Ghana Publishing Company scraper — https://ghanapublishing.gov.gh/

    The Ghana Publishing Company publishes the Ghana Gazette (official government
    notices, statutory instruments, acts as passed), consolidated statutes, and
    other official publications.

    Strategy:
    1. Scrape known publication paths (gazette, acts, regulations)
    2. Generic PDF link discovery across the site
    """
    documents = []
    session = requests.Session()
    session.headers.update(HEADERS)

    browse_paths = [
        "/", "/publications", "/gazette", "/gazettes",
        "/acts", "/regulations", "/statutes",
        "/publications/gazette", "/downloads",
        "/index.php", "/index.php/publications",
        "/categories", "/shop",  # Some publishing sites use e-commerce patterns
    ]

    for path in browse_paths:
        try:
            url = f"https://ghanapublishing.gov.gh{path}"
            response = session.get(url, timeout=30)
            if response.status_code != 200:
                continue
            from bs4 import BeautifulSoup
            import re
            soup = BeautifulSoup(response.text, "html.parser")

            # Find PDF links with Ghana Gazette naming patterns
            for link in soup.find_all("a", href=True):
                href = link.get("href", "")
                title = link.get_text().strip()
                if not title and link.get("title"):
                    title = link.get("title", "").strip()

                # Must have a title
                if not title or len(title) < 5:
                    # Check if the href itself is descriptive
                    href_base = href.rsplit("/", 1)[-1].replace("%20", " ").replace("_", " ")
                    if len(href_base) > 5:
                        title = href_base

                if not title or len(title) < 5:
                    continue

                # Accept PDFs and other document formats
                if any(href.lower().endswith(ext) for ext in (".pdf", ".doc", ".docx")):
                    # Tag Ghana Gazette publications
                    is_gazette = any(kw in title.lower() or kw in href.lower()
                                     for kw in ("gazette", "gazetted", "notice"))
                    prefix = "[Gazette] " if is_gazette else "[Pub] "
                    documents.append({
                        "title": f"{prefix}{title}",
                        "url": _resolve_url(href, url),
                        "type": "pdf" if href.lower().endswith(".pdf") else "doc",
                        "source_domain": source_domain,
                    })
        except Exception as e:
            logger.debug(f"Ghana Publishing {path}: {e}")

    return documents


# Per-domain discovery handlers — dedicated handlers for known sources
_DOMAIN_HANDLERS = {
    "parliament.gh": _discover_parliament_repository,
    "ghalii.org": _discover_ghalii,
    "judicial.gov.gh": _discover_ejudgment_gh,
    "ghanapublishing.gov.gh": _discover_ghanapublishing_gh,
}


def _build_generic_handlers() -> dict:
    """Register generic multi-strategy handlers for all non-dedicated sources.

    Called at module load time. Dedicated handlers always take precedence
    over generic ones — if a domain already has a handler in _DOMAIN_HANDLERS,
    the generic handler is NOT registered for that domain.
    """
    from core.klaus.source_registry import GHANA_LEGAL_SOURCES
    from core.klaus.generic_connector import create_generic_domain_handler

    generic = {}
    for src in GHANA_LEGAL_SOURCES:
        if src.domain in _DOMAIN_HANDLERS:
            continue  # dedicated handler exists, skip generic
        if not src.base_url:
            continue  # no URL to discover from

        generic[src.domain] = create_generic_domain_handler(
            source_key=src.key,
            source_domain=src.domain,
            discovery_urls=src.discovery_urls,
            acquisition_status=src.acquisition_status,
        )
    return generic


# Extend domain handlers with generic multi-strategy handlers
_GENERIC_HANDLERS = _build_generic_handlers()
_DOMAIN_HANDLERS.update(_GENERIC_HANDLERS)


def discover_source_content(source_url: str, source_domain: str) -> List[Dict]:
    """Discover and parse content from a source URL.

    Returns list of document candidates with metadata.
    Uses domain-specific handlers when available, falling back to generic scraping.
    """
    # Use domain-specific handler if available
    handler = _DOMAIN_HANDLERS.get(source_domain)
    if handler:
        try:
            documents = handler(source_url, source_domain)
            if documents:
                logger.info(f"Domain handler for {source_domain} found {len(documents)} documents")
                return documents
        except Exception as e:
            logger.warning(f"Domain handler for {source_domain} failed: {e}, falling back to generic")

    # Generic fallback
    try:
        response = _gh_get(source_url, headers=HEADERS, timeout=30)
        response.raise_for_status()

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(response.text, 'html.parser')
        documents = []

        # Look for PDF links
        pdf_links = soup.find_all('a', href=lambda x: x and x.lower().endswith('.pdf'))
        for link in pdf_links:
            href = link.get('href')
            if href and not href.startswith('#'):
                title = link.get_text().strip() or "Unknown Document"
                documents.append({
                    'title': title,
                    'url': href if href.startswith('http') else source_url.rstrip('/') + '/' + href.lstrip('/'),
                    'type': 'pdf',
                    'source_domain': source_domain,
                })

        # Look for text documents
        txt_links = soup.find_all('a', href=lambda x: x and x.lower().endswith('.txt'))
        for link in txt_links:
            href = link.get('href')
            if href and not href.startswith('#'):
                title = link.get_text().strip() or "Unknown Document"
                documents.append({
                    'title': title,
                    'url': href if href.startswith('http') else source_url.rstrip('/') + '/' + href.lstrip('/'),
                    'type': 'txt',
                    'source_domain': source_domain,
                })

        return documents

    except Exception as e:
        logger.warning(f"Failed to discover content from {source_url}: {e}")
        return []


def download_document_content(url: str) -> Optional[Tuple[bytes, str]]:
    """
    Download document content from URL.
    Returns (content_bytes, filename) or None if failed.

    Many Ghanaian government sites have expired/self-signed TLS certs, so a
    strict TLS failure is retried once with verification disabled — the
    content is public legislation and the host is a known .gov.gh source. This
    never bypasses TLS for non-government hosts.
    """
    from urllib.parse import unquote
    # Some servers 406 when Accept only advertises HTML; send a browser-like
    # binary Accept and a same-origin Referer for the download.
    dl_headers = {
        "User-Agent": HEADERS["User-Agent"],
        "Accept": "application/pdf,application/octet-stream,*/*",
        "Accept-Language": HEADERS.get("Accept-Language", "en-US,en;q=0.9"),
        "Referer": url,
    }
    try:
        response = requests.get(url, timeout=30, headers=dl_headers)
        response.raise_for_status()
    except requests.exceptions.SSLError:
        host = urlparse(url).hostname or ""
        if not host.endswith((".gov.gh", ".org.gh", ".edu.gh", ".com.gh", ".gh")):
            logger.warning("TLS failure for non-Ghana host %s; not bypassing", host)
            return None
        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            response = requests.get(url, timeout=30, verify=False, headers=dl_headers)
            response.raise_for_status()
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to download %s (insecure retry): %s", url, e)
            return None
    except Exception as e:
        logger.warning(f"Failed to download {url}: {e}")
        return None
    filename = unquote(urlparse(url).path.split('/')[-1]) or "unnamed_document"
    content = response.content
    # Guard against saving an HTML error/redirect page as a document. A URL
    # ending in .pdf that actually returns HTML (login wall, soft-404, JS
    # shell) must not enter the corpus as an empty "PDF" — it produces a
    # chunk-less document and pollutes the index.
    head = content[:16].lstrip().lower()
    if filename.lower().endswith(".pdf") and head.startswith((b"<!doctype", b"<html")):
        logger.warning("Skipping %s: expected PDF, got HTML (%d bytes)", url, len(content))
        return None
    return content, filename


def process_discovered_documents(
    source_id: int,
    source_url: str,
    source_domain: str,
    documents: List[Dict],
) -> int:
    """
    Process discovered documents through the ingestion pipeline.
    Returns number of successfully processed documents.
    """
    processed = 0
    
    for doc_info in documents:
        try:
            # Download content
            content_result = download_document_content(doc_info['url'])
            if not content_result:
                continue
                
            content_bytes, filename = content_result
            
            # Process through ingestion pipeline
            result = process_document(
                content=content_bytes,
                filename=filename,
                source_id=source_id,
                source_url=source_url,
                jurisdiction="Ghana",
                bypass_copyright=True,
            )
            
            if result.get("status") == "ingested":
                # Run quality control agents (6 agents including tier classification)
                agent_results = run_all_agents(result["document_id"])

                # Update document with final status
                document_approved = agent_results.get("overall") == "approved"

                # Create Legal Authority Record for the ingested document
                try:
                    from core.klaus.db_manager import (
                        insert_authority_record,
                        get_document,
                    )
                    from core.klaus.quality_agents import TierClassificationAgent

                    doc = get_document(result["document_id"])
                    if doc:
                        tier_num, authority_type, confidence = TierClassificationAgent.classify(
                            result["document_id"]
                        )
                        insert_authority_record(
                            document_id=result["document_id"],
                            authority_type=authority_type,
                            citation_text=doc.get("title", ""),
                            court_identifier=doc.get("court") or "",
                            status="current",
                            language="en",
                        )
                        update_tier_acquisition_count(tier_num)
                        logger.info(
                            f"Authority Record created: T{tier_num} {authority_type} "
                            f"(confidence={confidence})"
                        )
                except Exception as e:
                    logger.warning(f"Authority Record creation skipped: {e}")

                # Index chunks if approved
                if document_approved:
                    try:
                        index_count = index_document_chunks(result["document_id"])
                        logger.info(f"Indexed {index_count} chunks for document {result['document_id']}")
                    except Exception as e:
                        logger.error(f"Failed to index document {result['document_id']}: {e}")
                        
                processed += 1
                logger.info(f"Successfully processed document: {doc_info['title']}")
            elif result.get("status") == "duplicate":
                logger.info(f"Skipped duplicate document: {doc_info['title']}")
                
        except Exception as e:
            logger.error(f"Failed to process document {doc_info['title']}: {e}")
            continue
    
    return processed


def run_discovery_worker():
    """
    Run the discovery worker with tier-prioritized scheduling.

    Tier priority bands:
    - Band 1 (daily):  T1-T4  Constitutional, Acts, Subsidiary, Precedents
    - Band 2 (3 days): T5-T8  Criminal, Commercial, Employment, Tax
    - Band 3 (weekly): T9-T12 Property, Family, IP, Technology
    - Band 4 (monthly): T13-T16 Banking, Government, Case Law, Publications
    """
    logger.info("KLAUS Discovery Worker started (16-tier priority mode)")

    # Track last scan for tier bands
    last_band_scan = {"daily": 0, "every_3_days": 0, "weekly": 0, "monthly": 0}

    while True:
        try:
            now = time.time()

            # Determine which priority bands to scan this cycle
            active_bands = []
            if now - last_band_scan["daily"] >= 3600:  # 1 hour
                active_bands.append("daily")
                last_band_scan["daily"] = now
            if now - last_band_scan["every_3_days"] >= 86400:  # 24 hours (in practice: every cycle)
                active_bands.append("every_3_days")
                last_band_scan["every_3_days"] = now
            if now - last_band_scan["weekly"] >= 604800:  # 7 days
                active_bands.append("weekly")
                last_band_scan["weekly"] = now
            if now - last_band_scan["monthly"] >= 2592000:  # 30 days
                active_bands.append("monthly")
                last_band_scan["monthly"] = now

            # Report tier coverage at start of cycle
            coverage = get_tier_coverage_stats()
            empty_tiers = [
                s for s in coverage
                if s["actual_count"] == 0
                and s["tier_number"] in [
                    tn for tn in range(1, 17)
                    if get_tier_priority_band(tn) in active_bands
                ]
            ]
            low_tiers = [
                s for s in coverage
                if s["coverage_pct"] > 0 and s["coverage_pct"] < 50
                and s["tier_number"] in [
                    tn for tn in range(1, 17)
                    if get_tier_priority_band(tn) in active_bands
                ]
            ]

            if empty_tiers:
                empty_names = [f"T{s['tier_number']} ({s['tier_name']})" for s in empty_tiers]
                logger.warning(f"Empty tiers in active bands: {', '.join(empty_names)}")
            if low_tiers:
                low_names = [f"T{s['tier_number']} ({s['coverage_pct']:.0f}%)" for s in low_tiers]
                logger.info(f"Low-coverage tiers in active bands: {', '.join(low_names)}")

            # Scan all active sources
            sources = list_sources(tier=None, status="active")
            logger.info(
                f"Discovery Worker: Scanning {len(sources)} active sources "
                f"(bands: {', '.join(active_bands)})"
            )

            for source in sources:
                try:
                    logger.info(f"Discovering content from source: {source['domain']}")

                    # Find documents
                    discovered_docs = discover_source_content(source["url"], source["domain"])

                    # Filter: separate rights-blocked from acquirable
                    blocked_docs = [d for d in discovered_docs if d.get("_acquisition_blocked")]
                    acquirable_docs = [d for d in discovered_docs if not d.get("_acquisition_blocked")]

                    if blocked_docs:
                        logger.info(
                            f"{source['domain']}: {len(blocked_docs)} documents blocked by rights gate "
                            f"(metadata only, not acquired)"
                        )
                        log_audit_event(
                            "discovery", "info",
                            f"Rights gate: {len(blocked_docs)} documents from {source['domain']} "
                            f"blocked — metadata discovery only",
                            None,
                        )

                    logger.info(f"Found {len(discovered_docs)} total ({len(acquirable_docs)} acquirable) from {source['domain']}")

                    if acquirable_docs:
                        # Process discovered documents
                        processed = process_discovered_documents(
                            source_id=source["id"],
                            source_url=source["url"],
                            source_domain=source["domain"],
                            documents=acquirable_docs,
                        )

                        logger.info(f"Processed {processed} documents from {source['domain']}")

                        # Log discovery event
                        log_audit_event(
                            "discovery", "info",
                            f"Discovered {len(acquirable_docs)} acquirable documents, processed {processed} successfully",
                            None
                        )
                    else:
                        logger.debug(f"No documents found from {source['domain']}")

                except Exception as e:
                    logger.error(f"Error scanning source {source['domain']}: {e}")
                    # Mark source as broken if we can't access it
                    if "connection" in str(e).lower() or "timeout" in str(e).lower():
                        update_source_status(source["id"], "broken", 0.0)
                        logger.warning(f"Marked source {source['domain']} as broken")

            # End-of-cycle tier coverage summary
            tier_counts = count_documents_by_tier()
            coverage_lines = []
            for tn in range(1, 17):
                count = tier_counts.get(tn, 0)
                target = ACQUISITION_TIERS[tn]["target"]
                pct = f"{(count/target*100):.0f}%" if target else "N/A"
                icon = "✅" if count >= target * 0.5 else "🟡" if count > 0 else "⬜"
                coverage_lines.append(f"{icon} T{tn}: {count}/{target} ({pct})")
            logger.info("Tier Coverage:\n  " + "\n  ".join(coverage_lines))

            # Wait 1 hour before next discovery cycle
            logger.debug("Discovery Worker waiting 1 hour...")
            time.sleep(3600)  # 1 hour

        except KeyboardInterrupt:
            logger.info("Discovery Worker stopped by user")
            break
        except Exception as e:
            logger.error(f"Discovery Worker error: {e}")
            time.sleep(300)  # Wait 5 minutes before retrying


def run_ingestion_worker():
    """
    Run the ingestion worker that processes flagged documents.
    """
    logger.info("KLAUS Ingestion Worker started")
    
    while True:
        try:
            # Check for documents flagged for review
            flagged_docs = get_documents_flagged_for_review()
            logger.info(f"Ingestion Worker: Found {len(flagged_docs)} flagged documents")
            
            for doc in flagged_docs:
                try:
                    # Process with quality control
                    result = process_document(
                        content=b"",  # Will be populated from file
                        filename=doc["title"],
                        source_id=doc["source_id"],
                        source_url="",
                        jurisdiction=doc["jurisdiction"],
                        bypass_copyright=True,
                    )
                    logger.info(f"Re-processed document {doc['id']}: {result}")
                except Exception as e:
                    logger.error(f"Error re-processing document {doc['id']}: {e}")
            
            # Wait 5 minutes before next check
            logger.debug("Ingestion Worker waiting 5 minutes...")
            time.sleep(300)  # 5 minutes
            
        except KeyboardInterrupt:
            logger.info("Ingestion Worker stopped by user")
            break
        except Exception as e:
            logger.error(f"Ingestion Worker error: {e}")
            time.sleep(300)  # Wait 5 minutes before retrying


if __name__ == "__main__":
    # For testing purposes
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "discovery":
        run_discovery_worker()
    elif len(sys.argv) > 1 and sys.argv[1] == "ingestion":
        run_ingestion_worker()
    else:
        print("Usage: python -m core.klaus.background_workers [discovery|ingestion]")