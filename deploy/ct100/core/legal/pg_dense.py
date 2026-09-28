"""pgvector dense provider for hybrid retrieval (Kai legal, structure chunks). in ``juris_chunks`` on the
CT111 Postgres (pgvector HNSW, cosine) instead of the local SQLite
``document_embeddings`` windows. Embeds the query with the same
:class:`core.legal.embeddings.LocalEmbeddingClient` the legacy index uses
(nomic-embed-text on VM104 via the CT100 tunnel), so the vector spaces match.

Result rows mirror :meth:`core.legal.embeddings.EmbeddingIndex.search` exactly
(document_id, title, citation, type, year, chunk_index, char_start, char_end,
score, snippet) so :func:`core.legal.hybrid.hybrid_search` can fuse them with
BM25 unchanged. Doc-level metadata (title/citation/...) is resolved lazily
from the local corpus storage and cached; if storage is not attached the
fields are None and ``hybrid._doc_view`` falls back to ``storage.get_document``.

Failures raise :class:`core.legal.embeddings.EmbeddingError`; the hybrid seam
(``_safe_dense``) degrades to BM25-only, so retrieval never fails.

Gate: only active when ``JURIS_KAI_DENSE_BACKEND=pgvector`` (wired in
``legal_brain_api.embedding_index``); default stays sqlite (no behaviour
change until flipped). Rollback = unset the env and restart.

Context augmentation (env ``JURIS_KAI_CHUNK_CONTEXT=1``): when building the
result snippet, append ``" … "`` plus the first 300 chars of the NEXT chunk
(looked up by ``doc_id + ordinal + 1``) so answers retain the
following-provision context that short structure chunks otherwise lose.
Best-effort: a missing row or lookup error never changes the base snippet.
"""
from __future__ import annotations

import json
import logging
import os
import threading

from core.legal.embeddings import (
    EMBEDDING_DIM,
    EmbeddingError,
    LocalEmbeddingClient,
)

logger = logging.getLogger("kai.legal.pg_dense")


def _env_flag(name: str) -> bool:
    """Truthy env gate: 1/true/yes/on (case-insensitive) enables."""
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")

DEFAULT_DSN_FILE = os.environ.get(
    "JURIS_PG_DSN_FILE", "/opt/kai-legal-brain/data/.pg_dsn")
DEFAULT_EF_SEARCH = 100
DEFAULT_CONNECT_TIMEOUT = 5.0
DEFAULT_CHUNK_CONTEXT_CHARS = 300

NEXT_CHUNK_SQL = (
    "SELECT doc_id, ordinal, chunk_text FROM juris_chunks "
    "WHERE (doc_id, ordinal) IN %s"
)

SQL = (
    "SELECT doc_id, ordinal, heading, chunk_text, start_char, end_char, "
    "1 - (embedding <=> %s::vector) AS score "
    "FROM juris_chunks "
    "ORDER BY embedding <=> %s::vector "
    "LIMIT %s"
)


def load_dsn(env_dsn=None, env_file=DEFAULT_DSN_FILE):
    """Resolve the pgvector DSN: env override, then the 0600 token file."""
    if env_dsn:
        return env_dsn
    if env_file:
        try:
            with open(env_file) as fh:
                dsn = fh.read().strip()
        except OSError:
            return None
        if dsn:
            return dsn
    return None


