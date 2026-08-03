"""
KLAUS Legal Knowledge Acquisition System - Background Workers

Async background processing for legal document ingestion, quality
control, and vector indexing. Runs as part of the KLAUS subsystem.
"""

import asyncio
import logging
from pathlib import Path

from core.klaus.document_processor import process_document
from core.klaus.quality_agents import run_all_agents
from core.klaus.vector_indexer import index_document_chunks
from core.klaus.db_manager import (
    list_documents,
    get_document,
    log_audit_event,
    RAW_DIR,
    add_source,
)

logger = logging.getLogger(__name__)

SCAN_EXTENSIONS = {".pdf", ".txt", ".md"}


async def discover_and_ingest_directory(
    directory: Path,
    source_id: int,
    source_url: str,
    jurisdiction: str = "Ghana",
):
    """
    Scan a directory for legal documents and ingest them.
    Skips files that have already been ingested (detected by hash).
    """
    if not directory.exists():
        logger.warning("KLAUS: Directory not found: %s", directory)
        return {"discovered": 0, "ingested": 0, "skipped": 0, "errors": 0}

    files = [f for f in directory.iterdir() if f.is_file() and f.suffix.lower() in SCAN_EXTENSIONS]
    discovered = len(files)
    ingested = 0
    skipped = 0
    errors = 0

    for filepath in files:
        try:
            content = filepath.read_bytes()
            result = process_document(
                content=content,
                filename=filepath.name,
                source_id=source_id,
                source_url=source_url,
                jurisdiction=jurisdiction,
            )

            if result["status"] == "duplicate":
                skipped += 1
            elif result["status"] == "ingested":
                ingested += 1

                if result["chunks_count"] > 0:
                    try:
                        index_document_chunks(result["document_id"])
                    except Exception as e:
                        logger.warning("KLAUS: Failed to index doc %s: %s", result["document_id"], e)

                try:
                    run_all_agents(result["document_id"])
                except Exception as e:
                    logger.warning("KLAUS: QC agents failed for doc %s: %s", result["document_id"], e)

            else:
                errors += 1

        except Exception as e:
            logger.error("KLAUS: Failed to process %s: %s", filepath.name, e)
            errors += 1

    return {
        "discovered": discovered,
        "ingested": ingested,
        "skipped": skipped,
        "errors": errors,
    }


async def process_pending_reviews():
    """Run QC agents on all pending documents."""
    docs = list_documents(review_status="pending")
    for doc in docs:
        try:
            run_all_agents(doc["id"])
        except Exception as e:
            logger.warning("KLAUS: QC failed for doc %s: %s", doc["id"], e)

    log_audit_event("review", "info", f"Processed {len(docs)} pending documents for QC")
    return len(docs)
