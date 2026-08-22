"""Kai Betting — Security (Phase 17).

Rate limiting, input validation, and security middleware.
Integrates with the Kai platform's existing security infrastructure.
"""

from __future__ import annotations

import re
import hashlib
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, Tuple, List
from functools import wraps

from fastapi import Request, HTTPException
from core.kai_betting.db import get_db


# ── Rate Limiter ─────────────────────────────────────────────────────────────

class RateLimiter:
    """Simple in-memory rate limiter with sliding window.

    Production would use Redis, but in-memory is fine for Kai's scale.
    """

    def __init__(self, max_requests: int = 100, window_seconds: int = 60):
        self._max_requests = max_requests
        self._window = window_seconds
        self._buckets: Dict[str, List[float]] = {}

    def is_rate_limited(self, key: str) -> bool:
        """Check if a key has exceeded its rate limit.

        Args:
            key: Rate limit key (typically IP address or user ID)

        Returns:
            True if rate limited, False if allowed
        """
        now = time.time()
        window_start = now - self._window

        # Clean old entries
        if key not in self._buckets:
            self._buckets[key] = []

        self._buckets[key] = [t for t in self._buckets[key] if t > window_start]

        # Check limit
        if len(self._buckets[key]) >= self._max_requests:
            return True

        # Record request
        self._buckets[key].append(now)
        return False

    def remaining(self, key: str) -> int:
        """Get remaining requests in this window."""
        now = time.time()
        window_start = now - self._window
        bucket = self._buckets.get(key, [])
        bucket = [t for t in bucket if t > window_start]
        return max(0, self._max_requests - len(bucket))

    def reset(self, key: str) -> None:
        """Reset rate limit for a key."""
        self._buckets.pop(key, None)


# Create singleton instances with different limits
_general_limiter = RateLimiter(max_requests=100, window_seconds=60)     # 100 req/min
_prediction_limiter = RateLimiter(max_requests=10, window_seconds=60)   # 10 gen/min
_auth_limiter = RateLimiter(max_requests=20, window_seconds=300)        # 20 login/5min


def get_client_key(request: Request) -> str:
    """Extract a rate-limiter key from request."""
    # Prefer user ID from auth, fall back to IP
    user_id = getattr(request.state, "user_id", None)
    if user_id:
        return f"user:{user_id}"

    # Get real IP through proxies
    # NOTE: trusts X-Forwarded-For unconditionally — spoofable by any direct client.
    # Acceptable for current LAN-only/homelab scale; needs a trusted-proxy allowlist
    # before this app is exposed to untrusted networks at scale.
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(
    max_requests: int = 100,
    window_seconds: int = 60,
    error_message: str = "Too many requests. Please wait.",
    limiter: Optional[RateLimiter] = None,
):
    """Decorator for FastAPI endpoints to apply rate limiting.

    Usage:
        @rate_limit(max_requests=10, window_seconds=60)
        @router.post("/predictions/generate")
        async def generate_prediction(...): ...
    """
    limiter = limiter or RateLimiter(max_requests=max_requests, window_seconds=window_seconds)

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Extract Request object from kwargs or args
            request = None
            for arg in args:
                if isinstance(arg, Request):
                    request = arg
                    break
            if not request:
                for v in kwargs.values():
                    if isinstance(v, Request):
                        request = v
                        break

            if request:
                key = get_client_key(request)
                if limiter.is_rate_limited(key):
                    retry_after = window_seconds
                    raise HTTPException(
                        status_code=429,
                        detail=error_message,
                        headers={"Retry-After": str(retry_after)},
                    )

            return await func(*args, **kwargs)

        return wrapper

    return decorator


# ── Input Validation ─────────────────────────────────────────────────────────

# Regex patterns for common fields
PATTERNS = {
    "email": re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"),
    "phone": re.compile(r"^\+?[\d\s\-()]{7,20}$"),
    "password": re.compile(r"^.{8,128}$"),  # At least 8 chars
    "sport_key": re.compile(r"^[a-z_]{2,30}$"),
    "market_type": re.compile(r"^[a-z_]{2,30}$"),
    "transaction_id": re.compile(r"^[A-Za-z0-9\-_]{6,64}$"),
    "plan_key": re.compile(r"^(daily|weekly|monthly|yearly)$"),
    "currency": re.compile(r"^[A-Z]{3}$"),
    "ip_address": re.compile(r"^[\d.:]+$"),
    "safe_text": re.compile(r"^[^<>&\"'\\]{0,500}$"),
}


