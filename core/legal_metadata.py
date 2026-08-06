"""Legal document metadata, version control, and immutable storage with audit trail.

Phase 17O-C: Metadata schema (jurisdiction, court, year, citation, judge,
parties, status), version control for documents, and immutable content-addressable
storage with an append-only audit trail.

Storage layout:
  ~/.ai-orchestrator/legal_documents/<sha256_hash>  -- immutable content
  memory/legal_metadata.json                        -- metadata + version chain (atomic)
  memory/legal_audit_trail.json                     -- append-only audit log (atomic)
"""

import hashlib
import os
import stat
from datetime import datetime
from pathlib import Path

from core.id_generator import generate_id
from core.memory import load, update

VALID_STATUSES = ("current", "overruled", "amended")

STATUS_TRANSITIONS = {
    "current":  ["overruled", "amended"],
    "overruled": ["current"],
    "amended":   ["current"],
}


class InvalidStatus(ValueError):
    """Raised when a status value is not in VALID_STATUSES or the transition is not allowed."""


class DocumentNotFound(LookupError):
    """Raised when a document ID does not correspond to any stored document."""


class ImmutableViolation(RuntimeError):
    """Raised when attempting to modify an immutable document file."""


# ── Paths ───────────────────────────────────────────────────────────

def _default_documents_dir():
    override = os.environ.get("AI_ORCHESTRATOR_LEGAL_DOCUMENTS_DIR")
    return Path(override) if override else Path.home() / ".ai-orchestrator" / "legal_documents"


DOCUMENTS_DIR = _default_documents_dir()
METADATA_FILE = "legal_metadata.json"
AUDIT_FILE = "legal_audit_trail.json"


# ── Internal helpers ────────────────────────────────────────────────

def _digest(content_bytes):
    return hashlib.sha256(content_bytes).hexdigest()


def _store_immutable(content_bytes, doc_hash):
    doc_dir = DOCUMENTS_DIR
    doc_dir.mkdir(parents=True, exist_ok=True)
    file_path = doc_dir / doc_hash

    if file_path.exists():
        existing = file_path.read_bytes()
        if _digest(existing) != doc_hash:
            raise ImmutableViolation(
                f"Existing file at {file_path} does not match hash {doc_hash}"
            )
        return

    tmp_path = file_path.with_suffix(file_path.suffix + f".tmp.{os.getpid()}")
    try:
        with open(tmp_path, "wb") as f:
            f.write(content_bytes)
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(tmp_path), str(file_path))
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    file_path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)


def _load_metadata_index():
    index = load(METADATA_FILE)
    if not isinstance(index, dict):
        return {"schema_version": 1, "records": []}
    index.setdefault("records", [])
    return index


def _find_record(doc_id):
    for rec in _load_metadata_index()["records"]:
        if rec["id"] == doc_id:
            return rec
    return None


def _require_document(doc_id):
    record = _find_record(doc_id)
    if record is None:
        raise DocumentNotFound(f"No document with id {doc_id!r}")
    return record


def _append_audit(entry):
    def mutate(audit):
        audit = audit if isinstance(audit, dict) else {"schema_version": 1, "records": []}
        audit.setdefault("records", []).append(entry)
        return audit

    update(AUDIT_FILE, mutate)


# ── Public API ──────────────────────────────────────────────────────

def register_document(content, jurisdiction, court, year, citation, judge, parties,
                      status, registered_by=None):
    if status not in VALID_STATUSES:
        raise InvalidStatus(f"status must be one of {VALID_STATUSES}, got {status!r}")

    content_bytes = content if isinstance(content, bytes) else content.encode("utf-8")
    doc_hash = _digest(content_bytes)
    _store_immutable(content_bytes, doc_hash)

    now = datetime.now().isoformat()
    doc_id = generate_id()

    record = {
        "id": doc_id,
        "trace_id": doc_id,
        "jurisdiction": jurisdiction,
        "court": court,
        "year": year,
        "citation": citation,
        "judge": judge,
        "parties": parties,
        "status": status,
        "document_hash": doc_hash,
        "version": 1,
        "parent_document_id": None,
        "created": now,
        "updated": now,
        "history": [{"status": status, "timestamp": now}],
    }

    def mutate(index):
        index = index if isinstance(index, dict) else {"schema_version": 1, "records": []}
        index.setdefault("records", []).append(record)
        return index

    update(METADATA_FILE, mutate)

    _append_audit({
        "action": "register_document",
        "document_id": doc_id,
        "jurisdiction": jurisdiction,
        "citation": citation,
        "document_hash": doc_hash,
        "actor": registered_by,
        "timestamp": now,
    })

    return record


