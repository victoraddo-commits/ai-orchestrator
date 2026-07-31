"""Storage and text extraction for uploaded legal documents (textbooks,
lecture notes, case judgments, statutes) -- feeds the Law Tutor bot's
library. Deliberately a standalone module with no operational imports of
its own, so both core.api (the operator-facing upload endpoint) and
core.law_tutor (which reads extracted text to ground answers) can import it
without core.law_tutor's own import graph ever reaching an operational
module.

Storage: ~/.ai-orchestrator/law_documents/<id>/ (original file + extracted
.txt), outside the git repo, same pattern as self-build-workspaces/. This is
LOCAL disk on the same host ai-orchestrator runs on -- see the 2026-07-31
infra analysis (uploaded to /uploads) on why that's a short-term choice
pending a decision on relocating to separate, better-provisioned storage
before volume grows.
"""

import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from core.memory import update


def _default_documents_dir():
    override = os.environ.get("AI_ORCHESTRATOR_LAW_DOCUMENTS_DIR")
    return Path(override) if override else Path.home() / ".ai-orchestrator" / "law_documents"


DOCUMENTS_DIR = _default_documents_dir()
INDEX_FILE = "law_documents.json"

MAX_FILE_BYTES = 50 * 1024 * 1024  # 50MB per file -- generous for a single textbook PDF

ALLOWED_EXTENSIONS = {".pdf", ".txt", ".md"}


class DocumentTooLarge(ValueError):
    pass


class UnsupportedFileType(ValueError):
    pass


def _extract_text(path, suffix):
    if suffix == ".pdf":
        import pypdf
        reader = pypdf.PdfReader(str(path))
        pages = []
        for page in reader.pages:
            try:
                pages.append(page.extract_text() or "")
            except Exception:
                pages.append("")
        return "\n\n".join(pages).strip()
    # .txt / .md
    return path.read_text(errors="replace")


def save_document(filename, content_bytes, category=None, jurisdiction=None, uploaded_by=None):
    if len(content_bytes) > MAX_FILE_BYTES:
        raise DocumentTooLarge(f"{filename} is {len(content_bytes)} bytes, over the {MAX_FILE_BYTES} limit")

    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise UnsupportedFileType(f"{suffix or '(no extension)'} is not supported -- allowed: {sorted(ALLOWED_EXTENSIONS)}")

    doc_id = uuid.uuid4().hex[:12]
    doc_dir = DOCUMENTS_DIR / doc_id
    doc_dir.mkdir(parents=True, exist_ok=True)

    original_path = doc_dir / f"original{suffix}"
    original_path.write_bytes(content_bytes)

    try:
        text = _extract_text(original_path, suffix)
    except Exception as error:
        text = ""
        extraction_error = str(error)
    else:
        extraction_error = None

    text_path = doc_dir / "extracted.txt"
    text_path.write_text(text)

    record = {
        "id": doc_id,
        "filename": filename,
        "category": category,
        "jurisdiction": jurisdiction,
        "uploaded_by": uploaded_by,
        "uploaded_at": datetime.now().isoformat(),
        "size_bytes": len(content_bytes),
        "extracted_chars": len(text),
        "extraction_error": extraction_error,
    }

    def mutate(index):
        index = index if isinstance(index, dict) else {"schema_version": 1, "records": []}
        index.setdefault("records", []).append(record)
        return index

    update(INDEX_FILE, mutate)
    return record


def list_documents():
    from core.memory import load
    index = load(INDEX_FILE)
    if not isinstance(index, dict):
        return []
    return index.get("records", [])


def get_document(doc_id):
    for record in list_documents():
        if record["id"] == doc_id:
            return record
    return None


def get_document_text(doc_id, max_chars=None):
    doc_dir = DOCUMENTS_DIR / doc_id
    text_path = doc_dir / "extracted.txt"
    if not text_path.exists():
        return None
    text = text_path.read_text()
    return text[:max_chars] if max_chars else text


def delete_document(doc_id):
    def mutate(index):
        index = index if isinstance(index, dict) else {"schema_version": 1, "records": []}
        index["records"] = [r for r in index.get("records", []) if r["id"] != doc_id]
        return index

    existed = get_document(doc_id) is not None
    update(INDEX_FILE, mutate)

    doc_dir = DOCUMENTS_DIR / doc_id
    if doc_dir.exists():
        shutil.rmtree(doc_dir, ignore_errors=True)

    return existed
