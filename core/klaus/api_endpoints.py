"""
KLAUS Legal Knowledge Acquisition System - API Endpoints

FastAPI router providing REST endpoints for the KLAUS legal knowledge system:
- Source management (seeds, scanning)
- Document upload & ingestion pipeline
- Document listing, searching, and retrieval
- Quality control agent review
- Monitoring dashboard metrics
- Operator review/approval workflow

Integrated into core/api.py via `app.include_router(klaus_router, prefix="/klaus")`.
"""

import json
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Query, Request, Depends

from core.bridge_auth import require_bridge_token
from core.klaus.db_manager import (
    add_source,
    list_sources,
    get_source,
    update_source_status,
    list_documents,
    get_document,
    get_document_by_hash,
    get_chunks_for_document,
    update_document_review_status,
    get_documents_flagged_for_review,
    get_audit_logs,
    log_audit_event,
    get_storage_estimate,
    count_documents_by_status,
)
from core.klaus.document_processor import (
    process_document,
    RAW_DIR,
)
from core.klaus.quality_agents import run_all_agents
from core.klaus.vector_indexer import (
    index_document_chunks,
    search_similar,
    get_storage_stats,
)
from core.klaus.scheduler import trigger_job_now
from core.klaus.schema import (
    KNOWLEDGE_CATEGORIES,
    COPYRIGHT_CLASSIFICATIONS,
    EVENT_TYPES,
)

klaus_router = APIRouter(
    prefix="/klaus",
    tags=["klaus"],
    dependencies=[Depends(require_bridge_token)],
)


# ── Sources ────────────────────────────────────────────────────────────

@klaus_router.get("/sources")
async def api_list_sources(
    tier: Optional[int] = None,
    status: Optional[str] = None,
    jurisdiction: Optional[str] = None,
):
    return list_sources(tier=tier, status=status, jurisdiction=jurisdiction)


@klaus_router.post("/sources")
async def api_add_source(request: Request):
    body = await request.json()
    url = body.get("url")
    domain = body.get("domain")
    tier = body.get("tier")
    jurisdiction = body.get("jurisdiction", "Ghana")

    if not url or not domain or tier is None:
        raise HTTPException(400, "url, domain, and tier are required")

    if tier not in (1, 2, 3):
        raise HTTPException(400, "tier must be 1, 2, or 3")

    source_id = add_source(url, domain, tier, jurisdiction)
    return {"id": source_id, "url": url}


@klaus_router.put("/sources/{source_id}/status")
async def api_update_source_status(source_id: int, request: Request):
    body = await request.json()
    status = body.get("status")
    reliability = body.get("reliability_score")

    if not status:
        raise HTTPException(400, "status is required")

    source = get_source(source_id)
    if not source:
        raise HTTPException(404, "Source not found")

    update_source_status(source_id, status, reliability)
    return {"id": source_id, "status": status}


# ── Documents ──────────────────────────────────────────────────────────

@klaus_router.get("/documents")
async def api_list_documents(
    category: Optional[str] = None,
    review_status: Optional[str] = None,
    copyright_classification: Optional[str] = None,
    jurisdiction: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
):
    return list_documents(
        category=category,
        review_status=review_status,
        copyright_classification=copyright_classification,
        jurisdiction=jurisdiction,
        limit=limit,
        offset=offset,
    )


@klaus_router.get("/documents/flagged")
async def api_flagged_documents():
    return get_documents_flagged_for_review()


