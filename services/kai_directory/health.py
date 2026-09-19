from __future__ import annotations

import ipaddress
import os
import ssl
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from .models import Record

_BLOCKED_HOSTS = {"169.254.169.254", "100.100.100.200", "metadata.google.internal"}

# Explicit allowlist of internal hosts whose health endpoint presents a
# self-signed certificate. Only these hosts get a no-verify TLS context --
# TLS verification is never disabled globally. Configure a comma-separated
# list via KAI_DIRECTORY_INSECURE_TLS_HOSTS (hostnames or IPs).
_INSECURE_TLS_ENV = "KAI_DIRECTORY_INSECURE_TLS_HOSTS"


def _insecure_tls_hosts() -> set[str]:
    raw = os.environ.get(_INSECURE_TLS_ENV, "")
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def _tls_context_for(parsed: urllib.parse.ParseResult) -> ssl.SSLContext | None:
    """Return a no-verify context only for allowlisted HTTPS hosts."""
    if parsed.scheme != "https":
        return None
    host = (parsed.hostname or "").lower()
    if host not in _insecure_tls_hosts():
        return None
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def classify(code) -> str:
    if code is None:
        return "down"
    return "up" if code < 500 else "down"


def _allowed(url: str) -> bool:
    try:
        p = urllib.parse.urlparse(url)
    except Exception:
        return False
    if p.scheme not in ("http", "https"):
        return False
    host = (p.hostname or "").lower()
    if host in _BLOCKED_HOSTS:
        return False
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_link_local:
            return False
    except ValueError:
        pass
    return True


def check_one(rec: Record, timeout: float = 5.0) -> str:
    url = rec.health_url or rec.target_url
    if not url or not _allowed(url):
        return "unknown" if not url else "down"
    try:
        parsed = urllib.parse.urlparse(url)
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(
            req, timeout=timeout, context=_tls_context_for(parsed)
        ) as r:
            return classify(r.status)
    except urllib.error.HTTPError as e:
        return classify(e.code)
    except Exception:
        return "down"


def check_all(records: list[Record], timeout: float = 3.0) -> list[Record]:
    if not records:
        return records
    with ThreadPoolExecutor(max_workers=8) as ex:
        statuses = list(ex.map(lambda r: check_one(r, timeout), records))
    for r, s in zip(records, statuses):
        r.health_status = s
    return records
