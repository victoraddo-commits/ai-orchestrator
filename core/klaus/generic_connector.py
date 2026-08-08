"""
Multi-strategy generic legal source connector.

For sources without a dedicated domain handler, this module tries multiple
discovery strategies in priority order:
  1. sitemap.xml / sitemap index
  2. RSS/Atom feeds
  3. HTML link scanning for legal document patterns
  4. Generic PDF discovery

Respects the rights gate: RESTRICTED sources get metadata discovery only;
PERMITTED/UNVERIFIED sources proceed through the full pipeline.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("klaus.generic_connector")

# ── Config ────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

REQUEST_TIMEOUT = 30
MAX_DOCUMENTS_PER_SOURCE = 200  # safety cap
MAX_PAGES_TO_CRAWL = 10

# ── Legal document URL patterns ───────────────────────────────────────────

LEGAL_LINK_PATTERNS = [
    # Acts and legislation
    r"/acts?[/.]",  r"/bills?[/.]",  r"/legislation[/.]",
    r"/laws?[/.]",  r"/regulations?[/.]",  r"/rules?[/.]",
    r"/directives?[/.]",  r"/guidelines?[/.]",  r"/notices?[/.]",
    # Courts and judgments
    r"/judgments?[/.]",  r"/judgements?[/.]",  r"/decisions?[/.]",
    r"/cases?[/.]",  r"/court[/.]",  r"/rulings?[/.]",
    # Official publications
    r"/gazette[/.]",  r"/instruments?[/.]",
    r"/downloads?[/.]",  r"/publications?[/.]",  r"/resources?[/.]",
    r"/documents?[/.]",  r"/legal[/.]",  r"/legal-framework[/.]",
    # PDF indicators
    r"\.pdf$",
]

LEGAL_TITLE_PATTERNS = [
    re.compile(r"act\s+\d+", re.I),
    re.compile(r"regulation(s)?\s+\d+", re.I),
    re.compile(r"legislative\s+instrument", re.I),
    re.compile(r"l\.?i\.?\s*\d+", re.I),
    re.compile(r"constitutional\s+instrument", re.I),
    re.compile(r"c\.?i\.?\s*\d+", re.I),
    re.compile(r"executive\s+instrument", re.I),
    re.compile(r"e\.?i\.?\s*\d+", re.I),
    re.compile(r"bill\s+\d+", re.I),
    re.compile(r"amendment", re.I),
    re.compile(r"directive\s+\d+", re.I),
    re.compile(r"notice\s+\d+", re.I),
    re.compile(r"judgment", re.I),
    re.compile(r"ruling", re.I),
    re.compile(r"gazette", re.I),
    re.compile(r"act\s+of\s+parliament", re.I),
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_url(href: str, base_url: str) -> str:
    """Resolve relative URLs against the source base."""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return urljoin(base_url, href)


def _is_likely_legal_document(url: str, title: str = "") -> bool:
    """Check if a URL or title matches legal document patterns."""
    url_lower = url.lower()
    if any(re.search(p, url_lower) for p in LEGAL_LINK_PATTERNS):
        return True
    if title and any(p.search(title) for p in LEGAL_TITLE_PATTERNS):
        return True
    return False


# ── Strategy 1: Sitemap ───────────────────────────────────────────────────

def _discover_via_sitemap(base_url: str, source_domain: str) -> List[Dict]:
    """Try to discover documents via sitemap.xml."""
    sitemap_urls = [
        urljoin(base_url, "/sitemap.xml"),
        urljoin(base_url, "/sitemap_index.xml"),
        urljoin(base_url, "/sitemap-index.xml"),
    ]

    all_urls: Set[str] = set()

    for sm_url in sitemap_urls:
        try:
            resp = requests.get(sm_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                continue

            soup = BeautifulSoup(resp.content, "xml" if resp.content.strip().startswith(b"<?xml") else "lxml-xml")

            # Check if it's a sitemap index
            sitemap_locs = soup.find_all("sitemap")
            if sitemap_locs:
                for sm in sitemap_locs:
                    loc = sm.find("loc")
                    if loc and loc.text:
                        try:
                            sub_resp = requests.get(loc.text.strip(), headers=HEADERS, timeout=REQUEST_TIMEOUT)
                            if sub_resp.status_code == 200:
                                sub_soup = BeautifulSoup(sub_resp.content, "lxml-xml")
                                for url_tag in sub_soup.find_all("url"):
                                    loc_tag = url_tag.find("loc")
                                    if loc_tag and loc_tag.text:
                                        all_urls.add(loc_tag.text.strip())
                        except Exception:
                            continue
            else:
                # Direct URL listing
                for url_tag in soup.find_all("url"):
                    loc_tag = url_tag.find("loc")
                    if loc_tag and loc_tag.text:
                        all_urls.add(loc_tag.text.strip())

            break  # Found a working sitemap, stop trying alternates
        except Exception as e:
            logger.debug(f"Sitemap attempt for {sm_url}: {e}")
            continue

    documents = []
    for url in all_urls:
        if not url.lower().endswith(".pdf"):
            continue
        if not _is_likely_legal_document(url):
            continue
        parsed = urlparse(url)
        title = parsed.path.rsplit("/", 1)[-1].replace(".pdf", "").replace("%20", " ")
        documents.append({
            "title": title,
            "url": url,
            "type": "pdf",
            "source_domain": source_domain,
            "discovery_method": "sitemap",
        })

    if documents:
        logger.info(f"Sitemap: found {len(documents)} PDFs from {source_domain}")
    return documents


# ── Strategy 2: RSS/Atom ──────────────────────────────────────────────────

def _discover_via_rss(base_url: str, source_domain: str) -> List[Dict]:
    """Try to discover documents via RSS/Atom feeds."""
    feed_urls = [
        urljoin(base_url, "/feed"),
        urljoin(base_url, "/rss"),
        urljoin(base_url, "/feed.xml"),
        urljoin(base_url, "/rss.xml"),
        urljoin(base_url, "/atom.xml"),
        urljoin(base_url, "/news/feed"),
    ]

    documents = []

    for feed_url in feed_urls:
        try:
            resp = requests.get(feed_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                continue

            soup = BeautifulSoup(resp.content, "lxml-xml")

            # RSS
            for item in soup.find_all("item"):
                link = item.find("link")
                title = item.find("title")
                if link:
                    url = link.text.strip() if link.text else ""
                    title_text = title.text.strip() if title and title.text else ""
                    if url.lower().endswith(".pdf") or _is_likely_legal_document(url, title_text):
                        documents.append({
                            "title": title_text or urlparse(url).path.rsplit("/", 1)[-1],
                            "url": url,
                            "type": "pdf" if url.lower().endswith(".pdf") else "link",
                            "source_domain": source_domain,
                            "discovery_method": "rss",
                        })

            # Atom
            for entry in soup.find_all("entry"):
                link_tag = entry.find("link")
                title = entry.find("title")
                if link_tag:
                    href = link_tag.get("href", "")
                    title_text = title.text.strip() if title and title.text else ""
                    if href.lower().endswith(".pdf") or _is_likely_legal_document(href, title_text):
                        documents.append({
                            "title": title_text or urlparse(href).path.rsplit("/", 1)[-1],
                            "url": href,
                            "type": "pdf" if href.lower().endswith(".pdf") else "link",
                            "source_domain": source_domain,
                            "discovery_method": "rss",
                        })

            if documents:
                logger.info(f"RSS: found {len(documents)} items from {feed_url}")
                break
        except Exception as e:
            logger.debug(f"RSS attempt for {feed_url}: {e}")
            continue

    return documents


# ── Strategy 3: HTML Link Scanning ────────────────────────────────────────

def _discover_via_html_scan(base_url: str, source_domain: str,
                            discovery_urls: List[str] = None) -> List[Dict]:
    """Scan HTML pages for links matching legal document patterns."""
    urls_to_scan = discovery_urls or [base_url]
    all_documents: List[Dict] = []
    seen_urls: Set[str] = set()
    pages_scanned = 0

    # We only scan paths that look like legal content indexes
    legal_paths = [
        p for p in urls_to_scan
        if any(re.search(pattern, p.lower()) for pattern in LEGAL_LINK_PATTERNS)
        or p == base_url
    ]

    for page_url in legal_paths[:MAX_PAGES_TO_CRAWL]:
        if pages_scanned >= MAX_PAGES_TO_CRAWL:
            break

        try:
            resp = requests.get(page_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                continue
            pages_scanned += 1

            soup = BeautifulSoup(resp.text, "html.parser")
            links = soup.find_all("a", href=True)

            for link in links:
                href = link.get("href", "")
                if not href or href.startswith("#"):
                    continue
                full_url = _normalize_url(href, page_url)
                if full_url in seen_urls:
                    continue
                seen_urls.add(full_url)

                if len(all_documents) >= MAX_DOCUMENTS_PER_SOURCE:
                    break

                title = link.get_text().strip()
                is_pdf = full_url.lower().endswith(".pdf")

                if is_pdf or _is_likely_legal_document(full_url, title):
                    if is_pdf or any(p.search(title) for p in LEGAL_TITLE_PATTERNS):
                        all_documents.append({
                            "title": title or urlparse(full_url).path.rsplit("/", 1)[-1],
                            "url": full_url,
                            "type": "pdf" if is_pdf else "link",
                            "source_domain": source_domain,
                            "discovery_method": "html_scan",
                        })
        except Exception as e:
            logger.debug(f"HTML scan for {page_url}: {e}")
            continue

    if all_documents:
        logger.info(f"HTML scan: found {len(all_documents)} docs from {source_domain}")
    return all_documents


# ── Strategy 4: Generic PDF Discovery ─────────────────────────────────────

def _discover_via_generic(base_url: str, source_domain: str) -> List[Dict]:
    """Basic scan of the homepage for PDF links."""
    documents = []
    try:
        resp = requests.get(base_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        for link in soup.find_all("a", href=lambda h: h and h.lower().endswith(".pdf")):
            href = link.get("href")
            if not href or href.startswith("#"):
                continue
            full_url = _normalize_url(href, base_url)
            title = link.get_text().strip() or "Untitled Document"
            documents.append({
                "title": title,
                "url": full_url,
                "type": "pdf",
                "source_domain": source_domain,
                "discovery_method": "generic_pdf",
            })
    except Exception as e:
        logger.debug(f"Generic scan for {source_domain}: {e}")

    return documents


# ── Multi-Strategy Orchestrator ───────────────────────────────────────────

def multi_strategy_discover(source_url: str, source_domain: str,
                            discovery_urls: List[str] = None,
                            acquisition_status: str = "UNVERIFIED") -> List[Dict]:
    """Orchestrate all discovery strategies for a source.

    Args:
        source_url: Primary URL for the source
        source_domain: Domain name for the source
        discovery_urls: Additional discovery URLs (beyond base)
        acquisition_status: PERMITTED | RESTRICTED | UNVERIFIED

    For RESTRICTED sources, returns only metadata (no document content URLs).
    For PERMITTED/UNVERIFIED sources, returns document candidates for acquisition.
    """
    logger.info(f"Multi-strategy discovery for {source_domain} [{acquisition_status}]")

    # RESTRICTED sources: metadata discovery only — record existence, don't acquire
    if acquisition_status == "RESTRICTED":
        restricted_docs = []
        try:
            resp = requests.get(source_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                # Just count potential legal documents, don't download
                pdf_count = len(soup.find_all("a", href=lambda h: h and h.lower().endswith(".pdf")))
                page_title = soup.title.text.strip() if soup.title else source_domain
                restricted_docs.append({
                    "title": f"[RESTRICTED] {page_title} — {pdf_count} PDFs discovered, not acquired",
                    "url": source_url,
                    "type": "metadata_only",
                    "source_domain": source_domain,
                    "discovery_method": "rights_gate",
                    "_acquisition_blocked": True,
                    "_block_reason": "RESTRICTED source — metadata discovery only",
                })
                logger.info(
                    f"RESTRICTED source {source_domain}: {pdf_count} documents "
                    f"identified, 0 acquired (rights gate)"
                )
        except Exception as e:
            logger.warning(f"Restricted source check for {source_domain}: {e}")
        return restricted_docs

    # PERMITTED and UNVERIFIED sources: attempt full discovery
    all_docs: List[Dict] = []
    seen_urls: Set[str] = set()

    strategies = [
        ("sitemap", lambda: _discover_via_sitemap(source_url, source_domain)),
        ("rss", lambda: _discover_via_rss(source_url, source_domain)),
        ("html_scan", lambda: _discover_via_html_scan(source_url, source_domain, discovery_urls)),
        ("generic", lambda: _discover_via_generic(source_url, source_domain)),
    ]

    for strategy_name, strategy_fn in strategies:
        try:
            docs = strategy_fn()
            new_docs = []
            for doc in docs:
                if doc["url"] not in seen_urls:
                    seen_urls.add(doc["url"])
                    new_docs.append(doc)
            if new_docs:
                logger.info(
                    f"  {strategy_name}: {len(new_docs)} new documents from {source_domain}"
                )
            all_docs.extend(new_docs)
        except Exception as e:
            logger.warning(f"Strategy {strategy_name} failed for {source_domain}: {e}")

    # Cap
    if len(all_docs) > MAX_DOCUMENTS_PER_SOURCE:
        logger.warning(
            f"Capping {source_domain} at {MAX_DOCUMENTS_PER_SOURCE} "
            f"(found {len(all_docs)})"
        )
        all_docs = all_docs[:MAX_DOCUMENTS_PER_SOURCE]

    logger.info(
        f"Multi-strategy discovery for {source_domain}: "
        f"{len(all_docs)} documents total"
    )
    return all_docs


# ── Integration with domain handler map ───────────────────────────────────

def create_generic_domain_handler(source_key: str, source_domain: str,
                                  discovery_urls: List[str] = None,
                                  acquisition_status: str = "UNVERIFIED"):
    """Factory: create a closure that acts as a domain handler for background_workers."""
    if discovery_urls is None:
        discovery_urls = []

    def _handler(source_url: str, domain: str) -> List[Dict]:
        return multi_strategy_discover(
            source_url=source_url,
            source_domain=source_domain,
            discovery_urls=discovery_urls,
            acquisition_status=acquisition_status,
        )

    _handler.__name__ = f"_generic_handler_{source_key}"
    return _handler