def list_documents():
    return _load_metadata_index()["records"]


def get_document(doc_id):
    return _find_record(doc_id)


def update_document_status(doc_id, new_status, changed_by=None):
    if new_status not in VALID_STATUSES:
        raise InvalidStatus(f"status must be one of {VALID_STATUSES}, got {new_status!r}")

    record = _require_document(doc_id)
    current_status = record["status"]

    allowed = STATUS_TRANSITIONS.get(current_status, [])
    if new_status not in allowed:
        raise InvalidStatus(
            f"Cannot transition from {current_status!r} to {new_status!r}. "
            f"Allowed: {allowed}"
        )

    now = datetime.now().isoformat()
    note = f"Status changed: {current_status} -> {new_status}"

    def mutate(index):
        index = index if isinstance(index, dict) else {"schema_version": 1, "records": []}
        for rec in index.setdefault("records", []):
            if rec["id"] == doc_id:
                rec["status"] = new_status
                rec["updated"] = now
                rec.setdefault("history", []).append({
                    "status": new_status, "timestamp": now, "note": note,
                })
                return index
        return index

    updated = update(METADATA_FILE, mutate)

    _append_audit({
        "action": "update_status",
        "document_id": doc_id,
        "previous_status": current_status,
        "new_status": new_status,
        "actor": changed_by,
        "timestamp": now,
    })

    for rec in updated.get("records", []):
        if rec["id"] == doc_id:
            return rec

    raise DocumentNotFound(f"Document {doc_id!r} vanished during update")


def create_version(parent_doc_id, content, registered_by=None):
    parent = _require_document(parent_doc_id)

    content_bytes = content if isinstance(content, bytes) else content.encode("utf-8")
    doc_hash = _digest(content_bytes)
    _store_immutable(content_bytes, doc_hash)

    now = datetime.now().isoformat()
    doc_id = generate_id()

    record = {
        "id": doc_id,
        "trace_id": parent["trace_id"],
        "jurisdiction": parent["jurisdiction"],
        "court": parent.get("court"),
        "year": parent.get("year"),
        "citation": parent["citation"],
        "judge": parent.get("judge"),
        "parties": parent.get("parties"),
        "status": parent["status"],
        "document_hash": doc_hash,
        "version": parent["version"] + 1,
        "parent_document_id": parent_doc_id,
        "created": now,
        "updated": now,
        "history": [{"status": parent["status"], "timestamp": now}],
    }

    def mutate(index):
        index = index if isinstance(index, dict) else {"schema_version": 1, "records": []}
        index.setdefault("records", []).append(record)
        return index

    update(METADATA_FILE, mutate)

    _append_audit({
        "action": "create_version",
        "document_id": doc_id,
        "parent_id": parent_doc_id,
        "document_hash": doc_hash,
        "version": record["version"],
        "actor": registered_by,
        "timestamp": now,
    })

    return record


def get_version_history(doc_id):
    record = _find_record(doc_id)
    if record is None:
        return []

    chain = [record]
    current = record
    while current.get("parent_document_id"):
        parent = _find_record(current["parent_document_id"])
        if parent is None:
            break
        chain.insert(0, parent)
        current = parent

    return chain


def get_document_content(doc_id):
    record = _find_record(doc_id)
    if record is None:
        return None

    doc_path = DOCUMENTS_DIR / record["document_hash"]
    if not doc_path.exists():
        return None

    content = doc_path.read_bytes()
    if _digest(content) != record["document_hash"]:
        raise ImmutableViolation(
            f"Document {doc_id!r} hash mismatch — content has been tampered"
        )

    return content


def get_audit_trail():
    audit = load(AUDIT_FILE)
    if not isinstance(audit, dict):
        return []
    return audit.get("records", [])
