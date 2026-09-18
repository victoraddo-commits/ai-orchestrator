"""Best-effort event notifications for the Media Revenue Factory.

Pipeline events are POSTed to the orchestrator's existing ``/notify`` endpoint
(``core/notify_endpoint.py``). Authentication uses that endpoint's own bearer
mechanism:

* ``MEDIA_NOTIFY_TOKEN`` or ``KAI_NOTIFY_TOKEN`` when set, else
* the first non-comment line of ``KAI_NOTIFY_TOKENS_FILE`` when configured.

When no token is configured anywhere the endpoint runs in open mode, so a
placeholder bearer header is sent. Any failure (timeout, connection error,
403) degrades to a log line: notifications must never break a media cycle.
"""
from __future__ import annotations

import json
import logging
import os
import ssl
import urllib.request

logger = logging.getLogger(__name__)

DEFAULT_URL = os.environ.get("MEDIA_NOTIFY_URL", "https://127.0.0.1:8000/notify")
TIMEOUT = float(os.environ.get("MEDIA_NOTIFY_TIMEOUT", "5"))
SOURCE = "media-factory"

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def _token() -> str:
    for name in ("MEDIA_NOTIFY_TOKEN", "KAI_NOTIFY_TOKEN"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    path = os.environ.get("KAI_NOTIFY_TOKENS_FILE", "")
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        return line
        except OSError:
            pass
    return "media-factory"


def emit(severity: str, title: str, message: str) -> bool:
    """POST one event to ``/notify``. Returns True on HTTP 2xx; never raises."""
    payload = json.dumps({
        "source": SOURCE,
        "severity": severity,  # "info" | "warn" | "critical"
        "title": title,
        "message": message,
    }).encode()
    request = urllib.request.Request(
        DEFAULT_URL,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + _token(),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT, context=_CTX) as resp:
            return 200 <= resp.status < 300
    except Exception as exc:  # noqa: BLE001 - notify is best-effort
        logger.warning("media notify failed (%s): %s", type(exc).__name__, title)
        return False
