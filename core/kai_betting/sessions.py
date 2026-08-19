"""Kai Betting — session token issuance, verification, and revocation.

Tokens are opaque: only sha256(token) is ever persisted. The raw token is
returned to the caller once, at creation, and never stored server-side.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from typing import Optional

SESSION_EXPIRY_DAYS = 30


def hash_token(token: str) -> str:
    """sha256 hex digest of a raw session token."""
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: sqlite3.Connection, user_id: int) -> str:
    """Insert a session row, return the raw token (caller sends it to the client)."""
    token = secrets.token_urlsafe(32)
    db.execute(
        "INSERT INTO sessions (user_id, token_hash, expires_at) "
        "VALUES (?, ?, datetime('now', '+' || ? || ' days'))",
        (user_id, hash_token(token), str(SESSION_EXPIRY_DAYS)),
    )
    db.commit()
    return token


def resolve_session(db: sqlite3.Connection, token: str) -> Optional[sqlite3.Row]:
    """Look up a session by token hash, joined to its user row.

    Returns None if the token doesn't exist, belongs to a deactivated user,
    or is expired (and deletes the expired row when found, as a lazy
    cleanup). Bumps last_seen_at on a successful resolution as a freshness
    signal only, not a sliding expiry.
    """
    token_hash = hash_token(token)
    row = db.execute(
        """SELECT u.*, s.id as session_id, s.expires_at as session_expires_at
           FROM sessions s JOIN users u ON u.id = s.user_id
           WHERE s.token_hash = ? AND u.is_active = 1""",
        (token_hash,),
    ).fetchone()
    if not row:
        return None

    now = db.execute("SELECT datetime('now') as now").fetchone()["now"]
    if row["session_expires_at"] <= now:
        db.execute("DELETE FROM sessions WHERE id = ?", (row["session_id"],))
        db.commit()
        return None

    db.execute(
        "UPDATE sessions SET last_seen_at = datetime('now') WHERE id = ?",
        (row["session_id"],),
    )
    db.commit()
    return row


def delete_session(db: sqlite3.Connection, token: str) -> None:
    """Used by /auth/logout. No-op if the token doesn't exist."""
    db.execute("DELETE FROM sessions WHERE token_hash = ?", (hash_token(token),))
    db.commit()
