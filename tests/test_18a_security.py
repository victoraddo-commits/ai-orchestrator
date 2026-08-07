"""Tests for Phase 18A-a: JWT Auth, Rate Limiting, Brute-Force Protection."""

import os
import sys
import time
import json
import tempfile
import threading
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestJwtAuth:
    """JWT token creation, verification, and expiry tests."""

    def test_create_and_verify_jwt(self):
        """Round-trip: create a JWT, verify it, get claims back."""
        from core.jwt_auth import create_jwt, verify_jwt

        token = create_jwt({"sub": "testuser", "role": "operator"})
        claims = verify_jwt(token)

        assert claims is not None
        assert claims["sub"] == "testuser"
        assert claims["role"] == "operator"
        assert "iat" in claims
        assert "exp" in claims
        assert claims["exp"] > claims["iat"]

    def test_jwt_invalid_signature_rejected(self):
        """Tampered JWT is rejected."""
        from core.jwt_auth import create_jwt, verify_jwt

        token = create_jwt({"sub": "testuser", "role": "viewer"})
        # Tamper with the payload
        parts = token.split(".")
        tampered = parts[0] + "." + parts[1] + ".invalidsig"

        claims = verify_jwt(tampered)
        assert claims is None

    def test_jwt_different_users_have_different_tokens(self):
        """Tokens for different users are distinct."""
        from core.jwt_auth import create_jwt

        t1 = create_jwt({"sub": "user1", "role": "viewer"})
        t2 = create_jwt({"sub": "user2", "role": "operator"})

        assert t1 != t2
        # Payloads should differ
        from core.jwt_auth import decode_jwt_claims
        c1 = decode_jwt_claims(t1)
        c2 = decode_jwt_claims(t2)
        assert c1["sub"] == "user1"
        assert c2["sub"] == "user2"

    def test_jwt_expiry(self):
        """Expired JWT is rejected."""
        from core.jwt_auth import create_jwt, verify_jwt

        # Create a token that expires immediately (negative expiry forces past)
        token = create_jwt({"sub": "testuser", "role": "viewer"}, expiry_seconds=-1)

        claims = verify_jwt(token)
        assert claims is None

    def test_jwt_refresh(self):
        """Valid token can be refreshed."""
        from core.jwt_auth import create_jwt, verify_jwt, refresh_jwt

        token = create_jwt({"sub": "testuser", "role": "viewer"})
        refreshed = refresh_jwt(token)

        assert refreshed is not None
        assert refreshed != token

        new_claims = verify_jwt(refreshed)
        assert new_claims is not None
        assert new_claims["sub"] == "testuser"
        assert new_claims["role"] == "viewer"

    def test_expired_jwt_cannot_be_refreshed(self):
        """Expired token refresh returns None."""
        from core.jwt_auth import create_jwt, refresh_jwt

        token = create_jwt({"sub": "testuser"}, expiry_seconds=-1)
        refreshed = refresh_jwt(token)

        assert refreshed is None

    def test_jwt_has_jti_uniqueness(self):
        """Each JWT has a unique JWT ID."""
        from core.jwt_auth import create_jwt, verify_jwt

        t1 = create_jwt({"sub": "user"})
        t2 = create_jwt({"sub": "user"})

        c1 = verify_jwt(t1)
        c2 = verify_jwt(t2)

        assert c1["jti"] != c2["jti"]


class TestRateLimiter:
    """Token-bucket rate limiter tests."""

    def test_bucket_allows_up_to_burst(self):
        """Token bucket allows requests up to burst size."""
        from core.rate_limiter import TokenBucket

        bucket = TokenBucket(rate=100, burst=5)
        for _ in range(5):
            assert bucket.consume() is True
        # 6th should fail
        assert bucket.consume() is False

    def test_bucket_refills_over_time(self):
        """Tokens refill at the configured rate."""
        from core.rate_limiter import TokenBucket

        bucket = TokenBucket(rate=1000, burst=3)
        for _ in range(3):
            assert bucket.consume()  # exhaust
        assert bucket.consume() is False  # empty

        time.sleep(0.01)  # Wait for refill (rate=1000/sec → ~10 tokens)
        assert bucket.consume() is True  # refilled

    def test_rate_limiter_global_default(self):
        """Global rate limit applies by default."""
        from core.rate_limiter import get_rate_limiter

        rl = get_rate_limiter()
        key = f"test-global-{time.time()}"
        allowed, _ = rl.check(key, "global")
        assert allowed is True

    def test_rate_limiter_strict(self):
        """Strict rate limit only allows 2 requests per window."""
        from core.rate_limiter import get_rate_limiter

        rl = get_rate_limiter()
        key = f"test-strict-{time.time()}"
        assert rl.check(key, "strict")[0] is True
        assert rl.check(key, "strict")[0] is True
        assert rl.check(key, "strict")[0] is False  # 3rd blocked

    def test_check_multi_violates_first_limit(self):
        """check_multi returns the first violated limit name."""
        from core.rate_limiter import get_rate_limiter

        rl = get_rate_limiter()
        key = f"test-multi-{time.time()}"

        # Consume strict limit first
        rl.check(key, "strict")
        rl.check(key, "strict")

        allowed, retry, name = rl.check_multi(key, ["strict", "global"])
        assert allowed is False
        assert name == "strict"