@klaus_router.get("/documents/{document_id}")
async def api_get_document(document_id: int):
    doc = get_document(document_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    return doc


@klaus_router.get("/documents/{document_id}/chunks")
async def api_get_document_chunks(document_id: int):
    doc = get_document(document_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    return get_chunks_for_document(document_id)


@klaus_router.put("/documents/{document_id}/review")
async def api_review_document(document_id: int, request: Request):
    body = await request.json()
    review_status = body.get("review_status")
    if review_status not in ("pending", "approved", "rejected", "flagged"):
        raise HTTPException(400, "Invalid review_status")

    doc = get_document(document_id)
    if not doc:
        raise HTTPException(404, "Document not found")

    update_document_review_status(document_id, review_status)
    log_audit_event(
        "review", "info",
        f"Operator set review_status={review_status}",
        document_id,
    )
    return {"id": document_id, "review_status": review_status}


# ── Ingestion ──────────────────────────────────────────────────────────

@klaus_router.post("/ingest")
async def api_ingest_document(request: Request):
    """
    Ingest a legal document. Expects JSON body with content (base64-encoded
    bytes), filename, source_id, and source_url. Optional: jurisdiction,
    court, year, legislation_number, bypass_copyright.
    """
    import base64

    try:
        body = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(400, "Request body must be valid JSON")

    content_b64 = body.get("content")
    filename = body.get("filename")
    source_id = body.get("source_id")
    source_url = body.get("source_url", "")

    if not content_b64 or not filename or source_id is None:
        raise HTTPException(400, "content, filename, and source_id are required")

    if get_source(source_id) is None:
        raise HTTPException(400, f"Source {source_id} not found in catalog")

    try:
        content = base64.b64decode(content_b64)
    except Exception:
        raise HTTPException(400, "content must be valid base64")

    result = process_document(
        content=content,
        filename=filename,
        source_id=source_id,
        source_url=source_url,
        jurisdiction=body.get("jurisdiction"),
        court=body.get("court"),
        year=body.get("year"),
        legislation_number=body.get("legislation_number"),
        effective_date=body.get("effective_date"),
        bypass_copyright=body.get("bypass_copyright", False),
    )

    return result


# ── Quality Control Agents ─────────────────────────────────────────────

@klaus_router.post("/documents/{document_id}/verify")
async def api_verify_document(document_id: int):
    doc = get_document(document_id)
    if not doc:
        raise HTTPException(404, "Document not found")

    results = run_all_agents(document_id)
    return {"document_id": document_id, "results": results}


# ── Vector Indexing ────────────────────────────────────────────────────

@klaus_router.post("/documents/{document_id}/index")
async def api_index_document(document_id: int):
    doc = get_document(document_id)
    if not doc:
        raise HTTPException(404, "Document not found")

    if doc["access_level"] != "full_storage":
        raise HTTPException(400, "Cannot index metadata_only document")

    count = index_document_chunks(document_id)
    return {"document_id": document_id, "chunks_indexed": count}


@klaus_router.get("/search")
async def api_search(
    q: str = Query(..., description="Search query text"),
    limit: int = 10,
    threshold: float = 0.5,
):
    if not q.strip():
        raise HTTPException(400, "Query string is required")
    results = search_similar(q, limit=limit, threshold=threshold)
    return {"query": q, "results": results, "count": len(results)}


# ── Scheduling ─────────────────────────────────────────────────────────

@klaus_router.post("/scheduler/trigger/{job_id}")
async def api_trigger_job(job_id: str):
    valid_jobs = {"klaus_daily", "klaus_weekly", "klaus_monthly", "klaus_quarterly"}
    if job_id not in valid_jobs:
        raise HTTPException(400, f"Invalid job_id. Valid: {sorted(valid_jobs)}")

    success = trigger_job_now(job_id)
    if not success:
        raise HTTPException(500, "Failed to trigger job")
    return {"job_id": job_id, "triggered": True}


# ── Monitoring Dashboard ───────────────────────────────────────────────

@klaus_router.get("/monitoring")
async def api_monitoring():
    stats = get_storage_stats()
    status_counts = count_documents_by_status()

    return {
        "storage": stats,
        "documents": {
            "by_status": status_counts,
            "flagged_count": len(get_documents_flagged_for_review()),
        },
        "categories": list(KNOWLEDGE_CATEGORIES),
        "copyright_classifications": list(COPYRIGHT_CLASSIFICATIONS),
        "event_types": list(EVENT_TYPES),
    }


@klaus_router.get("/audit-logs")
async def api_audit_logs(
    event_type: Optional[str] = None,
    severity: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
):
    return get_audit_logs(
        event_type=event_type,
        severity=severity,
        limit=limit,
        offset=offset,
    )


# ── Schema Reference ───────────────────────────────────────────────────

@klaus_router.get("/reference/categories")
async def api_reference_categories():
    return {"categories": list(KNOWLEDGE_CATEGORIES)}


@klaus_router.get("/reference/copyright")
async def api_reference_copyright():
    return {"classifications": list(COPYRIGHT_CLASSIFICATIONS)}
