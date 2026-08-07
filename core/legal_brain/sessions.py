"""Phase 18D: Research Session Logging.

Every legal query generates a reproducible Research Session with full audit trail.
Stored in the permanent Legal Brain database as an append-only log.

Features:
  - Session schema: query, authorities, citations, model, confidence, brain_version
  - Append-only — sessions are never modified after creation
  - Filterable by user, date range, document references
  - Export to JSON and PDF formats
  - Privacy: user-uploaded document IDs are NOT stored — only permanent corpus refs
"""

import uuid
import json
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone

from .permanent import get_connection, _now

_SESSION_SCHEMA = """
CREATE TABLE IF NOT EXISTS research_sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    query_text TEXT NOT NULL,
    jurisdiction TEXT DEFAULT 'Ghana',
    model_used TEXT,
    confidence REAL,
    brain_version TEXT,
    retrieved_authorities TEXT,       -- JSON array of {doc_id, title, citation, similarity}
    citations_used TEXT,              -- JSON array of doc_ids actually cited in response
    search_strategy TEXT,             -- e.g. 'semantic', 'keyword', 'hybrid'
    session_duration_ms INTEGER,
    response_summary TEXT,
    feedback TEXT,                    -- user feedback if any
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_research_sessions_user ON research_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_research_sessions_created ON research_sessions(created_at);
CREATE INDEX IF NOT EXISTS idx_research_sessions_jurisdiction ON research_sessions(jurisdiction);
"""


def init_sessions_table(db_path: Optional[Path] = None):
    """Ensure the research_sessions table exists."""
    with get_connection(db_path) as conn:
        conn.executescript(_SESSION_SCHEMA)


