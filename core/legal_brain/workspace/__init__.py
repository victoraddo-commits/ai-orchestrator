"""Temporary User Workspace — Isolated, Ephemeral.

Design:
  - Per-session SQLite databases (auto-created, auto-destroyed on TTL)
  - NEVER shares storage with permanent corpus
  - User uploads are scanned, processed, then destroyed
  - No code path allows promotion to permanent store
"""

import sqlite3
import uuid
import shutil
import os
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone, timedelta

from ..config import WORKSPACE_ROOT, WORKSPACE_TTL_SECONDS

_WORKSPACE_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS workspace_sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL,
    document_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS workspace_documents (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES workspace_sessions(id) ON DELETE CASCADE,
    original_filename TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_size_bytes INTEGER DEFAULT 0,
    page_count INTEGER DEFAULT 0,
    extracted_text TEXT,
    ocr_applied INTEGER DEFAULT 0,
    malware_scan_result TEXT,
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS workspace_analyses (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES workspace_sessions(id) ON DELETE CASCADE,
    document_id TEXT REFERENCES workspace_documents(id) ON DELETE CASCADE,
    analysis_type TEXT NOT NULL,
    result TEXT,
    model_used TEXT,
    confidence REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _expires_at() -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=WORKSPACE_TTL_SECONDS)).isoformat()


def _get_session_db_path(session_id: str) -> Path:
    return WORKSPACE_ROOT / f"session_{session_id}.db"


def _init_session_db(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_WORKSPACE_SCHEMA)
    conn.commit()
    conn.close()


def create_session(user_id: str) -> Dict[str, Any]:
    """Create a new workspace session for a user."""
    session_id = str(uuid.uuid4())
    db_path = _get_session_db_path(session_id)
    _init_session_db(db_path)
    now = _now()
    expires = _expires_at()

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """INSERT INTO workspace_sessions (id, user_id, status, created_at, expires_at)
           VALUES (?, ?, 'active', ?, ?)""",
        (session_id, user_id, now, expires),
    )
    conn.commit()
    conn.close()

    return {
        "session_id": session_id,
        "user_id": user_id,
        "status": "active",
        "created_at": now,
        "expires_at": expires,
    }


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    db_path = _get_session_db_path(session_id)
    if not db_path.exists():
        return None
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM workspace_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    session = dict(row)
    # Check expiry
    if session["expires_at"] < _now() and session["status"] == "active":
        session["status"] = "expired"
    return session


def add_document(
    session_id: str,
    original_filename: str,
    file_hash: str,
    file_path: str,
    file_size_bytes: int = 0,
    page_count: int = 0,
    extracted_text: Optional[str] = None,
    ocr_applied: bool = False,
    malware_scan_result: Optional[str] = None,
) -> str:
    """Add a document to a workspace session."""
    db_path = _get_session_db_path(session_id)
    if not db_path.exists():
        raise ValueError(f"Session {session_id} not found")

    doc_id = str(uuid.uuid4())
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """INSERT INTO workspace_documents
           (id, session_id, original_filename, file_hash, file_path,
            file_size_bytes, page_count, extracted_text, ocr_applied, malware_scan_result)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (doc_id, session_id, original_filename, file_hash, file_path,
         file_size_bytes, page_count, extracted_text, int(ocr_applied), malware_scan_result),
    )
    conn.execute(
        "UPDATE workspace_sessions SET document_count = document_count + 1 WHERE id = ?",
        (session_id,),
    )
    conn.commit()
    conn.close()
    return doc_id


def get_document(session_id: str, document_id: str) -> Optional[Dict[str, Any]]:
    db_path = _get_session_db_path(session_id)
    if not db_path.exists():
        return None
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM workspace_documents WHERE id = ? AND session_id = ?",
        (document_id, session_id),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_documents(session_id: str) -> List[Dict[str, Any]]:
    db_path = _get_session_db_path(session_id)
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM workspace_documents WHERE session_id = ? ORDER BY uploaded_at DESC",
        (session_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_analysis(
    session_id: str,
    document_id: Optional[str],
    analysis_type: str,
    result: str,
    model_used: Optional[str] = None,
    confidence: Optional[float] = None,
) -> str:
    """Save an analysis result to the workspace."""
    db_path = _get_session_db_path(session_id)
    if not db_path.exists():
        raise ValueError(f"Session {session_id} not found")

    analysis_id = str(uuid.uuid4())
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """INSERT INTO workspace_analyses
           (id, session_id, document_id, analysis_type, result, model_used, confidence)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (analysis_id, session_id, document_id, analysis_type, result, model_used, confidence),
    )
    conn.commit()
    conn.close()
    return analysis_id


def get_analyses(session_id: str) -> List[Dict[str, Any]]:
    db_path = _get_session_db_path(session_id)
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM workspace_analyses WHERE session_id = ? ORDER BY created_at DESC",
        (session_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def destroy_session(session_id: str) -> bool:
    """Destroy a workspace session and all its data.

    This is irreversible — data is permanently deleted.
    """
    db_path = _get_session_db_path(session_id)
    deleted = False

    if db_path.exists():
        # Delete the WAL files too
        for suffix in ["", "-wal", "-shm"]:
            p = Path(str(db_path) + suffix)
            if p.exists():
                p.unlink()
        deleted = True

    # Delete uploaded files
    session_dir = WORKSPACE_ROOT / session_id
    if session_dir.exists():
        shutil.rmtree(session_dir)

    return deleted


def cleanup_expired_sessions() -> int:
    """Remove all expired workspace sessions. Returns count of cleaned sessions."""
    cleaned = 0
    if not WORKSPACE_ROOT.exists():
        return 0

    for db_file in WORKSPACE_ROOT.glob("session_*.db"):
        session_id = db_file.stem.replace("session_", "")
        session = get_session(session_id)
        if session is None or session.get("status") == "expired":
            destroy_session(session_id)
            cleaned += 1

    return cleaned


def get_workspace_stats() -> Dict[str, Any]:
    """Get stats about active workspaces."""
    active = 0
    expired = 0
    total_docs = 0
    total_size = 0

    if not WORKSPACE_ROOT.exists():
        return {"active_sessions": 0, "expired_sessions": 0, "total_documents": 0, "total_size_bytes": 0}

    for db_file in WORKSPACE_ROOT.glob("session_*.db"):
        session_id = db_file.stem.replace("session_", "")
        session = get_session(session_id)
        if session is None:
            continue
        if session.get("status") == "active":
            active += 1
            total_docs += session.get("document_count", 0)
        else:
            expired += 1

        total_size += db_file.stat().st_size

    return {
        "active_sessions": active,
        "expired_sessions": expired,
        "total_documents": total_docs,
        "total_size_bytes": total_size,
    }
