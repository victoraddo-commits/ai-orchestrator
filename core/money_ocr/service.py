"""Akush Money OCR service core (GAP G2 §37). Stateless; no persistence.

Everything extracted from an uploaded file is treated as untrusted DATA:
prompt-injection-shaped lines are removed before parsing and no extraction
result ever feeds an execution path (§54). Confidence is an explicit opinion
value the caller (akush-core) must preserve; anything low-confidence stays
pending until a human confirms (§37).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("kai.money_ocr")

MAX_OCR_BYTES = 10 * 1024 * 1024  # mirrors akush-core's 10MB upload cap
ALLOWED_MIME_PREFIXES = ("image/",)
ALLOWED_MIME_EXACT = ("application/pdf",)
INERT_MIME_FALLBACK = "application/octet-stream"

LOW_CONFIDENCE_THRESHOLD = 0.60

# §54: OCR text is data. Lines that look like instructions to a model are
# stripped before parsing so they can never influence anything downstream.
INJECTION_RX = re.compile(
    r"(?:\b(?:ignore|disregard|forget)\b[^.\n]{0,48}\b(?:previous|prior|above|earlier|instructions)\b)"
    r"|(?:^\s*system\s*[:=])"
    r"|(?:^\s*assistant\s*[:=])"
    r"|(?:\byou\s+are\s+now\b)"
    r"|(?:^\s*###\s*(?:system|instruction))",
    re.IGNORECASE | re.MULTILINE,
)

# GHS-aware money shapes: GH₵ / GHS / Ghc / bare ₵ / plain decimals.
CURRENCY_GHS_RX = re.compile(r"(?:GH\s*[₵c]|GHS|\u20b5)", re.IGNORECASE)
CURRENCY_LABEL_RX = re.compile(
    r"\b(GH\u20b5|GHS|Ghc|GH\u20b5|\u20b5|USD|EUR|GBP|NGN|\$|€|£)\b", re.IGNORECASE
)
AMOUNT_RX = re.compile(
    r"(?<![0-9])([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})|[0-9]+\.[0-9]{2})(?![0-9])"
)
TOTAL_KEYWORD_RX = re.compile(
    r"\b(?:grand\s+total|total\s+due|amount\s+due|total|amount|balance\s+due)\b"
    r"[^\d\n]{0,24}"
    r"(?:GH\s*[₵c]|GHS|\u20b5)?\s*",
    re.IGNORECASE,
)
DATE_RXS = [
    re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"),
    re.compile(r"\b(\d{1,2}/\d{1,2}/\d{4})\b"),
    re.compile(r"\b(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\b"),
    re.compile(r"\b(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4})\b", re.IGNORECASE),
    re.compile(r"\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4})\b", re.IGNORECASE),
]
REFERENCE_RX = re.compile(
    r"\b(?:receipt\s*(?:no|number|num)?|ref(?:erence)?|invoice\s*(?:no|number)?|txn|trans(?:action)?\s*id)\s*"
    r"[#:]?\s*([A-Za-z0-9][A-Za-z0-9/\-_.]{2,31})",
    re.IGNORECASE,
)
MERCHANT_LABEL_RX = re.compile(
    r"\b(?:merchant|seller|vendor|store|from|shop)\s*[:\-]\s*(.+)", re.IGNORECASE
)
# Lines we never treat as the merchant: totals, dates, refs, phone numbers.
MERCHANT_EXCLUDE_RX = re.compile(
    r"^\s*(?:total|amount|subtotal|vat|tax|date|receipt|ref|tel|phone|cash|change|thank)"
    r"|^[0-9/.\-]+$",
    re.IGNORECASE,
)

REDACT_RX = re.compile(r"[A-Z0-9]{10,}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact(value: str) -> str:
    """Log-safe form of a value: shape kept, long opaque runs masked."""
    return REDACT_RX.sub("<masked>", value[:60])


# ---------------------------------------------------------------------------
# service-token gating (vault secrets/money/service_tokens -> ``akush``)
# ---------------------------------------------------------------------------
_svc_cache: tuple[str, float] | None = None


def service_token() -> str:
    """Resolve the ``akush`` principal's bearer token.

    Sources: SERVICE_TOKENS env (JSON, test seam) else kai-vault machine
    plane. Never logged; empty string on failure (fail closed).
    """
    global _svc_cache
    if _svc_cache and (datetime.now(timezone.utc).timestamp() - _svc_cache[1]) < 300:
        return _svc_cache[0]
    tok = ""
    raw = os.environ.get("SERVICE_TOKENS", "")
    if not raw:
        try:
            from core.ai import kai_vault_client as _vault

            token = _vault.load_token()
            if token:
                raw = _vault.fetch_secret("secrets/money/service_tokens", token) or ""
        except Exception as exc:  # noqa: BLE001
            logger.warning("money_ocr service-token fetch failed: %s", type(exc).__name__)
            raw = ""
    if raw:
        try:
            tok = (json.loads(raw) or {}).get("akush") or ""
        except Exception:
            tok = ""
    _svc_cache = (tok, datetime.now(timezone.utc).timestamp())
    return tok


def require_service_authorized(authorization: Optional[str]) -> str:
    """Dependency body for POST /internal/ocr.

    Only akush-core's ``akush`` principal may call this. 401 without/with a
    wrong bearer; returns the principal string on success.
    """
    expected = service_token()
    if not expected:
        logger.error("money_ocr: akush service token unavailable — failing closed")
        raise PermissionError("service unavailable")
    if not authorization or not authorization.startswith("Bearer "):
        raise PermissionError("unauthorized")
    supplied = authorization[len("Bearer "):].strip()
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise PermissionError("unauthorized")
    return "service:akush"


# ---------------------------------------------------------------------------
# text extraction — reuses the KLAUS document processor (no duplicate engine)
# ---------------------------------------------------------------------------
def extract_text(data: bytes, filename: str, mime: str) -> Tuple[str, bool]:
    """Return (text, used_ocr). PDFs go through the KLAUS chain (remote
    VM112 first, local pdfplumber/pypdf/tesseract fallback); images go
    straight through pytesseract."""
    if data[:5] == b"%PDF-" or mime == "application/pdf" or filename.lower().endswith(".pdf"):
        from core.klaus.document_processor import extract_text_from_pdf

        text, used_ocr = extract_text_from_pdf(data)
        return text, used_ocr

    from PIL import Image
    import io as _io
    import pytesseract

    try:
        img = Image.open(_io.BytesIO(data))
        img.load()
    except Exception as exc:  # noqa: BLE001
        raise ValueError("unreadable image") from exc
    os.environ.setdefault("OMP_THREAD_LIMIT", "4")
    cfg = os.environ.get("KLAUS_OCR_TESSERACT_CONFIG", "--oem 1 --psm 6")
    text = pytesseract.image_to_string(img, lang="eng", config=cfg) or ""
    return text.strip(), bool(text.strip())


def strip_injection_noise(text: str) -> Tuple[str, int]:
    """Remove prompt-injection-shaped lines. Text is DATA only (§54)."""
    kept: List[str] = []
    removed = 0
    for line in text.splitlines():
        if INJECTION_RX.search(line):
            removed += 1
            continue
        kept.append(line)
    return "\n".join(kept), removed


# ---------------------------------------------------------------------------
# regex field extraction with confidence (regex-first; no LLM by default)
# ---------------------------------------------------------------------------
def _clean_amount(raw: str) -> str:
    return raw.replace(",", "")


def _text_quality(text: str) -> float:
    """Cheap OCR-output quality signal in [0,1]."""
    if not text:
        return 0.0
    sample = text[:800]
    printable = sum(1 for ch in sample if ch.isprintable() or ch in "\n\r\t")
    ratio = printable / max(1, len(sample))
    alnum = sum(1 for ch in sample if ch.isalnum()) / max(1, len(sample))
    return round(min(1.0, 0.5 * ratio + 0.5 * min(1.0, alnum * 2)), 2)


def extract_fields(text: str) -> Tuple[List[Dict], float, bool]:
    """Regex extraction with per-field confidence.

    Returns (fields, overall_confidence, low_confidence). fields is a list of
    {name, value, confidence}; OCR opinions, never facts.
    """
    fields: List[Dict] = []
    quality = _text_quality(text)
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # --- currency (GHS-aware) ---
    if CURRENCY_GHS_RX.search(text):
        fields.append({"name": "currency", "value": "GHS", "confidence": 0.9 * quality})
    else:
        m = CURRENCY_LABEL_RX.search(text)
        if m:
            val = m.group(1).upper()
            val = {"GH₵": "GHS", "GHC": "GHS", "₵": "GHS", "$": "USD", "€": "EUR", "£": "GBP"}.get(val, val)
            fields.append({"name": "currency", "value": val, "confidence": 0.7 * quality})

    # --- amounts: every decimal money-looking token ---
    amounts: List[str] = [_clean_amount(m.group(1)) for m in AMOUNT_RX.finditer(text)]
    if amounts:
        fields.append({"name": "amounts", "value": amounts, "confidence": 0.75 * quality})

    # --- total: keyword-anchored amount, else labelled fallback ---
    total: Optional[Dict] = None
    for m in TOTAL_KEYWORD_RX.finditer(text):
        tail = text[m.end():m.end() + 24]
        a = AMOUNT_RX.search(tail)
        if a:
            total = {"name": "total", "value": _clean_amount(a.group(1)), "confidence": 0.9 * quality}
            break
    if total is None and amounts:
        # no keyword anchor — largest amount is a weak opinion only
        try:
            biggest = max(amounts, key=float)
        except ValueError:
            biggest = amounts[0]
        total = {"name": "total", "value": biggest, "confidence": 0.5 * quality}
    if total is not None:
        fields.append(total)

    # --- date ---
    for rx in DATE_RXS:
        m = rx.search(text)
        if m:
            fields.append({"name": "date", "value": m.group(1), "confidence": 0.8 * quality})
            break

    # --- reference / receipt number ---
    m = REFERENCE_RX.search(text)
    if m:
        fields.append({"name": "reference", "value": m.group(1), "confidence": 0.85 * quality})

    # --- merchant ---
    merchant: Optional[Dict] = None
    for m in MERCHANT_LABEL_RX.finditer(text):
        val = m.group(1).strip()
        if val:
            merchant = {"name": "merchant", "value": val[:120], "confidence": 0.9 * quality}
            break
    if merchant is None:
        for line in lines[:5]:  # top-of-receipt heuristic
            if MERCHANT_EXCLUDE_RX.search(line):
                continue
            if len(line) >= 3 and any(ch.isalpha() for ch in line):
                merchant = {"name": "merchant", "value": line[:120], "confidence": 0.55 * quality}
                break
    if merchant is not None:
        fields.append(merchant)

    confidences = [f["confidence"] for f in fields if f["name"] != "amounts"]
    overall = round(sum(confidences) / len(confidences), 2) if confidences else 0.0
    return fields, overall, overall < LOW_CONFIDENCE_THRESHOLD


def ocr_document(data: bytes, filename: str, mime: str) -> Dict:
    """Full stateless OCR pass. Raises ValueError for unreadable/oversized
    input; everything else returns a labelled opinion envelope."""
    if len(data) > MAX_OCR_BYTES:
        raise ValueError(f"file too large (max {MAX_OCR_BYTES} bytes)")
    if not data:
        raise ValueError("empty upload")
    mime = (mime or INERT_MIME_FALLBACK).lower()
    if not (mime.startswith(ALLOWED_MIME_PREFIXES) or mime in ALLOWED_MIME_EXACT):
        raise ValueError(f"unsupported mime type: {mime[:60]}")

    raw_text, used_ocr = extract_text(data, filename, mime)
    text, stripped = strip_injection_noise(raw_text)
    fields, overall, low = extract_fields(text)
    if stripped:
        logger.info("money_ocr: stripped %d injection-shaped line(s) sha=%s",
                    stripped, hashlib.sha256(data).hexdigest()[:12])
    return {
        "sha256": hashlib.sha256(data).hexdigest(),
        "text_len": len(text),
        "fields": fields,
        "confidence": overall,
        "low_confidence": low,
        "used_ocr": used_ocr,
        "injection_lines_stripped": stripped,
        "processed_at": _now(),
    }
