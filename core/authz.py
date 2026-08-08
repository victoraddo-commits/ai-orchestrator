"""Phase 15A + 18A-a: Capability-based authorization with JWT, rate limiting.

Every write (mutating) endpoint in core/api.py calls check_capability() before
acting.  The existing bridge token ("bridge-token:*") operator retains full
operator capabilities unchanged — this phase adds JWT-based sessions and
brute-force login protection.

Deliberately NOT in scope: OAuth, MFA, service accounts, more than 2 roles,
multi-org, or per-user scoping.  See roadmap phases 15A/18A-a.
"""

import json
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

import bcrypt

from core.jwt_auth import create_jwt, verify_jwt
from core.rate_limiter import get_brute_force_protector

# ---------------------------------------------------------------------------
# Security-critical paths for self-modifying builds
# ---------------------------------------------------------------------------

# List of files that when modified by a build, require special security handling
# These are the core authentication/authorization and credential handling files
# that could potentially be abused to escalate privileges
SECURITY_CRITICAL_PATHS = {
    "core/authz.py",
    "core/security.py",
    "memory/accounts.json",
    "core/llm_clients.py",
    "core/api.py",
}

# ---------------------------------------------------------------------------
# Capability definitions
# ---------------------------------------------------------------------------

# Every named action a role can be granted.  Read-only endpoints (GET) don't
# call check_capability at all — they're unrestricted by design.
CAPABILITIES = {
    "approvals.approve": "Approve pending actions",
    "approvals.reject": "Reject pending actions",
    "builds.create": "Create new builds",
    "builds.approve_architecture": "Approve architecture plans",
    "builds.approve_deploy": "Approve deployments",
    "builds.answer": "Answer build clarification questions",
    "builds.generate": "Trigger code generation",
    "builds.rollback": "Rollback a deployed build",
    "roadmap.create": "Create roadmap phases",
    "roadmap.modify": "Modify roadmap phase status",
    "roadmap.autonomy": "Enable/disable autonomous roadmap mode",
    "autonomy.configure": "Change autonomy level",
    "delegate.use": "Use the AI delegation endpoint",
    "kai.command": "Issue Kai commands",
    "kai.chat.send": "Send chat messages to Kai",
    "law.manage": "Manage law documents (add/delete)",
    "dashboard.password": "Change dashboard login password",
    "juris.admin": "Manage Juris Kai accounts (subscription, deactivate, grant days, referrals)",
}

# Exactly two roles — operator (everything) and viewer (read-only GETs).
# Adding a narrower role later means changing the mapping below, not redesigning
# the check — there's no lock-in cost to starting minimal.
ROLE_CAPABILITIES = {
    "operator": set(CAPABILITIES.keys()),
    "viewer": set(),  # read-only — no write capabilities
}

# ---------------------------------------------------------------------------
# Account storage
# ---------------------------------------------------------------------------

ACCOUNTS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "memory", "accounts.json")

# Session tokens live in-process only (not persisted) — a restart invalidates
# every viewer session, which is acceptable for a single-operator box.
_sessions: dict[str, dict] = {}  # token → {"username": str, "role": str, "created": str}


def _read_accounts() -> dict:
    try:
        with open(ACCOUNTS_FILE) as fh:
            data = json.load(fh)
        return data.get("records", {}) if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_accounts(accounts: dict) -> None:
    payload = {"schema_version": 1, "records": accounts}
    tmp = ACCOUNTS_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(payload, fh, indent=2)
    os.replace(tmp, ACCOUNTS_FILE)


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_account(username: str, password: str, role: str = "viewer") -> dict:
    """Create a local account.  Only 'viewer' is valid for new accounts —
    'operator' is reserved for the bridge-token path.  The caller must have
    the operator capability to invoke this."""
    if role not in ("viewer",):
        raise ValueError(f"Role '{role}' cannot be used for new accounts")
    accounts = _read_accounts()
    if username in accounts:
        raise ValueError(f"Account '{username}' already exists")
    accounts[username] = {
        "password_hash": _hash_password(password),
        "role": role,
        "created": datetime.now(timezone.utc).isoformat(),
    }
    _write_accounts(accounts)
    return {"username": username, "role": role}


