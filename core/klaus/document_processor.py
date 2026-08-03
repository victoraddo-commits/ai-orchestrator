"""
KLAUS Legal Knowledge Acquisition System - Document Processing Pipeline

Implements the full ingestion pipeline per document:
discovery -> download -> verification -> processing -> metadata -> indexing

Features:
- SHA-256 duplicate detection (never silently overwrite)
- PDF extraction (pdfplumber + pypdf fallback)
- OCR via pytesseract (images embedded in PDFs)
- Text cleaning, section/article/citation detection
- Legal metadata extraction
- Version control with amendment history
- Hash verification against download integrity
- Copyright-based access control (full_storage vs metadata_only)
"""

import hashlib
import json
import re
import io
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.klaus.db_manager import (
    STORAGE_ROOT,
    compute_file_hash,
    insert_document,
    insert_chunk,
    get_document_by_hash,
    update_document_review_status,
    log_audit_event,
)

RAW_DIR = STORAGE_ROOT / "raw"
PROCESSED_DIR = STORAGE_ROOT / "processed"

CITATION_RE = re.compile(
    r"(?:Article|Section|s\.|Art\.)\s+(\d+(?:\(\d+[a-z]?\))?)",
    re.IGNORECASE,
)
CASE_CITATION_RE = re.compile(
    r"\[\d{4}(?:-\d{4})?\]\s*(?:\d+\s+)?(?:G\.?M\.?)?\s*(?:S\.?C\.?G\.?L\.?R\.?|G\.?L\.?R\.?)",
    re.IGNORECASE,
)
LEGISLATION_RE = re.compile(
    r"(?:Act|P\.?N\.?D\.?C\.?L\.?|L\.?I\.?)\s+(\d{1,4})",
    re.IGNORECASE,
)

CHUNK_SIZE = 800
CHUNK_OVERLAP = 100

