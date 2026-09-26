"""KAI OCR client — calls the VM112 OCR service, with local fallback.

The KLAUS document processor runs on CT111 (4 vCPU / 4 GB). OCR there is slow
and destabilises the PVE-B host. This client offloads OCR to the dedicated
service on VM112 (16 cores) when reachable, and degrades gracefully to the
local tesseract path when it is not — so ingestion never hard-fails.
"""
from __future__ import annotations

import logging
import os
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_URL = os.environ.get("KAI_OCR_URL", "http://192.168.1.242:8090")
TIMEOUT = float(os.environ.get("KAI_OCR_TIMEOUT", "1800"))
ENABLED = os.environ.get("KAI_OCR_REMOTE", "1") not in ("0", "false", "no")


def remote_available(url: str = DEFAULT_URL, timeout: float = 5.0) -> bool:
    """True when the VM112 OCR service answers /health."""
    if not ENABLED:
        return False
    try:
        import urllib.request
        with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def ocr_pdf(pdf_bytes: bytes, *, dpi: Optional[int] = None,
            lang: str = "eng") -> Tuple[str, bool]:
    """OCR a PDF via the remote service. Returns (text, used_remote).

    Returns ("", False) when the service is unreachable or errors, so the
    caller can fall back to the local path.
    """
    if not ENABLED:
        return "", False
    url = DEFAULT_URL.rstrip("/") + "/ocr"
    if dpi:
        url += f"?dpi={int(dpi)}&lang={lang}"
    try:
        import urllib.request
        req = urllib.request.Request(
            url, data=pdf_bytes,
            headers={"Content-Type": "application/pdf",
                     "Content-Length": str(len(pdf_bytes))},
            method="POST")
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            import json
            d = json.load(r)
            if d.get("ok") and d.get("text"):
                logger.info("OCR via VM112 (%s): %d pages in %ss",
                            d.get("engine"), d.get("pages"), d.get("seconds"))
                return d["text"], True
            logger.warning("VM112 OCR returned not-ok: %s", str(d.get("error"))[:150])
    except Exception as e:  # noqa: BLE001
        logger.warning("VM112 OCR unreachable (%s) — falling back to local",
                       type(e).__name__)
    return "", False