def authenticate(username: str, password: str, client_ip: str = "127.0.0.1") -> str | None:
    """Validate credentials and return a JWT session token, or None.

    Integrates brute-force protection: after 10 failed attempts in 15 minutes,
    the (IP, username) pair is blocked for 15 minutes with exponential backoff.

    Returns JWT token string on success, None on failure.
    """
    bf = get_brute_force_protector()

    # Check if currently blocked
    is_blocked, retry_after = bf.is_blocked(client_ip, username)
    if is_blocked:
        return None  # Blocked — don't even check password

    accounts = _read_accounts()
    entry = accounts.get(username)
    if entry is None:
        bf.record_failure(client_ip, username)
        return None

    if not _verify_password(password, entry["password_hash"]):
        bf.record_failure(client_ip, username)
        return None

    # Success — clear brute-force tracking
    bf.record_success(client_ip, username)

    # Create JWT token with role embedded
    token = create_jwt({
        "sub": username,
        "role": entry["role"],
    })

    # Store session metadata
    _sessions[token] = {
        "username": username,
        "role": entry["role"],
        "created": datetime.now(timezone.utc).isoformat(),
    }
    return token


def _resolve_session(token: str) -> dict | None:
    """Resolve a session token (JWT or legacy random token).

    For JWT tokens: verifies signature + expiry, returns claims as session dict.
    For legacy tokens: looks up in in-memory _sessions dict.
    Returns None for invalid/expired tokens.
    """
    # Try JWT verification first
    claims = verify_jwt(token)
    if claims is not None:
        return {
            "username": claims.get("sub", ""),
            "role": claims.get("role", "viewer"),
            "created": datetime.fromtimestamp(claims.get("iat", 0), tz=timezone.utc).isoformat(),
        }

    # Fall back to legacy in-memory session
    session = _sessions.get(token)
    if session is not None:
        # Check age: invalidate sessions older than 24h
        try:
            created = datetime.fromisoformat(session["created"])
            age = (datetime.now(timezone.utc) - created).total_seconds()
            if age > 86400:  # 24h
                _sessions.pop(token, None)
                return None
        except (ValueError, KeyError):
            pass
        return session

    return None


# ---------------------------------------------------------------------------
# Capability check
# ---------------------------------------------------------------------------

_EMPTY_SET: set = set()

# Operator names that always have full capabilities — these are the values
# returned by core.api.require_bridge_token (i.e. the CloudCLI plugin bridge
# that presents the shared API token).  No session lookup needed.
_BRIDGE_OPERATORS = frozenset([
    "cloudcli-plugin",       # core.api.BRIDGE_OPERATOR
    "dashboard-proxy",       # core.api.DASHBOARD_PROXY_OPERATOR
])


def check_capability(operator: str, capability: str) -> bool:
    """Return True if *operator* has *capability*, False otherwise.

    Known bridge-token operator names (cloudcli-plugin, dashboard-proxy)
    always have full operator capabilities — this matches the pre-15A
    behaviour where the single bridge token was the only auth mechanism.

    Session-token callers are resolved to a role via the in-process session
    store.  Unknown tokens → deny (fail closed).
    """
    # Known bridge-token operators — always full access
    if operator in _BRIDGE_OPERATORS:
        return True

    # Resolve session token to a role
    session = _resolve_session(operator)
    if session is None:
        return False  # fail closed — unknown token → deny

    role = session.get("role", "")
    allowed = ROLE_CAPABILITIES.get(role, _EMPTY_SET)
    return capability in allowed


def invalidate_session(token: str) -> None:
    """Remove a session token.  No-op for unknown tokens."""
    _sessions.pop(token, None)


def resolve_role(token: str) -> str | None:
    """Return the role for a session token, or None if not found."""
    session = _resolve_session(token)
    return session.get("role") if session else None


def is_bridge_token_operator(operator: str) -> bool:
    """True when *operator* is the existing bridge-token path, which always
    has full operator capabilities and doesn't need role resolution."""
    return isinstance(operator, str) and (
        "bridge-token" in operator.lower() or operator in _BRIDGE_OPERATORS
    )
