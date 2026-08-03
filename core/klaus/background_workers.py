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
)
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


def discover_source_content(source_url: str, source_domain: str) -> List[Dict]:
    """
    Discover and parse content from a source URL.
    Returns list of document candidates with metadata.
    """
    try:
        # Simple HTML parsing for now
        response = requests.get(source_url, timeout=30)
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
                # Run quality control agents
                agent_results = run_all_agents(result["document_id"])
                
                # Update document with final status
                document_approved = agent_results.get("overall") == "approved"
                
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
    Run the discovery worker that periodically scans sources for new documents.
    """
    logger.info("KLAUS Discovery Worker started")
    
    while True:
        try:
            # Scan all active sources
            sources = list_sources(tier=None, status="active")
            logger.info(f"Discovery Worker: Scanning {len(sources)} active sources")
            
            for source in sources:
                try:
                    # Skip sources that have been scanned within the last hour
                    # This is a simplified approach - in a real system, you'd track last scan times
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