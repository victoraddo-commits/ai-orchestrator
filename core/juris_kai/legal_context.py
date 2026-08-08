"""Juris Kai Legal Context — dedicated connection to the Legal Brain knowledge base.

This module provides Juris Kai with its OWN direct database connection to the
Legal Brain's permanent WORM store. It does NOT share any bridge, router, or
connection pool with other modules — this is Juris Kai's exclusive interface
to the Ghana legal corpus.

The legal-brain database is a LOCAL SQLite file at the path configured in
core/legal_brain/config.py (default: /var/lib/ai-orchestrator/legal_brain/).
No network calls, no shared connections.

Architecture:
  Juris Kai Bot → legal_context.query_knowledge_base() → Legal Brain DB
                 ↓ (no shared bridge)
            AI Provider (deepseek_native_pro)

Flow:
  1. User sends legal query to @Juriskai_bot
  2. Bot calls query_knowledge_base(query) to search local legal documents
  3. Matching document chunks are included in the AI prompt as context
  4. AI generates response grounded in the retrieved legal sources
  5. If no documents found, AI responds with disclaimer
"""

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger("juris_kai.legal_context")

# Dedicated path — same as legal-brain config, NOT a shared connection
LEGAL_BRAIN_DB = Path("/var/lib/ai-orchestrator/legal_brain/permanent/legal_brain.db")

# Maximum chunks to include in AI context (avoid overflowing the prompt)
MAX_CONTEXT_CHUNKS = 5
MAX_CHUNK_LENGTH = 2000


def _get_connection() -> sqlite3.Connection | None:
    """Open a dedicated read-only connection to the legal-brain database."""
    if not LEGAL_BRAIN_DB.exists():
        logger.warning(f"Legal Brain database not found at {LEGAL_BRAIN_DB}")
        return None
    try:
        conn = sqlite3.connect(f"file:{LEGAL_BRAIN_DB}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        logger.error(f"Failed to connect to Legal Brain DB: {e}")
        return None


def query_knowledge_base(query: str) -> list[dict]:
    """Search the Ghana legal corpus for documents relevant to the query.

    Searches document titles and chunk content. Returns up to MAX_CONTEXT_CHUNKS
    matching chunks with their source document metadata.

    Args:
        query: The user's legal query text

    Returns:
        List of dicts with keys: title, category, court, year, citation,
        chunk_content, jurisdiction
    """
    conn = _get_connection()
    if conn is None:
        return []

    try:
        # Search strategy: keyword match across document titles and chunk content
        # Use LIKE with keywords extracted from the query
        keywords = [w.strip().lower() for w in query.split() if len(w.strip()) > 2]
        if not keywords:
            return []

        # Build a search that matches document titles OR chunk content.
        # Parameter ordering: all title LIKE params first, then all chunk LIKE
        # params, then LIMIT. The ORDER BY repeats title params to sort title
        # matches first — each repetition adds the same params again.
        title_clauses = ["d.title LIKE ?" for _ in keywords]
        chunk_clauses = ["c.content LIKE ?" for _ in keywords]
        title_conditions = " OR ".join(title_clauses)
        chunk_conditions = " OR ".join(chunk_clauses)

        like_values = [f"%{kw}%" for kw in keywords]

        # Params: title_likes + chunk_likes + title_likes (ORDER BY) + LIMIT
        params = like_values + like_values + like_values + [MAX_CONTEXT_CHUNKS]

        sql = f"""
            SELECT DISTINCT
                d.title,
                d.category,
                d.court,
                d.year,
                d.citation_text,
                d.jurisdiction,
                c.content as chunk_content,
                c.chunk_index
            FROM documents d
            JOIN chunks c ON c.document_id = d.id
            WHERE d.jurisdiction = 'Ghana'
              AND d.review_status = 'approved'
              AND ({title_conditions} OR {chunk_conditions})
            ORDER BY
                CASE WHEN ({title_conditions}) THEN 0 ELSE 1 END,
                d.year DESC,
                c.chunk_index ASC
            LIMIT ?
        """

        rows = conn.execute(sql, params).fetchall()

        results = []
        for row in rows:
            chunk = row["chunk_content"] or ""
            if len(chunk) > MAX_CHUNK_LENGTH:
                chunk = chunk[:MAX_CHUNK_LENGTH] + "..."
            results.append({
                "title": row["title"],
                "category": row["category"],
                "court": row["court"],
                "year": row["year"],
                "citation": row["citation_text"],
                "jurisdiction": row["jurisdiction"],
                "chunk_content": chunk,
            })

        if results:
            logger.info(
                f"Legal Brain search: {len(results)} chunks found for '{query[:80]}'"
            )
        else:
            logger.info(f"Legal Brain search: no results for '{query[:80]}'")

        return results
    except Exception as e:
        logger.error(f"Legal Brain query error: {e}")
        return []
    finally:
        conn.close()


def build_context_preamble(search_results: list[dict]) -> str:
    """Build a context preamble from legal-brain search results for the AI prompt.

    If documents are found, includes them as authoritative Ghana legal sources.
    If no documents are found, instructs the AI to be transparent about it.

    Args:
        search_results: Results from query_knowledge_base()

    Returns:
        A string to prepend to the AI prompt, or empty string if no results
    """
    if not search_results:
        return (
            "\n\nLEGAL KNOWLEDGE BASE: No matching Ghana legal documents were found "
            "in the database for this query. Answer based on your knowledge of Ghana "
            "law only. If you are uncertain, state that clearly rather than guessing. "
            "Cite specific Ghanaian statutes and cases wherever possible."
        )

    lines = [
        "\n\nRELEVANT GHANA LEGAL SOURCES (from the Juris Kai knowledge base):",
    ]
    for i, doc in enumerate(search_results, 1):
        cite = doc["citation"] or ""
        court_str = f" [{doc['court']}]" if doc.get("court") else ""
        year_str = f" ({doc['year']})" if doc.get("year") else ""
        lines.append(
            f"\nSOURCE {i}: {doc['title']}{year_str}{court_str}"
        )
        if cite:
            lines.append(f"  Citation: {cite}")
        lines.append(f"  Category: {doc['category']} | Jurisdiction: {doc['jurisdiction']}")
        lines.append(f"  Text: {doc['chunk_content']}")

    lines.append(
        "\n\nINSTRUCTION: Base your answer primarily on the Ghana legal sources "
        "provided above. Cite them by title and citation. If the sources do not "
        "fully answer the query, supplement with your knowledge of Ghana law — "
        "but clearly distinguish between what comes from the sources and what "
        "comes from your general knowledge."
    )
    return "\n".join(lines)
