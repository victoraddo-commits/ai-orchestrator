"""Knowledge Fabric — API (Command Center management plane).

Exposes spaces, documents, versions, ACL grants, audit, and permission-aware
search over the Knowledge Fabric store. Mounted by ``core/api.py``.

Principal comes from the (auth-gated) Command Center session: header
``X-Kai-User`` (default ``operator``); ``X-Kai-Admin: 1`` for admin.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from core.knowledge.model import KnowledgeTier, Principal, Visibility
from core.knowledge.store import KnowledgeStore

knowledge_router = APIRouter(prefix="/knowledge", tags=["knowledge-fabric"])

_STORE = KnowledgeStore(os.environ.get(
    "KAI_KNOWLEDGE_DB", "/opt/ai-orchestrator/memory/knowledge_fabric.db"))


def _principal(request: Request) -> Principal:
    uid = request.headers.get("x-kai-user", "operator")
    return Principal(uid, space_ids=_STORE.space_ids_for(uid),
                     is_system_admin=request.headers.get("x-kai-admin") == "1")


class SpaceBody(BaseModel):
    name: str
    description: str | None = None
    type: str = "shared"
    visibility: str = "PRIVATE"


class MemberBody(BaseModel):
    user_id: str
    role: str = "READER"


class DocBody(BaseModel):
    title: str
    content: str = ""
    visibility: str = "PRIVATE"
    space_id: str | None = None


class VersionBody(BaseModel):
    content: str


class ACLBody(BaseModel):
    principal_type: str = "user"
    principal_id: str
    role: str = "READER"


class SearchBody(BaseModel):
    q: str = ""


@knowledge_router.get("/health")
def health():
    n = _STORE.db.execute("SELECT COUNT(*) c FROM kf_documents").fetchone()["c"]
    return {"ok": True, "documents": n}


@knowledge_router.post("/spaces")
def create_space(body: SpaceBody, request: Request):
    p = _principal(request)
    sid = _STORE.create_space(body.name, p.user_id, type=body.type,
                              description=body.description,
                              visibility=Visibility(body.visibility.upper()))
    return {"space_id": sid}


@knowledge_router.get("/spaces")
def list_spaces(request: Request):
    p = _principal(request)
    rows = _STORE.db.execute("SELECT * FROM kf_spaces").fetchall()
    return {"spaces": [dict(r) for r in rows if p.is_system_admin
                        or r["id"] in p.space_ids]}


@knowledge_router.post("/spaces/{space_id}/members")
def add_member(space_id: str, body: MemberBody, request: Request):
    _STORE.add_member(space_id, body.user_id, body.role, actor=_principal(request).user_id)
    return {"ok": True}


@knowledge_router.post("/documents")
def create_document(body: DocBody, request: Request):
    p = _principal(request)
    if body.space_id and body.space_id not in p.space_ids and not p.is_system_admin:
        raise HTTPException(status_code=403, detail="not a member of that space")
    did = _STORE.create_document(p.user_id, body.title, body.content,
                                 space_id=body.space_id,
                                 visibility=Visibility(body.visibility.upper()),
                                 tier=KnowledgeTier.SECONDARY)
    return {"document_id": did}


@knowledge_router.get("/documents")
def list_documents(request: Request):
    return {"documents": _STORE.list_readable(_principal(request))}


@knowledge_router.post("/documents/{doc_id}/versions")
def add_version(doc_id: str, body: VersionBody, request: Request):
    try:
        v = _STORE.add_version(doc_id, body.content, _principal(request).user_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="document not found")
    return {"version": v}


@knowledge_router.post("/documents/{doc_id}/grant")
def grant(doc_id: str, body: ACLBody, request: Request):
    _STORE.grant(doc_id, body.principal_type, body.principal_id, body.role,
                 actor=_principal(request).user_id)
    return {"ok": True}


@knowledge_router.post("/documents/{doc_id}/revoke")
def revoke(doc_id: str, body: ACLBody, request: Request):
    _STORE.revoke(doc_id, body.principal_type, body.principal_id, body.role,
                  actor=_principal(request).user_id)
    return {"ok": True}


@knowledge_router.post("/documents/upload")
async def upload_document(request: Request, file: UploadFile = File(...),
                          title: str = Form(None), visibility: str = Form("PRIVATE"),
                          space_id: str = Form(None)):
    """Upload a document (untrusted input). Text is extracted and stored; the
    original bytes are retained server-side. Never executed."""
    data = await file.read()
    text = _extract_text(data, file.filename or "")
    p = _principal(request)
    if space_id and space_id not in p.space_ids and not p.is_system_admin:
        raise HTTPException(status_code=403, detail="not a member of that space")
    did = _STORE.create_document(
        p.user_id, title or (file.filename or "uploaded document"), text,
        space_id=space_id, visibility=Visibility(visibility.upper()),
        tier=KnowledgeTier.SECONDARY, mime_type=file.content_type or "application/octet-stream")
    return {"document_id": did, "extracted_chars": len(text),
            "filename": file.filename}


def _extract_text(data: bytes, filename: str) -> str:
    """Best-effort text extraction (PDF via pypdf if available; else UTF-8)."""
    if filename.lower().endswith(".pdf"):
        try:
            import io

            from pypdf import PdfReader  # optional
            reader = PdfReader(io.BytesIO(data))
            return "\n\n".join((pg.extract_text() or "") for pg in reader.pages)
        except Exception:  # noqa: BLE001
            return ""
    try:
        return data.decode("utf-8", "ignore")
    except Exception:  # noqa: BLE001
        return ""


@knowledge_router.post("/search")
def search(body: SearchBody, request: Request):
    return {"results": _STORE.search(_principal(request), body.q)}


@knowledge_router.post("/query")
def query(body: SearchBody, request: Request):
    """Unified PRIMARY + authorized SECONDARY retrieval with authority/conflict."""
    return _STORE.query(_principal(request), body.q)


@knowledge_router.post("/context")
def context(body: SearchBody, request: Request):
    """Grounded RAG context (primary+secondary, citations) for a query."""
    from core.knowledge.context import build_context
    return build_context(_STORE, _principal(request), body.q)


@knowledge_router.get("/audit")
def audit(request: Request, limit: int = 100):
    rows = _STORE.db.execute("SELECT * FROM kf_audit ORDER BY id DESC LIMIT ?",
                             (min(limit, 500),)).fetchall()
    return {"audit": [dict(r) for r in rows]}
