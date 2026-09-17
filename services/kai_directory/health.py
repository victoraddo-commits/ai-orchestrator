from __future__ import annotations

import ipaddress
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from .models import Record

_BLOCKED_HOSTS = {"169.254.169.254", "100.100.100.200", "metadata.google.internal"}


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
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
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
