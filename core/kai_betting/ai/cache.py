"""Kai Betting — deterministic evidence hashing + result cache.

Avoids repeatedly paying to re-analyze an unchanged match: the router computes
a deterministic hash of the material evidence and reuses the prior result
unless the underlying data changed or the TTL expired.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Optional, Any


def data_hash(evidence: Any) -> str:
    """Deterministic SHA256 of a JSON-serializable evidence package."""
    canonical = json.dumps(evidence, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class InferenceCache:
    """Minimal TTL cache keyed by an arbitrary string.

    In-memory only: the worker is a single process on a ~300s cycle, and the
    TTL (hours) is short relative to a fixture's lifetime. A restart simply
    re-analyzes once.
    """

    def __init__(self, default_ttl_seconds: int = 6 * 3600):
        self._store: dict = {}
        self._default_ttl = default_ttl_seconds

    def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at is not None and time.monotonic() > expires_at:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
        ttl = self._default_ttl if ttl_seconds is None else ttl_seconds
        expires_at = None if ttl <= 0 else time.monotonic() + ttl
        self._store[key] = (value, expires_at)

    def clear(self) -> None:
        self._store.clear()