def validate_email(email: str) -> Tuple[bool, str]:
    """Validate email format."""
    if not email or len(email) > 254:
        return False, "Email is required and must be under 254 characters"
    if not PATTERNS["email"].match(email):
        return False, "Invalid email format"
    return True, ""


def validate_password(password: str) -> Tuple[bool, str]:
    """Validate password strength."""
    if not password or not PATTERNS["password"].match(password):
        return False, "Password must be at least 8 characters"
    if len(password) > 128:
        return False, "Password must be under 128 characters"
    # Check complexity
    has_upper = any(c.isupper() for c in password)
    has_lower = any(c.islower() for c in password)
    has_digit = any(c.isdigit() for c in password)
    if not (has_upper or has_lower):
        return False, "Password must contain letters"
    if not has_digit:
        return False, "Password should contain at least one digit"
    return True, ""


def validate_amount(amount: float, min_val: float = 0.01, max_val: float = 10000.0) -> Tuple[bool, str]:
    """Validate payment amount."""
    if not isinstance(amount, (int, float)):
        return False, "Amount must be a number"
    if amount < min_val:
        return False, f"Amount must be at least {min_val}"
    if amount > max_val:
        return False, f"Amount must be at most {max_val}"
    return True, ""


def validate_sport_key(key: str) -> Tuple[bool, str]:
    """Validate sport key."""
    if not key or not PATTERNS["sport_key"].match(key):
        return False, "Invalid sport key format"
    return True, ""


def validate_probability(prob: float) -> Tuple[bool, str]:
    """Validate probability (0-1 range)."""
    if not isinstance(prob, (int, float)):
        return False, "Probability must be a number"
    if prob < 0 or prob > 1:
        return False, "Probability must be between 0 and 1"
    return True, ""


def sanitize_text(text: str, max_length: int = 500) -> str:
    """Sanitize user-provided text.

    Strips HTML/script tags and truncates.
    """
    if not text:
        return ""

    # Remove HTML tags
    sanitized = re.sub(r"<[^>]*>", "", text)
    # Remove script-like content
    sanitized = re.sub(r"(?i)(javascript|onclick|onload|onerror|onmouseover):", "", sanitized)
    # Strip null bytes and control characters
    sanitized = sanitized.replace("\x00", "").strip()
    # Truncate
    return sanitized[:max_length]


# ── Auth Helpers ─────────────────────────────────────────────────────────────

def hash_api_key(key: str) -> str:
    """Hash an API key for storage."""
    return hashlib.sha256(key.encode()).hexdigest()


def verify_api_key(key: str, hashed: str) -> bool:
    """Verify an API key against its hash."""
    return hashlib.sha256(key.encode()).hexdigest() == hashed


def generate_api_key() -> str:
    """Generate a new API key with kai_betting_ prefix."""
    import secrets
    return f"kai_betting_{secrets.token_urlsafe(32)}"


def audit_action(
    action: str,
    entity_type: str,
    entity_id: Optional[int] = None,
    user_id: Optional[int] = None,
    details: Optional[Dict[str, Any]] = None,
    ip_address: str = "",
):
    """Record an audit trail entry."""
    import json
    try:
        with get_db() as db:
            db.execute("""
                INSERT INTO audit_logs (action, entity_type, entity_id, user_id, details, ip_address)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                action,
                entity_type,
                entity_id,
                user_id,
                json.dumps(details or {}),
                ip_address or "127.0.0.1",
            ))
            db.commit()
    except Exception:
        pass  # Don't fail the main operation on audit failure


# ── Security Headers ─────────────────────────────────────────────────────────

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "1; mode=block",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline';",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def add_security_headers(response_headers: Dict[str, str]) -> None:
    """Add security headers to a response."""
    for key, value in SECURITY_HEADERS.items():
        if key not in response_headers:
            response_headers[key] = value
