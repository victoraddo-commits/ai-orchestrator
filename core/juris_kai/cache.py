"""Bounded TTL caches for Juris Kai's hot path (local-only).

Two process-local caches remove the most expensive repeated work in the bot:

  * ``GENERATION_CACHE`` — full model answers keyed by
    ``(task_type, normalized query, corpus version)`` so an identical or
    near-identical legal question returns instantly on the second ask.
  * ``RETRIEVAL_CACHE`` — legal-brain search hits keyed by
    ``(normalized query, limit)`` so prompt assembly does not re-hit the
    retrieval service for a query already answered.

Design notes:
  * Nothing here talks to a provider, a cloud API, or Postgres. The only
    outbound call is the (short, cached) legal-brain ``/health`` probe used
    to stamp the corpus version, and a failure there degrades to ``"na"``.
  * LRU eviction + TTL keep memory bounded; a monotonic-ish wall clock is
    used because entries are process-local and short-lived.
  * All mutation is guarded by a lock: the Telegram poll loop and the
    FastAPI worker threads share one process.
"""

from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict

DEFAULT_MAXSIZE = 512
DEFAULT_TTL = 3600.0  # 1 hour for generated answers

RETRIEVAL_MAXSIZE = 1024
RETRIEVAL_TTL = 900.0  # 15 minutes for retrieval hits

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def normalize_query(text: str) -> str:
    """Fold a query to a stable cache key.

    Lowercases, strips punctuation, and collapses whitespace so
    "What is  Contract Law?" and "what is contract law" share a key.
    """
    q = (text or "").strip().lower()
    q = _PUNCT_RE.sub(" ", q)
    return _WS_RE.sub(" ", q).strip()


class TTLCache:
    """Thread-safe bounded cache with per-entry TTL and LRU eviction."""

    def __init__(self, maxsize: int = DEFAULT_MAXSIZE, ttl: float = DEFAULT_TTL,
                 name: str = "cache"):
        self.maxsize = maxsize
        self.ttl = ttl
        self.name = name
        self._data: "OrderedDict[object, tuple[object, float | None]]" = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._sets = 0
        self._evictions = 0
        self._expirations = 0

    def get(self, key):
        """Return the cached value, or None on miss/expiry."""
        now = time.time()
        with self._lock:
            item = self._data.get(key)
            if item is None:
                self._misses += 1
                return None
            value, expires_at = item
            if expires_at is not None and expires_at <= now:
                del self._data[key]
                self._expirations += 1
                self._misses += 1
                return None
            self._data.move_to_end(key)
            self._hits += 1
            return value

    def set(self, key, value, ttl: float | None = None) -> None:
        ttl = self.ttl if ttl is None else ttl
        expires_at = None if ttl is None or ttl <= 0 else time.time() + ttl
        with self._lock:
            self._data[key] = (value, expires_at)
            self._data.move_to_end(key)
            self._sets += 1
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)
                self._evictions += 1

    def clear(self) -> int:
        with self._lock:
            removed = len(self._data)
            self._data.clear()
            return removed

    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            return {
                "name": self.name,
                "size": len(self._data),
                "maxsize": self.maxsize,
                "ttl_seconds": self.ttl,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(self._hits / total, 4) if total else 0.0,
                "sets": self._sets,
                "evictions": self._evictions,
                "expirations": self._expirations,
            }


GENERATION_CACHE = TTLCache(maxsize=DEFAULT_MAXSIZE, ttl=DEFAULT_TTL,
                            name="generation")
RETRIEVAL_CACHE = TTLCache(maxsize=RETRIEVAL_MAXSIZE, ttl=RETRIEVAL_TTL,
                           name="retrieval")

# Corpus version stamp: a cached, cheap fingerprint of the legal corpus so a
# freshly-ingested document invalidates generated answers. Never blocks long —
# a probe failure simply yields "na".
_corpus_version: dict = {"value": None, "expires": 0.0}
_CORPUS_VERSION_TTL = 30.0


def corpus_version(max_age: float = _CORPUS_VERSION_TTL) -> str:
    now = time.time()
    cached = _corpus_version.get("value")
    if cached is not None and now < _corpus_version.get("expires", 0.0):
        return cached
    version = "na"
    try:
        from core import legal_brain_client as _lb
        health = _lb.health() or {}
        version = f"docs={health.get('documents', '?')}"
    except Exception:
        version = "na"
    _corpus_version["value"] = version
    _corpus_version["expires"] = now + max_age
    return version


def generation_key(task_type: str, query: str, corpus_ver: str = "na") -> tuple:
    return (task_type or "", normalize_query(query), str(corpus_ver))


def retrieval_key(query: str, limit: int) -> tuple:
    return (normalize_query(query), int(limit))


def cache_stats() -> dict:
    """Snapshot for the Command Center (does not trigger a network probe)."""
    return {
        "generation": GENERATION_CACHE.stats(),
        "retrieval": RETRIEVAL_CACHE.stats(),
        "corpus_version": _corpus_version.get("value") or "na",
    }


def clear_caches() -> dict:
    """Empty both caches. Returns counts for the API response."""
    gen = GENERATION_CACHE.clear()
    ret = RETRIEVAL_CACHE.clear()
    return {"cleared": gen + ret, "generation_cleared": gen,
            "retrieval_cleared": ret}