class PgVectorDense:
    """Dense provider backed by the CT111 Postgres ``juris_chunks`` table.

    Keeps one persistent connection guarded by a lock (the API server is
    multi-threaded but dense search is serialized at ~10-30ms per HNSW
    query); reconnects once on failure before giving up.
    """

    def __init__(self, storage=None, client=None, dsn=None,
                 ef_search: int = DEFAULT_EF_SEARCH,
                 connect_timeout: float = DEFAULT_CONNECT_TIMEOUT):
        self.storage = storage
        self.client = client or LocalEmbeddingClient()
        self.dsn = load_dsn(dsn)
        self.ef_search = int(ef_search)
        self.connect_timeout = float(connect_timeout)
        self.chunk_context = _env_flag("JURIS_KAI_CHUNK_CONTEXT")
        self._conn = None
        self._lock = threading.Lock()
        self._doc_cache: dict = {}

    def _connect(self):
        import psycopg2
        if not self.dsn:
            raise EmbeddingError(
                "pgvector DSN not configured (JURIS_PG_DSN or "
                f"{DEFAULT_DSN_FILE})")
        return psycopg2.connect(self.dsn,
                                connect_timeout=int(self.connect_timeout))

    def _connection(self):
        if self._conn is None or self._conn.closed:
            self._conn = self._connect()
            self._conn.autocommit = True
            with self._conn.cursor() as cur:
                cur.execute("SET hnsw.ef_search = %s", (self.ef_search,))
            self._conn.autocommit = False
        return self._conn

    def _doc_meta(self, doc_id):
        """Local corpus metadata for a pg doc_id (doc ids match SQLite ids)."""
        if doc_id in self._doc_cache:
            return self._doc_cache[doc_id]
        meta = None
        conn = getattr(self.storage, "conn", None) if self.storage else None
        if conn is not None:
            try:
                row = conn.execute(
                    "SELECT title, citation, type, year "
                    "FROM documents WHERE id = ?", (doc_id,)).fetchone()
                if row is not None:
                    meta = {"title": row[0], "citation": row[1],
                            "type": row[2], "year": row[3]}
            except Exception:  # noqa: BLE001 - metadata is best effort
                meta = None
        self._doc_cache[doc_id] = meta
        return meta

    def search(self, query: str, limit: int = 10) -> list:
        """Return the ``limit`` chunks most cosine-similar to ``query``."""
        if not (query or "").strip():
            return []
        query_vec = self.client.embed(query)
        if not query_vec or len(query_vec) != EMBEDDING_DIM:
            raise EmbeddingError(
                f"query embedding dim mismatch: {len(query_vec or [])}")
        vec_json = json.dumps([float(x) for x in query_vec])
        # ef_search must cover the fetch: pgvector scans ef candidates, so a
        # limit larger than ef silently degrades recall.
        ef = max(self.ef_search, int(limit))
        with self._lock:
            try:
                conn = self._connection()
                conn.autocommit = False
                with conn.cursor() as cur:
                    if ef != self.ef_search:
                        cur.execute("SET hnsw.ef_search = %s", (ef,))
                    cur.execute(SQL, (vec_json, vec_json, int(limit)))
                    rows = cur.fetchall()
                    context = self._next_chunk_texts(conn, rows)
                    conn.rollback()  # read-only txn; release the SET override
            except Exception as exc:
                try:
                    self._conn.close()
                except Exception:  # noqa: BLE001
                    pass
                self._conn = None
                # One reconnect attempt before giving up (transient restarts).
                try:
                    conn = self._connection()
                    conn.autocommit = False
                    with conn.cursor() as cur:
                        cur.execute("SET hnsw.ef_search = %s", (ef,))
                        cur.execute(SQL, (vec_json, vec_json, int(limit)))
                        rows = cur.fetchall()
                        context = self._next_chunk_texts(conn, rows)
                        conn.rollback()
                except Exception as exc2:
                    raise EmbeddingError(
                        f"pgvector dense search failed: {exc2}") from exc2
                logger.warning("pgvector reconnect succeeded after: %s", exc)
        return self._to_hits(rows, context)

    def _next_chunk_texts(self, conn, rows) -> dict:
        """Batched next-chunk text lookup: {(doc_id, next_ordinal): text}.

        One extra query per search (same transaction, best-effort): a
        missing row or lookup error yields {} and the base snippet survives.
        """
        if not self.chunk_context or not rows:
            return {}
        wanted = [(int(r[0]), int(r[1]) + 1) for r in rows]
        try:
            with conn.cursor() as cur:
                cur.execute(NEXT_CHUNK_SQL, (wanted,))
                found = cur.fetchall()
        except Exception as exc:  # noqa: BLE001 - context is optional
            logger.debug("chunk-context lookup failed: %s", exc)
            return {}
        return {(int(doc_id), int(ordinal)): text
                for doc_id, ordinal, text in found or []}

    def _to_hits(self, rows, context=None) -> list:
        hits = []
        for doc_id, ordinal, heading, chunk_text, start_char, end_char, score \
                in rows:
            meta = self._doc_meta(doc_id) or {}
            snippet = (chunk_text or "").strip()
            if heading and snippet.lower().startswith(heading.lower()[:24]):
                pass  # heading already inside the chunk text
            if self.chunk_context and context:
                nxt = (context or {}).get((int(doc_id), int(ordinal) + 1))
                if nxt:
                    context_text = " ".join(str(nxt).split())
                    snippet = (snippet + " … "
                               + context_text[:DEFAULT_CHUNK_CONTEXT_CHARS]
                               ).strip()
            hits.append({
                "document_id": int(doc_id),
                "title": (meta or {}).get("title"),
                "citation": (meta or {}).get("citation"),
                "type": (meta or {}).get("type"),
                "year": (meta or {}).get("year"),
                "chunk_index": int(ordinal),
                "char_start": start_char if start_char is not None else 0,
                "char_end": end_char if end_char is not None else 0,
                "score": float(score),
                "snippet": snippet,
            })
        return hits

    def _next_chunk_text(self, doc_id, ordinal):
        """First 300 chars of the chunk following (doc_id, ordinal).

        Kept for callers/tests that need a single lookup; the search path
        uses the batched :meth:`_next_chunk_texts` instead. Best-effort:
        returns "" on any failure so the base snippet survives.
        """
        try:
            conn = self._connection()
            conn.autocommit = False
            with conn.cursor() as cur:
                cur.execute(NEXT_CHUNK_SQL, ([(int(doc_id), int(ordinal) + 1)],))
                row = cur.fetchone()
                conn.rollback()
            if row and row[2]:
                return " ".join(str(row[2]).split())[:DEFAULT_CHUNK_CONTEXT_CHARS]
        except Exception as exc:  # noqa: BLE001 - context is optional
            logger.debug("chunk-context lookup failed for %s/%s: %s",
                         doc_id, ordinal, exc)
        return ""

    def stats(self) -> dict:
        return {"backend": "pgvector", "dsn_host": (
            self.dsn.split("@")[1].split("/")[0] if self.dsn else None),
            "ef_search": self.ef_search}
