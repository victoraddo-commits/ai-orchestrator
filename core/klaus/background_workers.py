"""
KLAUS Legal Knowledge Acquisition System - Background Workers

Implements the discovery and ingestion pipeline workers that run
as background processes to crawl sources, discover documents, and
process them through the quality control pipeline.
"""

import logging
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


def _resolve_url(href: str, base_url: str) -> str:
    """Resolve relative URL against base, handling various edge cases."""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    base = base_url.rstrip("/")
    if href.startswith("/"):
        return base + href
    return base + "/" + href.lstrip("/")


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
    """Ghana-specific: scrape GhaLII (PeachJam/LII platform) for judgments and legislation.

    GhaLII blocks direct browse paths (/judgments/) with 403 but search works.
    Uses keyword searches across Ghana legal topics to discover documents.
    """
    import urllib.parse

    documents = []
    seen_urls = set()

    # Ghana legal search terms — broad coverage of legal areas
    search_terms = [
        "ghana supreme court", "ghana court of appeal", "ghana high court",
        "ghana constitution", "act of parliament ghana", "criminal ghana",
        "commercial ghana", "land ghana", "employment ghana",
        "tax ghana", "family law ghana", "contract ghana",
        "property ghana", "banking ghana", "human rights ghana",
    ]

    for term in search_terms:
        try:
            q = urllib.parse.quote(term)
            url = f"https://ghalii.org/search/?q={q}"
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
                if any(skip in href.lower() for skip in ("/search", "/about", "/contact", "#")):
                    continue

                is_content = any(p in href.lower() for p in (
                    "/judgment/", "/akn/", "/legislation/", "/node/"
                ))
                is_pdf = href.lower().endswith(".pdf")

                if is_content or is_pdf:
                    full_url = _resolve_url(href, "https://ghalii.org")
                    if full_url in seen_urls:
                        continue
                    seen_urls.add(full_url)

                    if is_pdf:
                        documents.append({
                            "title": title,
                            "url": full_url,
                            "type": "pdf",
                            "source_domain": source_domain,
                        })
                    else:
                        # Follow judgment page for PDF download links
                        try:
                            inner_r = requests.get(full_url, headers=HEADERS, timeout=15)
                            if inner_r.status_code == 200:
                                inner_soup = BeautifulSoup(inner_r.text, "html.parser")
                                for inner_link in inner_soup.find_all("a", href=True):
                                    ihref = inner_link.get("href", "")
                                    if ihref.lower().endswith(".pdf"):
                                        doc_title = inner_link.get_text().strip() or title
                                        pdf_url = _resolve_url(ihref, full_url)
                                        if pdf_url not in seen_urls:
                                            seen_urls.add(pdf_url)
                                            documents.append({
                                                "title": doc_title,
                                                "url": pdf_url,
                                                "type": "pdf",
                                                "source_domain": source_domain,
                                            })
                        except Exception:
                            pass
        except Exception as e:
            logger.warning(f"GhaLII search '{term}' failed: {e}")

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
    """Ghana Parliament Repository scraper — https://repository.parliament.gh/

    The Parliament repository runs on DSpace, which exposes a REST API.
    Strategy:
    1. REST API: /rest/collections → enumerate collections → /rest/items
    2. Fallback: HTML scrape of the repository home page for links
    3. Also try the showPDF pattern (legacy parliament.gh)
    """
    documents = []
    session = requests.Session()
    session.headers.update({**HEADERS, "Accept": "application/json, text/html,*/*"})

    # Strategy 1: DSpace REST API discovery
    try:
        # Get collections
        collections_url = "https://repository.parliament.gh/rest/collections"
        resp = session.get(collections_url, timeout=30)
        if resp.status_code == 200:
            try:
                collections = resp.json()
                for coll in collections[:20]:  # Limit to first 20 collections
                    coll_id = coll.get("id") or coll.get("uuid")
                    if not coll_id:
                        continue
                    # Get items in this collection
                    items_url = f"https://repository.parliament.gh/rest/collections/{coll_id}/items"
                    items_resp = session.get(items_url, timeout=30)
                    if items_resp.status_code != 200:
                        continue
                    items = items_resp.json()
                    for item in items[:50]:  # Limit per collection
                        item_name = item.get("name", "")
                        item_id = item.get("id") or item.get("uuid")
                        if not item_name or not item_id:
                            continue
                        # Get bitstreams (PDFs) for this item
                        try:
                            bs_url = f"https://repository.parliament.gh/rest/items/{item_id}/bitstreams"
                            bs_resp = session.get(bs_url, timeout=30)
                            if bs_resp.status_code == 200:
                                for bs in bs_resp.json():
                                    bs_name = bs.get("name", "")
                                    if bs_name.lower().endswith(".pdf"):
                                        bs_id = bs.get("id") or bs.get("uuid")
                                        documents.append({
                                            "title": f"[Parliament] {item_name}",
                                            "url": f"https://repository.parliament.gh/rest/bitstreams/{bs_id}/retrieve",
                                            "type": "pdf",
                                            "source_domain": source_domain,
                                        })
                        except Exception:
                            pass
            except Exception:
                pass
    except Exception as e:
        logger.info(f"Parliament REST API discovery: {e}")

    # Strategy 2: Fall back to HTML scraping of the repository home page
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
        except Exception as e:
            logger.debug(f"Parliament HTML fallback: {e}")

    # Strategy 3: Also try legacy parliament.gh showPDF pattern
    legacy_docs = _discover_parliament_gh("https://www.parliament.gh", source_domain)
    documents.extend(legacy_docs)

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


# Per-domain discovery handlers
_DOMAIN_HANDLERS = {
    "parliament.gh": _discover_parliament_repository,
    "ghalii.org": _discover_ghalii,
    "judicial.gov.gh": _discover_ejudgment_gh,
    "ghanapublishing.gov.gh": _discover_ghanapublishing_gh,
}


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
        response = requests.get(source_url, headers=HEADERS, timeout=30)
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
    """
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        filename = urlparse(url).path.split('/')[-1] or "unnamed_document"
        return response.content, filename
    except Exception as e:
        logger.warning(f"Failed to download {url}: {e}")
        return None


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
                    logger.info(f"Found {len(discovered_docs)} documents from {source['domain']}")

                    if discovered_docs:
                        # Process discovered documents
                        processed = process_discovered_documents(
                            source_id=source["id"],
                            source_url=source["url"],
                            source_domain=source["domain"],
                            documents=discovered_docs,
                        )

                        logger.info(f"Processed {processed} documents from {source['domain']}")

                        # Log discovery event
                        log_audit_event(
                            "discovery", "info",
                            f"Discovered {len(discovered_docs)} documents, processed {processed} successfully",
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