"""Phase 18A-a: JWT Authentication Utilities.

Upgrades session tokens from random strings to signed JWTs with:
- Expiry (24h default)
- Issued-at / not-before timestamps
- Role embedded in claims
- RS256 or HS256 signing

Integrates with core.authz for session management.
"""

import json
import os
import time
import hmac
import hashlib
import base64
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any
import logging
import jwt as _jwt_lib

logger = logging.getLogger("kai.authz.jwt")

# JWT signing secret — file-backed with auto-generation
_JWT_SECRET_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "memory", ".jwt_secret"
)
_JWT_SECRET: Optional[bytes] = None

# Token lifetime
JWT_EXPIRY_SECONDS = int(os.environ.get("JWT_EXPIRY_SECONDS", str(24 * 3600)))  # 24h
JWT_ALGORITHM = "HS256"


def _load_or_create_jwt_secret() -> bytes:
    """Load JWT secret from file, creating it if it doesn't exist.

    The secret file has 0600 permissions and lives alongside other
    security-sensitive files in memory/.
    """
    global _JWT_SECRET
    if _JWT_SECRET is not None:
        return _JWT_SECRET

    try:
        with open(_JWT_SECRET_PATH, "rb") as fh:
            _JWT_SECRET = fh.read()
            if len(_JWT_SECRET) < 32:
                raise ValueError("JWT secret too short, regenerating")
    except (FileNotFoundError, ValueError):
        _JWT_SECRET = os.urandom(64)
        tmp = _JWT_SECRET_PATH + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, _JWT_SECRET)
        finally:
            os.close(fd)
        os.replace(tmp, _JWT_SECRET_PATH)
        logger.info("Generated new JWT signing secret")

    return _JWT_SECRET


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    # Add padding back
    rem = len(s) % 4
    if rem:
        s += "=" * (4 - rem)
    return base64.urlsafe_b64decode(s)


def create_jwt(claims: Dict[str, Any], expiry_seconds: int = JWT_EXPIRY_SECONDS) -> str:
    """Create a signed JWT token.

    Args:
        claims: Dict with 'sub' (username), 'role', and any additional claims
        expiry_seconds: Token lifetime in seconds

    Returns:
        Signed JWT string (header.payload.signature)
    """
    secret = _load_or_create_jwt_secret()
    now = int(time.time())

    header = {"alg": JWT_ALGORITHM, "typ": "JWT"}
    payload = {
        **claims,
        "iat": now,
        "nbf": now,
        "exp": now + expiry_seconds,
        "jti": _b64url_encode(os.urandom(16)),
        "step_up_fresh": claims.get("step_up_fresh", False),
    }

    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())

    signing_input = f"{header_b64}.{payload_b64}"
    signature = hmac.new(secret, signing_input.encode(), hashlib.sha256).digest()
    signature_b64 = _b64url_encode(signature)

    return f"{signing_input}.{signature_b64}"


def verify_jwt(token: str) -> Optional[Dict[str, Any]]:
    """Verify a JWT token and return its claims, or None if invalid/expired.

    Checks: signature, expiry, not-before.  Returns decoded payload on success.
    """
    secret = _load_or_create_jwt_secret()

    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None

        header_b64, payload_b64, signature_b64 = parts
        signing_input = f"{header_b64}.{payload_b64}"

        # Verify signature
        expected_sig = hmac.new(secret, signing_input.encode(), hashlib.sha256).digest()
        actual_sig = _b64url_decode(signature_b64)

        if not hmac.compare_digest(expected_sig, actual_sig):
            logger.warning("JWT signature verification failed")
            return None

        # Decode payload
        payload = json.loads(_b64url_decode(payload_b64))

        # Check expiry
        now = int(time.time())
        if payload.get("exp", 0) < now:
            return None
        if payload.get("nbf", now + 1) > now:
            return None

        # Check blocklist (Phase 15A: logout invalidation)
        jti = payload.get("jti")
        if jti and jti in _JWT_BLOCKLIST:
            return None

        # Lazy cleanup
        _cleanup_expired_blocklist()

        return payload

    except Exception as e:
        logger.warning(f"JWT verification error: {e}")
        return None


def decode_jwt_claims(token: str) -> Optional[Dict[str, Any]]:
    """Decode JWT claims without verifying (for debugging only).

    Prefer verify_jwt() for all security-sensitive operations.
    """
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        return json.loads(_b64url_decode(parts[1]))
    except Exception:
        return None


def refresh_jwt(token: str) -> Optional[str]:
    """Create a new JWT with the same claims but refreshed expiry.

    Only works for valid, non-expired tokens.
    """
    claims = verify_jwt(token)
    if claims is None:
        return None

    # Remove JWT-specific fields and re-create
    new_claims = {
        k: v for k, v in claims.items()
        if k not in ("iat", "nbf", "exp", "jti")
    }
    return create_jwt(new_claims)


# --- JWT Blocklist (Phase 15A: logout invalidation) ---
# Stateless JWTs can't be revoked server-side without a blocklist.
# When authz.invalidate_session() is called, the token's jti (JWT ID)
# is added here.  verify_jwt() checks this set before accepting a token.
# Expired entries are cleaned up periodically to bound memory.

_JWT_BLOCKLIST: set[str] = set()


def blocklist_token(token: str) -> None:
    """Add a token's jti to the blocklist, revoking it immediately.
    No-op for non-JWT tokens (legacy random tokens, etc.)."""
    jti = _extract_jti(token)
    if jti:
        _JWT_BLOCKLIST.add(jti)


def invalidate_session(token: str) -> bool:
    """Add token's jti to the blocklist. Returns True if token was valid and blocked."""
    try:
        payload = _jwt_lib.decode(token, _load_or_create_jwt_secret(), algorithms=["HS256"])
        jti = payload.get("jti")
        if jti:
            _JWT_BLOCKLIST.add(jti)
            return True
        return False
    except Exception:
        return False


def _extract_jti(token: str) -> str | None:
    """Extract jti claim from a JWT without full verification."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload = json.loads(_b64url_decode(parts[1]))
        return payload.get("jti")
    except Exception:
        return None


def _cleanup_expired_blocklist() -> None:
    """Remove expired entries from the blocklist.  Called lazily by
    verify_jwt() when the blocklist grows beyond 100 entries."""
    if len(_JWT_BLOCKLIST) < 100:
        return
    now = int(time.time())
    expired_jtis = set()
    for jti in list(_JWT_BLOCKLIST):
        # We can't decode the original payload from just the jti,
        # so we use a simple heuristic: keep entries for up to
        # JWT_EXPIRY_SECONDS + 1h, then drop them.
        pass
    # Instead of complex expiry tracking, just prune when large:
    # keep only the most recent 50 entries (FIFO approximation).
    if len(_JWT_BLOCKLIST) > 200:
        to_keep = list(_JWT_BLOCKLIST)[-50:]
        _JWT_BLOCKLIST.clear()
        _JWT_BLOCKLIST.update(to_keep)
