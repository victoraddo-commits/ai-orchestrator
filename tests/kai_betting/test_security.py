"""Tests for Kai Betting security module."""

import pytest
import time
from core.kai_betting.security import (
    RateLimiter, validate_email, validate_password, validate_amount,
    validate_sport_key, sanitize_text, hash_api_key, verify_api_key,
    generate_api_key, PATTERNS, SECURITY_HEADERS,
)


class TestRateLimiter:
    """Rate limiter behavior."""

    def test_allows_requests_within_limit(self):
        limiter = RateLimiter(max_requests=5, window_seconds=60)
        key = "test-ip"
        for _ in range(5):
            assert not limiter.is_rate_limited(key)

    def test_blocks_requests_over_limit(self):
        limiter = RateLimiter(max_requests=3, window_seconds=60)
        key = "test-ip"
        for _ in range(3):
            assert not limiter.is_rate_limited(key)
        # 4th request should be rate limited
        assert limiter.is_rate_limited(key)

    def test_remaining_count(self):
        limiter = RateLimiter(max_requests=10, window_seconds=60)
        key = "user-1"
        assert limiter.remaining(key) == 10
        limiter.is_rate_limited(key)
        assert limiter.remaining(key) == 9

    def test_reset(self):
        limiter = RateLimiter(max_requests=3, window_seconds=60)
        key = "test-key"
        for _ in range(3):
            limiter.is_rate_limited(key)
        assert limiter.is_rate_limited(key)
        limiter.reset(key)
        assert not limiter.is_rate_limited(key)

    def test_separate_keys(self):
        limiter = RateLimiter(max_requests=2, window_seconds=60)
        for _ in range(2):
            limiter.is_rate_limited("key-a")
        assert limiter.is_rate_limited("key-a")
        assert not limiter.is_rate_limited("key-b")

    def test_window_expiry(self):
        """Old entries should expire from the window."""
        limiter = RateLimiter(max_requests=3, window_seconds=0)
        # With window_seconds=0, every request creates a new window
        for _ in range(5):
            assert not limiter.is_rate_limited("key")


class TestEmailValidation:
    """Email validation."""

    def test_valid_emails(self):
        for email in ["user@example.com", "a@b.co", "test.user+tag@domain.com"]:
            ok, msg = validate_email(email)
            assert ok, f"Expected valid: {email} — {msg}"

    def test_invalid_emails(self):
        for email in ["", "notanemail", "@domain.com", "user@", "user@.com",
                      "user@domain", None]:
            ok, msg = validate_email(email)
            assert not ok, f"Expected invalid: {email}"


class TestPasswordValidation:
    """Password validation."""

    def test_valid_passwords(self):
        for pw in ["Password123", "secure1Password", "a1b2c3d4e5f6g7h8"]:
            ok, msg = validate_password(pw)
            assert ok, f"Expected valid: ... — {msg}"

    def test_invalid_passwords(self):
        for pw in ["", "short", "1234567", "ab", None]:
            ok, msg = validate_password(pw)
            assert not ok, f"Expected invalid: {pw}"

    def test_no_digit_warns(self):
        """Password without digit should fail validation."""
        ok, msg = validate_password("abcdefgh")
        assert not ok


class TestAmountValidation:
    """Amount validation."""

    def test_valid_amounts(self):
        for amt in [0.01, 5.0, 100.0, 5000.0]:
            ok, msg = validate_amount(amt)
            assert ok, f"Expected valid: {amt}"

    def test_invalid_amounts(self):
        for amt in [0, -1, -100, float("inf")]:
            ok, msg = validate_amount(amt)
            assert not ok, f"Expected invalid: {amt}"

    def test_amount_too_high(self):
        ok, msg = validate_amount(20000.0, max_val=10000.0)
        assert not ok


class TestSportKeyValidation:
    """Sport key validation."""

    def test_valid_keys(self):
        for key in ["football", "ice_hockey", "american_football"]:
            ok, msg = validate_sport_key(key)
            assert ok

    def test_invalid_keys(self):
        for key in ["", "football<script>", "a", None, "with spaces", "UPPERCASE"]:
            ok, msg = validate_sport_key(key)
            assert not ok, f"Expected invalid: {key}"


class TestSanitization:
    """Text sanitization."""

    def test_html_removed(self):
        assert sanitize_text("<script>alert('xss')</script>Hello") == "alert('xss')Hello"

    def test_javascript_handler_removed(self):
        sanitized = sanitize_text("javascript:alert(1)")
        assert "javascript" not in sanitized.lower()

    def test_null_bytes_removed(self):
        assert sanitize_text("hello\x00world") == "helloworld"

    def test_truncation(self):
        long_text = "a" * 1000
        result = sanitize_text(long_text, max_length=50)
        assert len(result) == 50

    def test_empty_input(self):
        assert sanitize_text("") == ""
        assert sanitize_text(None) == ""


class TestAPIKeys:
    """API key hashing and verification."""

    def test_hash_and_verify(self):
        key = generate_api_key()
        hashed = hash_api_key(key)
        assert verify_api_key(key, hashed)
        assert not verify_api_key("wrong_key", hashed)

    def test_key_format(self):
        key = generate_api_key()
        assert key.startswith("kai_betting_")
        assert len(key) > 30

    def test_deterministic_hash(self):
        key = "test-key-1"
        h1 = hash_api_key(key)
        h2 = hash_api_key(key)
        assert h1 == h2


class TestSecurityHeaders:
    """Security headers."""

    def test_all_headers_present(self):
        assert "X-Content-Type-Options" in SECURITY_HEADERS
        assert "X-Frame-Options" in SECURITY_HEADERS
        assert "X-XSS-Protection" in SECURITY_HEADERS
        assert "Content-Security-Policy" in SECURITY_HEADERS
        assert "Strict-Transport-Security" in SECURITY_HEADERS


class TestPatterns:
    """Regex pattern validation."""

    def test_email_pattern(self):
        assert PATTERNS["email"].match("user@example.com")
        assert not PATTERNS["email"].match("notanemail")

    def test_phone_pattern(self):
        assert PATTERNS["phone"].match("+233241234567")
        assert PATTERNS["phone"].match("0241234567")

    def test_sport_key_pattern(self):
        assert PATTERNS["sport_key"].match("ice_hockey")
        assert not PATTERNS["sport_key"].match("UPPERCASE")

    def test_plan_key_pattern(self):
        assert PATTERNS["plan_key"].match("daily")
        assert PATTERNS["plan_key"].match("monthly")
        assert not PATTERNS["plan_key"].match("premium_best")

    def test_currency_pattern(self):
        assert PATTERNS["currency"].match("GHS")
        assert PATTERNS["currency"].match("USD")
        assert not PATTERNS["currency"].match("ghs")
