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

# FAQ / repeat cache: answers persisted in the local ``juris_qa_log`` SQLite
# table (CT111 only, never sent externally). The in-process layer front-runs
# the DB so a repeat is a dict lookup; the DB layer survives restarts and is
# the durable "learning" store.
FAQ_MAXSIZE = 2048
FAQ_TTL = 7 * 24 * 3600.0  # 7 days

# An answer must be at least this long, and free of failure markers, before it
# is worth reusing as a canned answer.
ANSWER_MIN_CACHE_CHARS = 120
_ANSWER_ERROR_MARKERS = (
    "i couldn't generate",
    "couldn't generate a response",
    "query timed out",
    "timed out",
    "unable to",
    "try again later",
    "empty reply",
    "not authorized",
)

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)

# First-person markers mean the answer is about *this* user and must never be
# shared with another account. Deliberately excludes second-person ("you",
# "your") because generic questions ("can you explain X") are not personalised.
_PERSONAL_RE = re.compile(
    r"\b(i|i'm|im|me|my|mine|myself|we|we're|our|ours|us)\b", re.IGNORECASE)


def normalize_query(text: str) -> str:
    """Fold a query to a stable cache key.

    Lowercases, strips punctuation, and collapses whitespace so
    "What is  Contract Law?" and "what is contract law" share a key.
    """
    q = (text or "").strip().lower()
    q = _PUNCT_RE.sub(" ", q)
    return _WS_RE.sub(" ", q).strip()


def question_hash(text: str) -> str:
    """Stable hash of the *normalized* question, for repeat detection."""
    import hashlib
    return hashlib.sha1(normalize_query(text).encode("utf-8")).hexdigest()


def is_generic_question(text: str) -> bool:
    """True when a question carries no first-person/personal marker.

    Generic questions may reuse another account's good answer; personalised
    questions are always scoped to the asking account.
    """
    norm = normalize_query(text)
    if not norm:
        return False
    return _PERSONAL_RE.search(norm) is None


def answer_is_cacheable(answer: str) -> bool:
    """True when an answer is substantial enough to reuse as a canned answer."""
    text = (answer or "").strip()
    if len(text) < ANSWER_MIN_CACHE_CHARS:
        return False
    low = text.lower()
    return not any(marker in low for marker in _ANSWER_ERROR_MARKERS)


def answer_is_blocked(answer: str) -> bool:
    """True when an answer contains a failure/refusal marker."""
    low = (answer or "").strip().lower()
    return bool(low) and any(marker in low for marker in _ANSWER_ERROR_MARKERS)


def answer_confidence(answer: str) -> float:
    """Cheap 0..1 confidence heuristic used for weak-area mining.

    Not a model score — a deterministic proxy from length and failure markers:
    blocked/empty answers score 0, short answers score low, well-developed
    answers with citations score high.
    """
    text = (answer or "").strip()
    if not text:
        return 0.0
    low = text.lower()
    if any(marker in low for marker in _ANSWER_ERROR_MARKERS):
        return 0.0
    score = min(1.0, len(text) / 500.0)
    if len(text) < 80:
        score = min(score, 0.3)
    if any(tok in low for tok in (" act ", "section ", "article ", " v. ",
                                  "constitution", "held that")):
        score = min(1.0, score + 0.15)
    return round(score, 3)


def context_fingerprint(context: str) -> str:
    """Short digest of follow-up context (cache key component)."""
    if not context:
        return ""
    import hashlib
    return hashlib.sha1(context.encode("utf-8")).hexdigest()[:10]


def faq_key(task_type: str, query: str, scope: str) -> tuple:
    """Cache key for the FAQ layer.

    ``scope`` is ``"__generic__"`` for non-personalised questions (shareable)
    or the account id for anything personalised (never shared).
    """
    return ("faq", task_type or "", normalize_query(query), scope or "")


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
FAQ_CACHE = TTLCache(maxsize=FAQ_MAXSIZE, ttl=FAQ_TTL, name="faq")

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


def generation_key(task_type: str, query: str, corpus_ver: str = "na",
                   context_key: str = "") -> tuple:
    return (task_type or "", normalize_query(query), str(corpus_ver),
            context_key or "")


def retrieval_key(query: str, limit: int) -> tuple:
    return (normalize_query(query), int(limit))


def cache_stats() -> dict:
    """Snapshot for the Command Center (does not trigger a network probe)."""
    return {
        "generation": GENERATION_CACHE.stats(),
        "retrieval": RETRIEVAL_CACHE.stats(),
        "faq": FAQ_CACHE.stats(),
        "corpus_version": _corpus_version.get("value") or "na",
    }


def clear_caches() -> dict:
    """Empty all caches. Returns counts for the API response."""
    gen = GENERATION_CACHE.clear()
    ret = RETRIEVAL_CACHE.clear()
    faq = FAQ_CACHE.clear()
    return {"cleared": gen + ret + faq, "generation_cleared": gen,
            "retrieval_cleared": ret, "faq_cleared": faq}