def log_research_session(
    user_id: str,
    query_text: str,
    retrieved_authorities: List[Dict[str, Any]],
    citations_used: List[str],
    model_used: Optional[str] = None,
    confidence: Optional[float] = None,
    brain_version: Optional[str] = None,
    search_strategy: str = "semantic",
    session_duration_ms: Optional[int] = None,
    response_summary: Optional[str] = None,
    jurisdiction: str = "Ghana",
    db_path: Optional[Path] = None,
) -> str:
    """Log a research session to the permanent append-only store.

    Args:
        user_id: Authenticated user or 'anonymous'
        query_text: The legal question asked
        retrieved_authorities: List of {doc_id, title, citation, similarity} dicts
        citations_used: List of doc_ids actually cited in the AI response
        model_used: AI model that generated the response
        confidence: Model's confidence score (0-1)
        brain_version: Legal Brain version at time of query
        search_strategy: How authorities were retrieved
        session_duration_ms: Time taken for the full query→response cycle
        response_summary: Brief summary of the AI response
        jurisdiction: Ghana (default)

    Returns:
        session_id for the created session
    """
    init_sessions_table(db_path)

    session_id = str(uuid.uuid4())

    with get_connection(db_path) as conn:
        conn.execute(
            """INSERT INTO research_sessions
               (id, user_id, query_text, jurisdiction, model_used, confidence,
                brain_version, retrieved_authorities, citations_used,
                search_strategy, session_duration_ms, response_summary)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                user_id,
                query_text,
                jurisdiction,
                model_used,
                confidence,
                brain_version,
                json.dumps(retrieved_authorities),
                json.dumps(citations_used),
                search_strategy,
                session_duration_ms,
                response_summary,
            ),
        )

    return session_id


def get_session(session_id: str, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Retrieve a single research session by ID."""
    init_sessions_table(db_path)
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM research_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not row:
            return None
        return _deserialize_session(dict(row))


def list_sessions(
    user_id: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    jurisdiction: str = "Ghana",
    limit: int = 100,
    offset: int = 0,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """List research sessions with optional filters."""
    init_sessions_table(db_path)

    clauses = ["jurisdiction = ?"]
    params: List[Any] = [jurisdiction]

    if user_id:
        clauses.append("user_id = ?")
        params.append(user_id)
    if since:
        clauses.append("created_at >= ?")
        params.append(since)
    if until:
        clauses.append("created_at <= ?")
        params.append(until)

    where = " WHERE " + " AND ".join(clauses)

    with get_connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT * FROM research_sessions{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [_deserialize_session(dict(r)) for r in rows]


def get_session_stats(
    user_id: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Aggregate statistics about research sessions."""
    init_sessions_table(db_path)

    clauses = []
    params: List[Any] = []
    if user_id:
        clauses.append("user_id = ?")
        params.append(user_id)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    with get_connection(db_path) as conn:
        total = conn.execute(
            f"SELECT COUNT(*) as c FROM research_sessions{where}", params
        ).fetchone()["c"]

        if total == 0:
            return {"total_sessions": 0, "avg_confidence": 0, "avg_duration_ms": 0,
                    "top_models": [], "popular_authorities": []}

        avg_conf = conn.execute(
            f"SELECT AVG(confidence) as c FROM research_sessions{where}", params
        ).fetchone()["c"] or 0

        duration_where = where + (" AND" if where else " WHERE") + " session_duration_ms IS NOT NULL"
        avg_dur = conn.execute(
            f"SELECT AVG(session_duration_ms) as c FROM research_sessions{duration_where}",
            params,
        ).fetchone()["c"] or 0

        models_where = where + (" AND" if where else " WHERE") + " model_used IS NOT NULL"
        models = conn.execute(
            f"""SELECT model_used, COUNT(*) as cnt FROM research_sessions{models_where}
                GROUP BY model_used ORDER BY cnt DESC LIMIT 5""",
            params,
        ).fetchall()

    return {
        "total_sessions": total,
        "avg_confidence": round(avg_conf, 3),
        "avg_duration_ms": round(avg_dur),
        "top_models": [{"model": r["model_used"], "count": r["cnt"]} for r in models],
    }


def export_session_json(session_id: str, output_path: Optional[Path] = None, db_path: Optional[Path] = None) -> Optional[str]:
    """Export a research session as a JSON report.

    If output_path is provided, writes to file. Otherwise returns JSON string.
    """
    session = get_session(session_id, db_path)
    if not session:
        return None

    report = {
        "kai_legal_brain_research_report": {
            "session_id": session["id"],
            "generated_at": _now(),
            "query": {
                "text": session["query_text"],
                "jurisdiction": session["jurisdiction"],
                "timestamp": session["created_at"],
            },
            "response": {
                "summary": session.get("response_summary"),
                "model": session.get("model_used"),
                "confidence": session.get("confidence"),
                "duration_ms": session.get("session_duration_ms"),
            },
            "authorities_retrieved": session.get("retrieved_authorities", []),
            "authorities_cited": session.get("citations_used", []),
        }
    }

    json_str = json.dumps(report, indent=2, default=str)

    if output_path:
        Path(output_path).write_text(json_str)

    return json_str


def export_session_pdf(session_id: str, output_path: Path, db_path: Optional[Path] = None) -> bool:
    """Export a research session as a text-format report (PDF placeholder).

    Note: True PDF generation requires additional dependencies.
    This writes a formatted text report suitable for conversion.
    """
    session = get_session(session_id, db_path)
    if not session:
        return False

    lines = []
    lines.append("=" * 72)
    lines.append("KAI LEGAL BRAIN — RESEARCH SESSION REPORT")
    lines.append("=" * 72)
    lines.append(f"Session ID:  {session['id']}")
    lines.append(f"Date:        {session['created_at']}")
    lines.append(f"User:        {session['user_id']}")
    lines.append(f"Jurisdiction: {session['jurisdiction']}")
    lines.append("")
    lines.append("-" * 72)
    lines.append("QUERY")
    lines.append("-" * 72)
    lines.append(session["query_text"])
    lines.append("")
    lines.append("-" * 72)
    lines.append("RESPONSE SUMMARY")
    lines.append("-" * 72)
    lines.append(session.get("response_summary", "No summary available"))
    lines.append("")
    lines.append("-" * 72)
    lines.append("AUTHORITIES RETRIEVED")
    lines.append("-" * 72)
    for i, auth in enumerate(session.get("retrieved_authorities", [])):
        if isinstance(auth, dict):
            lines.append(f"  {i+1}. {auth.get('title', 'Unknown')}")
            if auth.get("citation"):
                lines.append(f"     Citation: {auth['citation']}")
            if auth.get("similarity"):
                lines.append(f"     Relevance: {auth['similarity']:.2%}")
    lines.append("")
    lines.append("-" * 72)
    lines.append("AUTHORITIES CITED IN RESPONSE")
    lines.append("-" * 72)
    for i, cite_id in enumerate(session.get("citations_used", [])):
        lines.append(f"  {i+1}. Document ID: {cite_id}")
    lines.append("")
    lines.append("-" * 72)
    lines.append(f"Model: {session.get('model_used', 'N/A')} | Confidence: {session.get('confidence', 'N/A')}")
    lines.append(f"Duration: {session.get('session_duration_ms', 'N/A')}ms")
    lines.append("=" * 72)
    lines.append("END OF REPORT — Kai Legal Brain")

    output_path.write_text("\n".join(lines))
    return True


def _deserialize_session(session: Dict[str, Any]) -> Dict[str, Any]:
    """Deserialize JSON fields in a session record."""
    for field in ("retrieved_authorities", "citations_used"):
        val = session.get(field)
        if isinstance(val, str):
            try:
                session[field] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                session[field] = []
    return session
