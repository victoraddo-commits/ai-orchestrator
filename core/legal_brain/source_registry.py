"""Legal Brain — Source Management registry (KAI 2.0 phase 18E).

CRUD-lite registry of external legal sources (statute repositories, court
reporters, journal indexes, gazette feeds). Records live in
``memory/legal_sources.json`` with schema::

    {
      "schema_version": 1,
      "records": [
        {
          "id": "<12 hex>",
          "url": "<canonical url>",
          "name": "<human name>",
          "trust_score": <0..100>,
          "tier": <1..16>,
          "added_at": "<iso>",
          "active": true|false,
          "last_check": {"reachable": bool, "http_status": int|null, "checked_at": "<iso>"}
                       | null
        }
      ]
    }

``add_source`` is idempotent on URL (returns the existing record if a match
already exists, active or soft-deleted). ``remove_source`` flips ``active``
to False rather than dropping the row so history is preserved.
``validate_source`` performs an HTTP HEAD with a 5-second timeout using
httpx (already in requirements) and stores the result on the record.
"""
from __future__ import annotations

import ipaddress
import socket
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from core.memory import load, save


def _url_is_safe_to_probe(url: str) -> tuple[bool, str]:
    """SSRF guard for validate_source().

    Only http/https URLs whose hostname resolves to a GLOBAL routable
    address may be probed. Rejects loopback (127.x, ::1), private (RFC1918,
    fc00::/7), link-local (169.254.0.0/16, fe80::/10), reserved, multicast,
    and unspecified addresses. Returns (allowed, reason).
    """
    try:
        parsed = urlparse(url)
    except Exception as e:
        return False, f"invalid url: {e}"
    if parsed.scheme not in ("http", "https"):
        return False, f"scheme not allowed: {parsed.scheme!r}"
    host = parsed.hostname
    if not host:
        return False, "no hostname"
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception as e:
        return False, f"dns lookup failed: {e}"
    for info in infos:
        sockaddr = info[4]
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except Exception:
            continue
        if not ip.is_global or ip.is_loopback or ip.is_link_local or \
           ip.is_private or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False, f"non-global address {ip}"
    return True, "ok"

_STORE = "legal_sources.json"
_SCHEMA_VERSION = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict[str, Any]:
    data = load(_STORE)
    if not isinstance(data, dict) or "records" not in data:
        data = {"schema_version": _SCHEMA_VERSION, "records": []}
    if not isinstance(data.get("records"), list):
        data["records"] = []
    return data


def _save(data: dict[str, Any]) -> None:
    data.setdefault("schema_version", _SCHEMA_VERSION)
    save(_STORE, data)


def _canonical_url(url: str) -> str:
    return url.strip().rstrip("/").lower()


def list_sources(include_inactive: bool = True) -> list[dict[str, Any]]:
    """Return all source records, newest first. Set ``include_inactive`` to
    False to filter out soft-deleted rows."""
    data = _load()
    recs = list(data["records"])
    if not include_inactive:
        recs = [r for r in recs if r.get("active", True)]
    recs.sort(key=lambda r: r.get("added_at", ""), reverse=True)
    return recs


def get_source(source_id: str) -> dict[str, Any] | None:
    """Fetch one source by id, or None if missing."""
    for r in _load()["records"]:
        if r.get("id") == source_id:
            return r
    return None


def add_source(
    url: str,
    name: str,
    trust_score: int | float,
    tier: int,
) -> dict[str, Any]:
    """Register a source. If ``url`` already exists (case/trailing-slash
    normalised), return that record instead of adding a duplicate — the
    caller can update fields with a follow-up if that becomes needed.
    """
    if not url or not url.strip():
        raise ValueError("url is required")
    if not name or not name.strip():
        raise ValueError("name is required")
    canon = _canonical_url(url)

    data = _load()
    for r in data["records"]:
        if _canonical_url(r.get("url", "")) == canon:
            # Reactivate soft-deleted duplicates so `add` is idempotent.
            if not r.get("active", True):
                r["active"] = True
            return r

    record = {
        "id": uuid.uuid4().hex[:12],
        "url": url.strip(),
        "name": name.strip(),
        "trust_score": int(max(0, min(100, trust_score))),
        "tier": int(tier) if isinstance(tier, (int, float)) else None,
        "added_at": _now_iso(),
        "active": True,
        "last_check": None,
    }
    data["records"].append(record)
    _save(data)
    return record


def remove_source(source_id: str) -> dict[str, Any] | None:
    """Soft-delete: flip ``active`` to False. Returns the updated record or
    ``None`` if no such id."""
    data = _load()
    for r in data["records"]:
        if r.get("id") == source_id:
            r["active"] = False
            _save(data)
            return r
    return None


def validate_source(source_id: str, http_client: Any = None) -> dict[str, Any]:
    """HTTP HEAD reachability check with a 5-second timeout. Stores the
    result on the record. Returns ``{reachable, http_status, checked_at}``
    or ``{error: "not_found"}`` if the source id is unknown.

    ``http_client`` is injected in tests; production path uses httpx.
    """
    data = _load()
    target = None
    for r in data["records"]:
        if r.get("id") == source_id:
            target = r
            break
    if target is None:
        return {"error": "not_found"}

    url = target.get("url", "")
    reachable = False
    http_status: int | None = None

    # SSRF guard: refuse to probe non-global / private / loopback / link-local
    # addresses. Prevents this endpoint from being turned into a scanner for
    # internal services.
    allowed, reason = _url_is_safe_to_probe(url)
    if not allowed:
        check = {
            "reachable": False,
            "http_status": None,
            "checked_at": _now_iso(),
            "error": f"blocked: {reason}",
        }
        target["last_check"] = check
        _save(data)
        return check

    if http_client is None:
        try:
            import httpx as _httpx
            http_client = _httpx
        except Exception as e:
            check = {
                "reachable": False,
                "http_status": None,
                "checked_at": _now_iso(),
                "error": f"httpx unavailable: {e}",
            }
            target["last_check"] = check
            _save(data)
            return check

    try:
        # follow_redirects=False: each hop can re-target a private IP, so we
        # only trust the initial (validated) hostname.
        response = http_client.head(url, timeout=5.0, follow_redirects=False)
        http_status = int(response.status_code)
        reachable = 200 <= http_status < 400
    except Exception as e:
        check = {
            "reachable": False,
            "http_status": None,
            "checked_at": _now_iso(),
            "error": type(e).__name__,
        }
        target["last_check"] = check
        _save(data)
        return check

    check = {
        "reachable": reachable,
        "http_status": http_status,
        "checked_at": _now_iso(),
    }
    target["last_check"] = check
    _save(data)
    return check


def trust_score_breakdown() -> dict[str, int]:
    """Bucketed trust-score histogram — used by the dashboard summary."""
    buckets = {"90-100": 0, "70-89": 0, "50-69": 0, "0-49": 0}
    for r in list_sources(include_inactive=False):
        s = int(r.get("trust_score") or 0)
        if s >= 90: buckets["90-100"] += 1
        elif s >= 70: buckets["70-89"] += 1
        elif s >= 50: buckets["50-69"] += 1
        else: buckets["0-49"] += 1
    return buckets
