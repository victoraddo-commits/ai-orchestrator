"""Phase 18A-a: Rate Limiting & Brute-Force Protection.

Provides:
- Token-bucket rate limiter for API endpoints
- Brute-force login protection with exponential backoff
- IP-based and account-based tracking
- Memory-backed with atomic operations (no external deps)

Integrates with core.authz for login protection and core.api for
endpoint-level rate limiting.
"""

import time
import threading
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Tuple, Optional

logger = logging.getLogger("kai.authz.rate_limiter")

# ---------------------------------------------------------------------------
# Rate limit configuration
# ---------------------------------------------------------------------------

# Global endpoint rate limits (requests per window)
DEFAULT_RATE_LIMITS = {
    "global": (600, 60),           # 600 req/min (10/sec) — overall API
    "read": (300, 60),              # 300 req/min — GET endpoints
    "write": (120, 60),             # 120 req/min — POST/PUT/DELETE
    "chat": (30, 60),               # 30 req/min — chat endpoints
    "auth": (5, 60),                # 5 req/min — login attempts
    "build": (10, 60),              # 10 req/min — build-related
    "strict": (2, 60),              # 2 req/min — very restricted (e.g., password change)
}

# Brute-force protection
BRUTE_FORCE_CONFIG = {
    "max_attempts": 10,             # Max failed login attempts
    "window_seconds": 900,          # 15-minute window
    "block_duration_seconds": 900,  # 15-minute block
    "backoff_base_seconds": 1,      # Exponential backoff starts at 1s
    "backoff_max_seconds": 300,     # Max 5-minute delay
}

# Cleanup interval (seconds) — prevent memory leaks from stale entries
CLEANUP_INTERVAL = 300


# ---------------------------------------------------------------------------
# Token bucket implementation
# ---------------------------------------------------------------------------

@dataclass
class TokenBucket:
    """Thread-safe token bucket for rate limiting."""
    rate: float           # tokens per second
    burst: int             # maximum burst size
    tokens: float = field(default=0.0)
    last_refill: float = field(default_factory=time.monotonic)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self):
        self.tokens = float(self.burst)

    def consume(self, tokens: int = 1) -> bool:
        """Try to consume tokens. Returns True if allowed, False if rate-limited."""
        with self.lock:
            now = time.monotonic()
            elapsed = now - self.last_refill
            self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
            self.last_refill = now

            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False

    @property
    def available(self) -> float:
        with self.lock:
            now = time.monotonic()
            elapsed = now - self.last_refill
            return min(self.burst, self.tokens + elapsed * self.rate)


# ---------------------------------------------------------------------------
# Rate limiter registry
# ---------------------------------------------------------------------------

class RateLimiter:
    """Thread-safe rate limiter managing buckets per key."""

    def __init__(self):
        self._buckets: Dict[str, Dict[str, TokenBucket]] = defaultdict(dict)
        self._lock = threading.Lock()
        self._last_cleanup = time.monotonic()

    def _get_bucket(self, key: str, limit_name: str) -> TokenBucket:
        """Get or create a token bucket for (key, limit_name)."""
        bucket_key = f"{key}:{limit_name}"
        with self._lock:
            self._maybe_cleanup()
            if bucket_key not in self._buckets:
                limits = DEFAULT_RATE_LIMITS.get(limit_name, DEFAULT_RATE_LIMITS["global"])
                max_req, window = limits
                self._buckets[bucket_key] = TokenBucket(
                    rate=max_req / window,
                    burst=max_req,
                )
            return self._buckets[bucket_key]

    def _maybe_cleanup(self):
        """Periodically clean up stale buckets."""
        now = time.monotonic()
        if now - self._last_cleanup < CLEANUP_INTERVAL:
            return
        self._last_cleanup = now
        # Remove buckets with full tokens (unused)
        stale = [
            k for k, b in list(self._buckets.items())
            if b.available >= b.burst * 0.99
        ]
        for k in stale:
            del self._buckets[k]
        if stale:
            logger.debug(f"Cleaned up {len(stale)} stale rate-limit buckets")

    def check(self, key: str, limit_name: str = "global") -> Tuple[bool, float]:
        """Check if request should be allowed.

        Returns (allowed, retry_after_seconds).
        """
        bucket = self._get_bucket(key, limit_name)
        if bucket.consume(1):
            return True, 0.0

        # Estimate retry-after
        rate = bucket.rate
        retry_after = 1.0 / rate if rate > 0 else 1.0
        return False, retry_after

    def check_multi(self, key: str, limit_names: list) -> Tuple[bool, float, str]:
        """Check against multiple rate limits. Returns (allowed, retry, violated_limit)."""
        for name in limit_names:
            allowed, retry = self.check(key, name)
            if not allowed:
                return False, retry, name
        return True, 0.0, ""