class TestBruteForce:
    """Brute-force login protection tests."""

    def test_failure_tracking(self):
        """Failed attempts are tracked per (IP, username)."""
        from core.rate_limiter import get_brute_force_protector

        bf = get_brute_force_protector()
        key_ip = f"192.168.1.{int(time.time() * 1000) % 255}"

        for _ in range(5):
            blocked, _ = bf.record_failure(key_ip, "testuser")
            assert blocked is False  # Under the max

        count = bf.get_failure_count(key_ip, "testuser")
        assert count == 5

    def test_block_after_max_attempts(self):
        """After max_attempts failures, login is blocked."""
        from core.rate_limiter import get_brute_force_protector, BRUTE_FORCE_CONFIG

        bf = get_brute_force_protector()
        key_ip = f"10.0.0.{int(time.time() * 1000) % 255}"

        max_attempts = BRUTE_FORCE_CONFIG["max_attempts"]
        for i in range(max_attempts):
            blocked, _ = bf.record_failure(key_ip, "victim")
            if i < max_attempts - 1:
                assert blocked is False

        # The last one should trigger the block
        blocked, retry_after = bf.record_failure(key_ip, "victim")
        assert blocked is True
        assert retry_after > 0

    def test_success_clears_failures(self):
        """Successful login clears failure tracking."""
        from core.rate_limiter import get_brute_force_protector

        bf = get_brute_force_protector()
        key_ip = f"172.16.0.{int(time.time() * 1000) % 255}"

        for _ in range(3):
            bf.record_failure(key_ip, "testuser")

        bf.record_success(key_ip, "testuser")
        count = bf.get_failure_count(key_ip, "testuser")
        assert count == 0

    def test_different_users_tracked_separately(self):
        """Each (IP, username) pair is tracked independently."""
        from core.rate_limiter import get_brute_force_protector

        bf = get_brute_force_protector()
        ip = f"10.10.10.{int(time.time() * 1000) % 255}"

        bf.record_failure(ip, "user1")
        bf.record_failure(ip, "user1")
        bf.record_failure(ip, "user2")

        assert bf.get_failure_count(ip, "user1") == 2
        assert bf.get_failure_count(ip, "user2") == 1


class TestAuthzIntegration:
    """Authz module integration with JWT + brute-force."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Reset state before each test."""
        import core.authz as authz
        authz._sessions.clear()
        # Don't clear accounts file, just the sessions

    def test_authenticate_jwt_token(self):
        """authenticate() returns a valid JWT token."""
        import core.authz as authz

        # Create a test account
        accounts = authz._read_accounts()
        accounts["testuser"] = {
            "password_hash": authz._hash_password("testpass"),
            "role": "viewer",
            "created": "2026-01-01T00:00:00+00:00",
        }
        authz._write_accounts(accounts)

        token = authz.authenticate("testuser", "testpass", "127.0.0.1")
        assert token is not None

        # Verify it's a JWT
        from core.jwt_auth import verify_jwt
        claims = verify_jwt(token)
        assert claims is not None
        assert claims["sub"] == "testuser"
        assert claims["role"] == "viewer"

    def test_authenticate_wrong_password(self):
        """Wrong password returns None."""
        import core.authz as authz

        accounts = authz._read_accounts()
        accounts["testuser"] = {
            "password_hash": authz._hash_password("correct"),
            "role": "viewer",
            "created": "2026-01-01T00:00:00+00:00",
        }
        authz._write_accounts(accounts)

        token = authz.authenticate("testuser", "wrong", "127.0.0.1")
        assert token is None

    def test_authenticate_nonexistent_user(self):
        """Nonexistent user returns None."""
        import core.authz as authz

        token = authz.authenticate("nobody", "pass", "127.0.0.1")
        assert token is None

    def test_session_resolution_jwt(self):
        """_resolve_session handles JWT tokens."""
        import core.authz as authz
        from core.jwt_auth import create_jwt

        token = create_jwt({"sub": "operator1", "role": "operator"})
        session = authz._resolve_session(token)

        assert session is not None
        assert session["username"] == "operator1"
        assert session["role"] == "operator"

    def test_session_resolution_expired_jwt(self):
        """_resolve_session rejects expired JWTs."""
        import core.authz as authz
        from core.jwt_auth import create_jwt

        token = create_jwt({"sub": "user", "role": "viewer"}, expiry_seconds=-1)

        session = authz._resolve_session(token)
        assert session is None

    def test_invalidate_session(self):
        """invalidate_session removes the session."""
        import core.authz as authz
        from core.jwt_auth import create_jwt

        token = create_jwt({"sub": "user", "role": "viewer"})
        authz._sessions[token] = {"username": "user", "role": "viewer", "created": "2026-01-01"}

        authz.invalidate_session(token)
        assert token not in authz._sessions
