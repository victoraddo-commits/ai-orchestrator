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

from core.jwt_auth import create_jwt, verify_jwt, blocklist_token
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
    "services.manage": "Register/update/remove services",
    "capabilities.manage": "Register/update/remove capabilities and implementations",
}

# Exactly two roles — operator (everything) and viewer (read-only GETs).
# Adding a narrower role later means changing the mapping below, not redesigning
# the check — there's no lock-in cost to starting minimal.
ROLE_CAPABILITIES = {
    # Lazily built on first use — avoids import-order issues where
    # kai_tools.routes registers kai.tools.execute after this module
    # has already frozen the operator role's capability set.
    "operator": None,  # resolved lazily in check_capability()
    "viewer": set(),  # read-only — no write capabilities
    "device": {
        "kai.chat.send",       # chat with Kai
    },
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


def _map_vault_role_to_orch_role(vault_role: str) -> str:
    """Map vault SSO role to orchestrator role.

    Vault admin/operator → orchestrator operator (full capabilities)
    Vault auditor → orchestrator viewer (read-only)
    Unknown role → viewer (safe default)
    """
    if vault_role in ("admin", "operator"):
        return "operator"
    return "viewer"


def _resolve_session(token: str) -> dict | None:
    """Resolve a session token (JWT or legacy random token).

    For JWT tokens: verifies signature + expiry, returns claims as session dict.
    For legacy tokens: looks up in in-memory _sessions dict.
    Returns None for invalid/expired tokens.
    """
    # Try JWT verification first
    claims = verify_jwt(token)
    if claims is not None:
        # Check for vault_role claim (Phase 3 SSO integration)
        # The vault_role claim is trusted — it was set by this orchestrator's
        # own /auth/kai/callback after vault verified the operator's passkey.
        role = claims.get("role", "viewer")
        vault_role = claims.get("vault_role")
        if vault_role:
            role = _map_vault_role_to_orch_role(vault_role)
        return {
            "username": claims.get("sub", ""),
            "role": role,
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

    # Device bearer tokens (kai_device_ prefix) — long-lived, revocable
    if isinstance(token, str) and token.startswith("kai_device_"):
        from core.device_registry import find_device_by_token
        device = find_device_by_token(token)
        if device and device.get("status") == "authorized":
            return {
                "username": device["device_id"],
                "role": "device",
                "created": device.get("created_at", datetime.now(timezone.utc).isoformat()),
            }

    return None


# ---------------------------------------------------------------------------
# Capability check
# ---------------------------------------------------------------------------

_EMPTY_SET: set = set()


def _require_write_capability(capability: str):
    """FastAPI dependency factory.  Returns a callable that checks the caller
    has *capability* — either via the bridge token (always operator) or via
    a valid session token with the matching role capability."""

    def checker(
        authorization: str | None = None,
        x_kai_session: str | None = None,
    ):
        from fastapi import HTTPException
        from core.bridge_auth import _load_api_token

        session_token = x_kai_session or ""

        # Bridge-token path: the existing mechanism, always operator
        expected = f"Bearer {_load_api_token()}"
        if authorization:
            import hmac
            if hmac.compare_digest(authorization.encode(), expected.encode()):
                return "bridge-token:operator"

        # Session-token path: resolve role and check capability
        if session_token:
            if check_capability(session_token, capability):
                return session_token
            raise HTTPException(status_code=403, detail="Insufficient permissions")

        # Neither path — no valid credentials → 401
        raise HTTPException(status_code=401, detail="Missing or invalid credentials")

    from fastapi import Depends, Header
    # Header() defaults tell FastAPI to extract and inject these header values.
    # Without them, FastAPI ignores header names and passes None.
    checker.__defaults__ = (
        Header(default=None),
        Header(default=None, alias="x-kai-session"),
    )
    return Depends(checker)


# Bridge-token operators — always have full operator capabilities and bypass
# session lookup.  These correspond to core.api.BRIDGE_OPERATOR and
# core.api.DASHBOARD_PROXY_OPERATOR.
_BRIDGE_OPERATORS = frozenset([
    "cloudcli-plugin",
    "dashboard-proxy",
])


def get_operator_capabilities() -> set[str]:
    """Return the operator role's capability set, rebuilding it lazily.

    This is the public accessor for the operator role's capabilities.
    Use this instead of directly accessing ROLE_CAPABILITIES["operator"],
    which may be None before first resolution due to lazy initialization.
    """
    return _get_role_caps("operator")


def _get_role_caps(role: str) -> set[str]:
    """Return the capability set for *role*, rebuilding operator lazily."""
    if role == "operator":
        caps = ROLE_CAPABILITIES.get("operator")
        if caps is None:
            caps = set(CAPABILITIES.keys())
            ROLE_CAPABILITIES["operator"] = caps
        return caps
    return ROLE_CAPABILITIES.get(role, _EMPTY_SET)


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
    allowed = _get_role_caps(role)
    return capability in allowed


def invalidate_session(token: str) -> None:
    """Remove a session token.  Also blocklists the JWT so it can't be
    replayed — stateless JWTs are otherwise not revocable mid-lifetime."""
    _sessions.pop(token, None)
    blocklist_token(token)


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
