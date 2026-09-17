from __future__ import annotations

import urllib.request

from .models import Record


def classify(code) -> str:
    if code is None:
        return "down"
    return "up" if code < 500 else "down"


def check_one(rec: Record, timeout: float = 5.0) -> str:
    url = rec.health_url or rec.target_url
    if not url:
        return "unknown"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return classify(r.status)
    except urllib.error.HTTPError as e:
        return classify(e.code)
    except Exception:
        return "down"


def check_all(records: list[Record], timeout: float = 5.0) -> list[Record]:
    for r in records:
        r.health_status = check_one(r, timeout)
    return records
