"""Payments store — SQLite ledger for the payments subsystem.

One row per provider reference, keyed by `reference` for idempotency. Uses the
same sqlite3 context-manager pattern as the rest of the Kai tree. Any module
(betting, juris, susu, ...) can record and look up payments here.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = os.environ.get("PAYMENTS_DB", str(_REPO_ROOT / "memory" / "payments.db"))

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS payments (
    reference          TEXT PRIMARY KEY,
    provider           TEXT NOT NULL DEFAULT 'paystack',
    mode               TEXT DEFAULT '',
    status             TEXT NOT NULL DEFAULT 'initialized',
    amount             INTEGER NOT NULL DEFAULT 0,
    currency           TEXT NOT NULL DEFAULT 'GHS',
    email              TEXT DEFAULT '',
    requested_channel  TEXT DEFAULT '',
    channel            TEXT DEFAULT '',
    authorization_url  TEXT DEFAULT '',
    access_code        TEXT DEFAULT '',
    gateway_response   TEXT DEFAULT '',
    metadata           TEXT DEFAULT '{}',
    raw_response       TEXT DEFAULT '{}',
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT NOT NULL DEFAULT (datetime('now')),
    verified_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status);
CREATE INDEX IF NOT EXISTS idx_payments_created ON payments(created_at);
"""


def get_db_path() -> str:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return DB_PATH


@contextmanager
def get_db():
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA_SQL)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_db():
        pass


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    return dict(row) if row is not None else None


def get_payment(reference: str) -> Optional[Dict[str, Any]]:
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM payments WHERE reference = ?", (reference,)
        ).fetchone()
    return _row_to_dict(row)


def record_initialized(
    *,
    reference: str,
    amount: int,
    currency: str,
    email: str,
    authorization_url: str = "",
    access_code: str = "",
    mode: str = "",
    provider: str = "paystack",
    requested_channel: str = "",
    metadata: Optional[Any] = None,
    raw: Optional[Any] = None,
) -> Dict[str, Any]:
    """Insert an initialized payment. Idempotent: an existing row is kept."""
    metadata_json = metadata if isinstance(metadata, str) else json.dumps(metadata or {})
    raw_json = raw if isinstance(raw, str) else json.dumps(raw or {})
    with get_db() as db:
        db.execute(
            """
            INSERT OR IGNORE INTO payments
                (reference, provider, mode, status, amount, currency, email,
                 requested_channel, authorization_url, access_code, metadata,
                 raw_response)
            VALUES (?, ?, ?, 'initialized', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reference, provider, mode, amount, currency, email,
                requested_channel, authorization_url, access_code,
                metadata_json, raw_json,
            ),
        )
    return get_payment(reference)


def update_verified(
    reference: str,
    *,
    status: str,
    channel: str = "",
    gateway_response: str = "",
    amount: Optional[int] = None,
    currency: str = "",
    raw: Optional[Any] = None,
    provider: str = "paystack",
    mode: str = "",
) -> Dict[str, Any]:
    """Persist a verification result, creating the row if it does not exist."""
    raw_json = raw if isinstance(raw, str) else json.dumps(raw or {})
    with get_db() as db:
        existing = db.execute(
            "SELECT reference FROM payments WHERE reference = ?", (reference,)
        ).fetchone()
        if existing is None:
            db.execute(
                """
                INSERT INTO payments
                    (reference, provider, mode, status, amount, currency, channel,
                     gateway_response, raw_response, verified_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    reference, provider, mode, status, amount or 0,
                    currency or "GHS", channel, gateway_response, raw_json,
                ),
            )
        else:
            db.execute(
                """
                UPDATE payments
                SET status = ?, mode = COALESCE(NULLIF(?, ''), mode),
                    channel = ?, gateway_response = ?, raw_response = ?,
                    amount = COALESCE(?, amount),
                    currency = COALESCE(NULLIF(?, ''), currency),
                    verified_at = datetime('now'), updated_at = datetime('now')
                WHERE reference = ?
                """,
                (
                    status, mode, channel, gateway_response, raw_json,
                    amount, currency, reference,
                ),
            )
    return get_payment(reference)


def record_webhook(
    reference: str,
    *,
    event: str = "",
    status: str,
    channel: str = "",
    gateway_response: str = "",
    amount: Optional[int] = None,
    currency: str = "",
    raw: Optional[Any] = None,
    provider: str = "paystack",
    mode: str = "",
) -> Dict[str, Any]:
    """Idempotently apply a webhook event.

    Returns {"payment": <row>, "duplicate": bool}. A duplicate is a
    `charge.success` (or any success) already recorded as `success`.
    """
    raw_json = raw if isinstance(raw, str) else json.dumps(raw or {})
    duplicate = False
    with get_db() as db:
        existing = db.execute(
            "SELECT status FROM payments WHERE reference = ?", (reference,)
        ).fetchone()
        if existing is not None and existing["status"] == "success" and status == "success":
            duplicate = True
        elif existing is None:
            db.execute(
                """
                INSERT INTO payments
                    (reference, provider, mode, status, amount, currency, channel,
                     gateway_response, raw_response, verified_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    reference, provider, mode, status, amount or 0,
                    currency or "GHS", channel, gateway_response, raw_json,
                ),
            )
        else:
            db.execute(
                """
                UPDATE payments
                SET status = ?, mode = COALESCE(NULLIF(?, ''), mode),
                    channel = CASE WHEN ? != '' THEN ? ELSE channel END,
                    gateway_response = ?, raw_response = ?,
                    amount = COALESCE(?, amount),
                    currency = COALESCE(NULLIF(?, ''), currency),
                    verified_at = datetime('now'), updated_at = datetime('now')
                WHERE reference = ?
                """,
                (
                    status, mode, channel, channel, gateway_response, raw_json,
                    amount, currency, reference,
                ),
            )
    return {"payment": get_payment(reference), "duplicate": duplicate}


def list_payments(limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM payments ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return [dict(r) for r in rows]