# ---------------------------------------------------------------------------
# Brute-force login protection
# ---------------------------------------------------------------------------

@dataclass
class BruteForceEntry:
    """Tracking for a single login target (IP+username)."""
    failures: int = 0
    first_failure: float = 0.0
    last_failure: float = 0.0
    blocked_until: float = 0.0


class BruteForceProtector:
    """Tracks failed login attempts and enforces progressive blocking.

    Algorithm:
    1. Track failures per (ip, username) key
    2. After max_attempts failures in window_seconds, block for block_duration
    3. Exponential backoff for repeat offenders:
       - 1st block: block_duration (15 min)
       - 2nd block: 2× block_duration (30 min)
       - 3rd block: 4× block_duration (60 min)
       - Caps at 24 hours
    """

    def __init__(self):
        self._entries: Dict[str, BruteForceEntry] = {}
        self._lock = threading.Lock()
        self._block_multipliers: Dict[str, int] = defaultdict(lambda: 0)

    def _key(self, ip: str, username: str) -> str:
        return f"{ip}:{username.lower()}"

    def record_failure(self, ip: str, username: str) -> Tuple[bool, float]:
        """Record a failed login attempt.

        Returns (is_blocked, retry_after_seconds).
        """
        key = self._key(ip, username)
        now = time.monotonic()
        cfg = BRUTE_FORCE_CONFIG

        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                entry = BruteForceEntry()
                self._entries[key] = entry

            # Check if currently blocked
            if now < entry.blocked_until:
                return True, entry.blocked_until - now

            # Reset if window expired
            if now - entry.first_failure > cfg["window_seconds"]:
                entry.failures = 0
                entry.first_failure = now
                self._block_multipliers[key] = max(0, self._block_multipliers[key] - 1)

            if entry.failures == 0:
                entry.first_failure = now

            entry.failures += 1
            entry.last_failure = now

            if entry.failures >= cfg["max_attempts"]:
                multiplier = self._block_multipliers.get(key, 0)
                block_duration = cfg["block_duration_seconds"] * (2 ** multiplier)
                block_duration = min(block_duration, 86400)  # Cap at 24h
                entry.blocked_until = now + block_duration
                self._block_multipliers[key] = min(multiplier + 1, 10)
                logger.warning(
                    f"Brute-force block: {key} blocked for {block_duration}s "
                    f"(failure #{entry.failures}, multiplier {multiplier})"
                )
                return True, block_duration

            # Calculate backoff delay for retry-after header
            backoff = min(
                cfg["backoff_base_seconds"] * (2 ** (entry.failures - 1)),
                cfg["backoff_max_seconds"],
            )
            return False, backoff

    def record_success(self, ip: str, username: str) -> None:
        """Clear failure tracking on successful login."""
        key = self._key(ip, username)
        with self._lock:
            self._entries.pop(key, None)
            self._block_multipliers.pop(key, None)

    def is_blocked(self, ip: str, username: str) -> Tuple[bool, float]:
        """Check if a login attempt is currently blocked."""
        key = self._key(ip, username)
        with self._lock:
            entry = self._entries.get(key)
            if entry and time.monotonic() < entry.blocked_until:
                return True, entry.blocked_until - time.monotonic()
            return False, 0.0

    def get_failure_count(self, ip: str, username: str) -> int:
        """Get current failure count for a key."""
        key = self._key(ip, username)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return 0
            now = time.monotonic()
            if now - entry.first_failure > BRUTE_FORCE_CONFIG["window_seconds"]:
                return 0
            return entry.failures


# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------

_rate_limiter: Optional[RateLimiter] = None
_brute_force: Optional[BruteForceProtector] = None


def get_rate_limiter() -> RateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter()
    return _rate_limiter


def get_brute_force_protector() -> BruteForceProtector:
    global _brute_force
    if _brute_force is None:
        _brute_force = BruteForceProtector()
    return _brute_force