JURISDICTION_SIGNALS = {
    "ghana": "Ghana",
    "nigeria": "Nigeria",
    "kenya": "Kenya",
    "south africa": "South Africa",
    "tanzania": "Tanzania",
    "uganda": "Uganda",
    "ecowas": "ECOWAS",
    "au": "African Union",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def extract_text_from_pdf(pdf_bytes: bytes) -> Tuple[str, bool]:
    """
    Extract text from PDF bytes. Returns (text, used_ocr).
    Tries pdfplumber first, falls back to pypdf, then OCR.
    """
    text = ""
    used_ocr = False

    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages = []
            for page in pdf.pages:
                t = page.extract_text()
                pages.append(t or "")
            text = "\n\n".join(pages).strip()
    except Exception:
        text = ""

    if not text:
        try:
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
            pages = []
            for page in reader.pages:
                t = page.extract_text() or ""
                pages.append(t)
            text = "\n\n".join(pages).strip()
        except Exception:
            text = ""

    if not text:
        try:
            import pytesseract
            from PIL import Image
            import pdfplumber

            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                ocr_pages = []
                for page in pdf.pages:
                    img = page.to_image(resolution=300)
                    ocr_text = pytesseract.image_to_string(img.original, lang="eng")
                    ocr_pages.append(ocr_text or "")
                text = "\n\n".join(ocr_pages).strip()
                if text:
                    used_ocr = True
        except Exception:
            pass

    return text, used_ocr


def extract_text_from_txt(content_bytes: bytes) -> str:
    return content_bytes.decode("utf-8", errors="replace")


def clean_text(text: str) -> str:
    text = re.sub(r"\s*\n\s*\n\s*", "\n\n", text)
    text = re.sub(r"[\t ]+", " ", text)
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def detect_jurisdiction(text: str) -> str:
    lower = text[:4000].lower()
    for signal, name in JURISDICTION_SIGNALS.items():
        if signal in lower:
            return name
    return "Ghana"


def extract_citations(text: str) -> Dict[str, List[str]]:
    return {
        "articles": list(set(m.group(0) for m in CITATION_RE.finditer(text))),
        "cases": list(set(m.group(0) for m in CASE_CITATION_RE.finditer(text))),
        "legislation": list(set(m.group(0) for m in LEGISLATION_RE.finditer(text))),
    }


def classify_document_by_keywords(text: str) -> str:
    lower = text[:5000].lower()
    if "constitution" in lower and ("supreme law" in lower or "chapter" in lower or "sovereign" in lower):
        return "Constitutional Law"
    if "act of parliament" in lower or "enacted by parliament" in lower or "assented to" in lower:
        return "Legislation"
    if "judgment" in lower or "ruling" in lower or "plaintiff" in lower or "defendant" in lower or "appeal" in lower:
        return "Judiciary"
    if "procedure" in lower or "rules of court" in lower or "civil procedure" in lower or "criminal procedure" in lower:
        return "Legal Procedure"
    if "treaty" in lower or "convention" in lower or "ratif" in lower or "protocol" in lower:
        return "International Law"
    return "Legal Scholarship"


def classify_copyright(text: str, source_url: str) -> Tuple[str, str]:
    lower = text[:5000].lower()
    url_lower = source_url.lower() if source_url else ""

    if any(d in url_lower for d in (".gov.gh", "parliament.gh", "judiciary.gov.gh", ".gov")):
        return "official_public_access", "full_storage"

    if "public domain" in lower or "no rights reserved" in lower:
        return "public_domain", "full_storage"

    if "creative commons" in lower or "cc-by" in lower or "open access" in lower:
        return "open_license", "full_storage"

    if "all rights reserved" in lower or "copyright" in lower:
        return "copyright_protected", "metadata_only"

    return "unknown", "metadata_only"


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    paragraphs = text.split("\n\n")
    chunks = []
    current = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(current) + len(para) + 2 <= chunk_size:
            current = (current + "\n\n" + para) if current else para
        else:
            if current:
                chunks.append(current)
            current = para

    if current:
        chunks.append(current)

    return chunks


def process_document(
    content: bytes,
    filename: str,
    source_id: int,
    source_url: str,
    jurisdiction: Optional[str] = None,
    legislation_number: Optional[str] = None,
    year: Optional[int] = None,
    court: Optional[str] = None,
    effective_date: Optional[str] = None,
    perform_ocr: bool = True,
    bypass_copyright: bool = False,
) -> Dict:
    """
    Full ingestion pipeline for a single document.
    Returns a dict with status and details.
    """
    file_hash = compute_file_hash(content)

    existing = get_document_by_hash(file_hash)
    if existing:
        return {
            "status": "duplicate",
            "document_id": existing["id"],
            "message": "Document already ingested",
            "hash": file_hash,
        }

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    raw_path = RAW_DIR / f"{timestamp}_{file_hash[:12]}_{filename}"
    raw_path.write_bytes(content)

    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        text, used_ocr = extract_text_from_pdf(content)
    else:
        text = extract_text_from_txt(content)
        used_ocr = False

    text = clean_text(text)
    jur = jurisdiction or detect_jurisdiction(text)
    category = classify_document_by_keywords(text)
    copyright_cls, access_level = classify_copyright(text, source_url)

    if bypass_copyright and copyright_cls in ("copyright_protected", "unknown"):
        access_level = "full_storage"

    citations = extract_citations(text)

    try:
        doc_id = insert_document(
            source_id=source_id,
            title=filename,
            file_hash=file_hash,
            file_path=str(raw_path),
            category=category,
            jurisdiction=jur,
            copyright_classification=copyright_cls,
            access_level=access_level,
            court=court,
            year=year,
            legislation_number=legislation_number,
            effective_date=effective_date,
        )
    except Exception as e:
        # If we have an existing document and encounter an error, flag it
        if existing:
            update_document_review_status(existing["id"], "flagged")
        raise

    log_audit_event("discovery", "info", f"Ingested {filename}", doc_id)
    log_audit_event(
        "classification",
        "info",
        f"Classified as {category}, copyright={copyright_cls}, access={access_level}",
        doc_id,
    )

    if access_level == "metadata_only":
        log_audit_event(
            "review",
            "warning",
            f"Copyright={copyright_cls}; metadata-only. Operator review needed.",
            doc_id,
        )
        if copyright_cls == "unknown":
            update_document_review_status(doc_id, "flagged")

    chunks = chunk_text(text) if access_level == "full_storage" else []
    chunk_ids = []
    for i, chunk_text_val in enumerate(chunks):
        cid = insert_chunk(
            document_id=doc_id,
            chunk_index=i,
            content=chunk_text_val,
            metadata={"citations": citations, "source_url": source_url},
        )
        chunk_ids.append(cid)

    processed_path = PROCESSED_DIR / f"{timestamp}_{file_hash[:12]}.txt"
    processed_path.write_text(text)

    return {
        "status": "ingested",
        "document_id": doc_id,
        "file_hash": file_hash,
        "category": category,
        "jurisdiction": jur,
        "copyright_classification": copyright_cls,
        "access_level": access_level,
        "chunks_count": len(chunks),
        "chunk_ids": chunk_ids,
        "used_ocr": used_ocr,
        "citations_found": {k: len(v) for k, v in citations.items()},
        "text_length": len(text),
    }


_thread_pool = ThreadPoolExecutor(max_workers=4)


def process_document_async(content: bytes, filename: str, source_id: int, source_url: str, **kwargs):
    return _thread_pool.submit(process_document, content, filename, source_id, source_url, **kwargs)
