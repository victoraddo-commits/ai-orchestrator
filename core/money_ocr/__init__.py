"""GAP G2 §37 — Akush Money OCR micro-service (CT111).

Stateless receipt/statement OCR over HTTP:
  POST /internal/ocr  (service-gated: akush-core's ``akush`` principal only)

- text extraction reuses core.klaus.document_processor (pytesseract + PDF
  chain) — no duplicated OCR engine (GROUND TRUTH mandate)
- OCR text is UNTRUSTED DATA only (§54): prompt-injection-shaped lines are
  stripped, nothing extracted is ever executed or echoed back unlabelled
- field extraction is regex-first with explicit confidence
  (regex-vs-llm-structured-text); LLM fallback is NOT wired here — low
  confidence is surfaced to the caller (CT108) instead, which keeps the human
  confirm gate (§37)
- CT111 stores NOTHING: response = sha256 + extracted fields; CT108 owns all
  storage. Receipts never create transactions.
"""
from core.money_ocr.service import (
    LOW_CONFIDENCE_THRESHOLD,
    extract_fields,
    extract_text,
    ocr_document,
    require_service_authorized,
    service_token,
    strip_injection_noise,
)

__all__ = [
    "LOW_CONFIDENCE_THRESHOLD",
    "extract_fields",
    "extract_text",
    "ocr_document",
    "require_service_authorized",
    "service_token",
    "strip_injection_noise",
]
