"""Local-only embedding client + dense vector index (Legal Brain 2.0, Phase 1).

Embeddings are produced by the **Kai model fabric** — ``nomic-embed-text``
(768-dim, retrieval-tuned) served by ollama on VM104, reached from CT100
through a localhost SSH tunnel (``127.0.0.1:11434``). Nothing here ever calls a
cloud provider: :class:`LocalEmbeddingClient` refuses any non-local endpoint and
re-checks the URL immediately before every request, so a misconfigured
environment fails closed instead of leaking corpus text to a remote API.

Vectors are stored in SQLite as float32 BLOBs (``document_embeddings``) with a
per-document state row (``embedding_documents``) keyed by
``(document_id, model, version)``. Ranking uses cosine similarity over a numpy
matrix when numpy is present, with a pure-Python fallback — sqlite-vec is not
installed, and a separate vector DB is not justified at this scale (YAGNI).
"""
from __future__ import annotations

import array
import hashlib
import ipaddress
import json
import os
import re
import urllib.request
from datetime import datetime, timezone
from typing import Iterable, Optional
from urllib.parse import urlparse

# ── Configuration ─────────────────────────────────────────────────────────
EMBEDDING_MODEL = os.environ.get("KAI_LEGAL_EMBED_MODEL", "nomic-embed-text")
EMBEDDING_VERSION = os.environ.get("KAI_LEGAL_EMBED_VERSION", "v1")
DEFAULT_ENDPOINT = os.environ.get("KAI_LEGAL_EMBED_URL", "http://127.0.0.1:11434")

# Known output dimensions; a model not listed falls back to EMBEDDING_DIM and
# the real dimension is recorded from the first successful response.
KNOWN_DIMS = {"nomic-embed-text": 768, "bge-m3": 1024, "bge-small-en-v1.5": 384}
EMBEDDING_DIM = KNOWN_DIMS[EMBEDDING_MODEL] if EMBEDDING_MODEL in KNOWN_DIMS else 768

CHUNK_SIZE = 1100
CHUNK_OVERLAP = 150


class EmbeddingError(RuntimeError):
    """Raised when a local embedding call fails or returns garbage.

    Callers degrade gracefully (BM25-only retrieval, skipped document) rather
    than propagating a network error — but they never fall back to the cloud.
    """


def embedding_dim(model: Optional[str] = None) -> int:
    """Expected vector dimension for ``model`` (defaults to the configured one)."""
    name = model or EMBEDDING_MODEL
    return KNOWN_DIMS.get(name, EMBEDDING_DIM)


def is_local_endpoint(url: str) -> bool:
    """Whether ``url`` points at a loopback/private host.

    This is the local-only invariant: only loopback, RFC1918 private, link-local
    or ``*.local``/``localhost`` hosts are accepted. A public host returns
    ``False`` so the client can refuse it.
    """
    if not url:
        return False
    candidate = url if "://" in url else f"http://{url}"
    try:
        host = urlparse(candidate).hostname
    except ValueError:
        return False
    if not host:
        return False
    host = host.strip("[]").lower()
    if host in ("localhost", "::1") or host.endswith(".localhost") \
            or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(ip.is_loopback or ip.is_private or ip.is_link_local
                or ip.is_unspecified)


class LocalEmbeddingClient:
    """Minimal ollama ``/api/embed`` client, hard-wired to local hosts only.

    ``transport(url, payload) -> dict`` is injectable so tests can exercise the
    client without a model; the default uses ``urllib`` (stdlib, no requests
    dependency).
    """

    def __init__(self, endpoint: Optional[str] = None,
                 model: Optional[str] = None, timeout: float = 60.0,
                 transport=None):
        self.endpoint = (endpoint or DEFAULT_ENDPOINT).rstrip("/")
        if not is_local_endpoint(self.endpoint):
            raise ValueError(
                f"refusing non-local embedding endpoint: {self.endpoint!r} "
                "(local-only invariant)")
        self.model = model or EMBEDDING_MODEL
        self.timeout = timeout
        self._transport = transport
        self.dim = embedding_dim(self.model)

    def _post(self, url: str, payload: dict) -> dict:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def embed_batch(self, texts: Iterable[str]) -> list[list[float]]:
        """Embed a batch of texts; raises :class:`EmbeddingError` on failure."""
        texts = list(texts)
        if not texts:
            return []
        url = f"{self.endpoint}/api/embed"
        # Re-check right before the request: fail closed, never call out.
        if not is_local_endpoint(url):
            raise EmbeddingError(
                f"refusing non-local embedding endpoint: {url!r}")
        transport = self._transport or self._post
        try:
            data = transport(url, {"model": self.model, "input": texts})
        except Exception as exc:  # noqa: BLE001 - normalize every failure
            raise EmbeddingError(
                f"local embedding request failed: {exc}") from exc
        vecs = data.get("embeddings") if isinstance(data, dict) else None
        if not isinstance(vecs, list) or len(vecs) != len(texts):
            raise EmbeddingError("malformed embedding response")
        dims = {len(v) for v in vecs if isinstance(v, (list, tuple))}
        if len(dims) != 1 or not dims:
            raise EmbeddingError("inconsistent embedding dimensions")
        self.dim = dims.pop()
        return [[float(x) for x in v] for v in vecs]

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]


def embed_text(text: str, client: Optional[LocalEmbeddingClient] = None,
               **kwargs) -> list[float]:
    """Embed one string with ``client`` (or a new default local client)."""
    client = client or LocalEmbeddingClient(**kwargs)
    return client.embed(text)


def chunk_text(text: str, size: int = CHUNK_SIZE,
               overlap: int = CHUNK_OVERLAP) -> list[dict]:
    """Split ``text`` into ~``size``-char windows with ``overlap`` chars.

    Windows break on a paragraph/sentence/whitespace boundary in the back half
    where possible, always cover the whole text, and never exceed ``size``.
    Each chunk is ``{text, char_start, char_end}`` with offsets into the
    original string (so ``text[char_start:char_end]`` reproduces ``text``).
    """
    text = text or ""
    if not text.strip():
        return []
    if overlap < 0:
        overlap = 0
    if overlap >= size:
        overlap = size // 4
    chunks: list[dict] = []
    n = len(text)
    start = 0
    while start < n:
        end = min(start + size, n)
        if end < n:
            window = text[start:end]
            cut = max(window.rfind("\n\n"), window.rfind(". "),
                      window.rfind("\n"), window.rfind(" "))
            if cut > size // 2:
                end = start + cut + 1
        chunk = text[start:end]
        if chunk.strip():
            chunks.append({"text": chunk, "char_start": start, "char_end": end})
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return chunks


def pack_vector(vec: Iterable[float]) -> bytes:
    """Pack a vector into a little-endian float32 BLOB."""
    return array.array("f", [float(x) for x in vec]).tobytes()


def unpack_vector(blob: bytes) -> list[float]:
    """Unpack a float32 BLOB produced by :func:`pack_vector`."""
    arr = array.array("f")
    arr.frombytes(blob)
    return list(arr)


def cosine(a: Iterable[float], b: Iterable[float]) -> float:
    """Cosine similarity; ``0.0`` for a zero-length/zero vector or mismatch."""
    a = list(a)
    b = list(b)
    if not a or not b or len(a) != len(b):
        return 0.0
    try:
        import numpy as np  # noqa: PLC0415 - optional fast path
        va = np.asarray(a, dtype=np.float32)
        vb = np.asarray(b, dtype=np.float32)
        na = float(np.linalg.norm(va))
        nb = float(np.linalg.norm(vb))
        if na == 0.0 or nb == 0.0:
            return 0.0
        return float(np.dot(va, vb) / (na * nb))
    except Exception:  # noqa: BLE001 - pure-Python fallback
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(y * y for y in b) ** 0.5
        if na == 0.0 or nb == 0.0:
            return 0.0
        return dot / (na * nb)


class EmbeddingIndex:
    """Chunk-level dense index over the rights-cleared legal corpus."""

    def __init__(self, storage, client=None, model: Optional[str] = None,
                 version: Optional[str] = None, store_modes=("full",),
                 dim: Optional[int] = None, embed_batch_size: int = 64,
                 use_ann: bool = True, ann_path: Optional[str] = None):
        self.storage = storage
        self.model = model or EMBEDDING_MODEL
        self.version = version or EMBEDDING_VERSION
        self.client = client or LocalEmbeddingClient(model=self.model)
        self.store_modes = tuple(store_modes) or ("full",)
        self.dim = dim or embedding_dim(self.model)
        self.embed_batch_size = max(1, int(embed_batch_size))
        # Optional persisted faiss-HNSW accelerator. It only changes latency:
        # any missing/stale/unavailable index degrades to exact brute force.
        self.use_ann = bool(use_ann)
        self.ann_path = ann_path

    def ann_index_path(self) -> Optional[str]:
        """Resolved ANN file for ``(model, version)`` (``None`` disables it).

        The default is ABSOLUTE (derived from the corpus DB location) rather
        than the relative "data/ann": when the process ran from any directory
        other than the app root the relative path resolved elsewhere, the ANN
        failed to load, and search silently fell back to an ~8x slower brute
        force (~54ms -> ~443ms per query).
        """
        if self.ann_path:
            return self.ann_path
        base = (os.environ.get("KAI_LEGAL_ANN_DIR") or "").strip()
        if not base:
            base = os.path.join(os.path.dirname(os.path.abspath(
                os.environ.get("KAI_LEGAL_DB",
                               "/opt/kai-legal-brain/data/legal_brain.db"))), "ann")
        safe_model = re.sub(r"[^\w.-]", "_", self.model)
        safe_version = re.sub(r"[^\w.-]", "_", self.version)
        return os.path.join(base, f"{safe_model}_{safe_version}.faiss")

    def index_missing(self, limit: Optional[int] = None, max_failures: int = 5,
                      on_progress=None) -> dict:
        """Index documents that lack vectors for ``(model, version)``.

        Idempotent + resumable: a document whose stored ``content_hash`` matches
        the current one is skipped, so re-running continues where it stopped.
        ``limit`` bounds how many documents are *embedded* this run. Embedding
        failures are counted and skipped (never raised) unless ``max_failures``
        consecutive failures occur.
        """
        candidates = self.storage.embedding_candidates(self.store_modes)
        report = {"model": self.model, "version": self.version,
                  "documents_total": len(candidates), "indexed": 0,
                  "skipped": 0, "failed": 0, "chunks": 0}
        indexed = 0
        failures = 0
        for row in candidates:
            doc = dict(row)
            existing = self.storage.get_embedding_document(
                doc["id"], self.model, self.version)
            if existing and existing.get("content_hash") == doc["content_hash"]:
                report["skipped"] += 1
                continue
            if limit is not None and indexed >= limit:
                break
            try:
                n_chunks = self.index_document(doc)
            except EmbeddingError:
                report["failed"] += 1
                failures += 1
                if max_failures and failures >= max_failures:
                    report["aborted"] = "max_failures"
                    break
                continue
            report["indexed"] += 1
            report["chunks"] += n_chunks
            indexed += 1
            failures = 0
            if on_progress is not None:
                on_progress(report)
        return report

    def index_document(self, doc: dict) -> int:
        """Embed + store every chunk of one document; returns the chunk count."""
        content = doc.get("content") or ""
        chunks = chunk_text(content)
        if not chunks:
            return 0
        texts = [c["text"] for c in chunks]
        vectors: list[list[float]] = []
        size = self.embed_batch_size
        for i in range(0, len(texts), size):
            part = self.client.embed_batch(texts[i:i + size])
            if len(part) != len(texts[i:i + size]):
                raise EmbeddingError(
                    "embedding count does not match chunk count")
            vectors.extend(part)
        if len(vectors) != len(chunks):
            raise EmbeddingError("embedding count does not match chunk count")
        self.dim = len(vectors[0])
        rows = []
        for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
            rows.append({
                "chunk_index": i,
                "chunk_hash": hashlib.sha256(
                    chunk["text"].encode("utf-8")).hexdigest(),
                "char_start": chunk["char_start"],
                "char_end": chunk["char_end"],
                "vector": pack_vector(vec),
            })
        return self.storage.replace_document_embeddings(
            doc["id"], self.model, self.version, self.dim,
            doc["content_hash"], rows)

    # ── Optional ANN accelerator (Phase E Task 1) ────────────────────────
    def _ann_meta(self, count: int) -> dict:
        return {"model": self.model, "version": self.version, "count": count,
                "built_at": datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ")}

    def _rows_to_vectors(self, rows):
        """Return ``(float32 matrix, keys)`` for ``iter_embeddings`` rows."""
        import numpy as np  # noqa: PLC0415 - optional fast path
        dim = len(rows[0]["vector"]) // 4
        if dim and all(len(r["vector"]) == dim * 4 for r in rows):
            buf = b"".join(bytes(r["vector"]) for r in rows)
            matrix = np.frombuffer(buf, dtype=np.float32).reshape(len(rows), dim)
        else:
            matrix = np.asarray([unpack_vector(r["vector"]) for r in rows],
                                dtype=np.float32)
        keys = [(r["document_id"], r["chunk_index"], r["char_start"],
                 r["char_end"]) for r in rows]
        return matrix, keys

    def _load_ann(self):
        """Return a fresh loaded :class:`AnnIndex`, or ``None`` to brute force."""
        if not self.use_ann:
            return None
        path = self.ann_index_path()
        if not path:
            return None
        try:
            from core.legal import ann_index  # noqa: PLC0415 - lazy heavy import
            if not ann_index.faiss_available():
                return None
            index = ann_index.load_cached(path)
        except Exception:  # noqa: BLE001 - any ANN failure means brute force
            return None
        if index is None:
            return None
        meta = index.meta or {}
        if meta.get("model") not in (None, self.model) \
                or meta.get("version") not in (None, self.version):
            return None
        if index.dim != self.dim:
            return None
        try:
            current = self.storage.embedding_chunk_count(self.model, self.version)
        except Exception:  # noqa: BLE001 - unverifiable index is not used
            return None
        if index.n != current:
            return None
        return index

    def _ann_search(self, index, query_vec, limit: int) -> list[dict]:
        hits = []
        for pos, score in index.search(query_vec, k=limit):
            key = index.key(pos)
            if not key:
                continue
            doc_id, chunk_index, char_start, char_end = (list(key) + [None] * 4)[:4]
            doc = self.storage.get_document(doc_id) or {}
            content = doc.get("content") or ""
            snippet = (content[char_start:char_end]
                       if char_start is not None and char_end is not None
                       else content)
            hits.append({
                "document_id": doc_id,
                "title": doc.get("title"),
                "citation": doc.get("citation"),
                "type": doc.get("type"),
                "year": doc.get("year"),
                "chunk_index": chunk_index,
                "char_start": char_start,
                "char_end": char_end,
                "score": float(score),
                "snippet": snippet,
            })
        return hits

    def build_ann(self, path: Optional[str] = None, *, m: int = 32,
                  ef_construction: int = 200, ef_search: int = 64) -> dict:
        """Build + persist an ANN snapshot from the current embeddings."""
        try:
            from core.legal import ann_index  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            return {"built": False, "reason": f"import_failed: {exc}"}
        if not ann_index.faiss_available():
            return {"built": False, "reason": "faiss_unavailable"}
        path = path or self.ann_index_path()
        if not path:
            return {"built": False, "reason": "no_path"}
        rows = self.storage.iter_embeddings(self.model, self.version)
        if not rows:
            return {"built": False, "reason": "empty", "chunks": 0}
        matrix, keys = self._rows_to_vectors(rows)
        self.dim = int(matrix.shape[1])
        index = ann_index.AnnIndex(self.dim, m=m,
                                   ef_construction=ef_construction,
                                   ef_search=ef_search)
        index.add(matrix, keys)
        index.meta = self._ann_meta(len(rows))
        index.save(path, meta=index.meta)
        ann_index._CACHE.pop(path, None)
        return {"built": True, "path": path, "chunks": len(rows),
                "dim": self.dim, "model": self.model, "version": self.version,
                "bytes": os.path.getsize(path)}

    def sync_ann(self, path: Optional[str] = None, **kwargs) -> dict:
        """Incrementally append new chunks, else rebuild; returns a report."""
        try:
            from core.legal import ann_index  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            return {"synced": False, "reason": f"import_failed: {exc}"}
        if not ann_index.faiss_available():
            return {"synced": False, "reason": "faiss_unavailable"}
        path = path or self.ann_index_path()
        if not path or not os.path.exists(path):
            report = self.build_ann(path=path, **kwargs)
            report["action"] = "rebuild"
            report["synced"] = report.get("built", False)
            return report
        rows = self.storage.iter_embeddings(self.model, self.version)
        index = ann_index.load_cached(path)
        if index is not None and index.meta.get("model") in (None, self.model) \
                and index.meta.get("version") in (None, self.version) \
                and 0 < index.n <= len(rows):
            new_keys = [(r["document_id"], r["chunk_index"], r["char_start"],
                         r["char_end"]) for r in rows]
            if index.n == len(rows) and list(index.keys) == new_keys:
                return {"synced": True, "action": "noop", "chunks": len(rows)}
            if list(index.keys[:index.n]) == new_keys[:index.n]:
                matrix, keys = self._rows_to_vectors(rows)
                self.dim = int(matrix.shape[1])
                index.add(matrix[index.n:], keys[index.n:])
                index.meta = self._ann_meta(len(rows))
                index.save(path, meta=index.meta)
                ann_index._CACHE.pop(path, None)
                return {"synced": True, "action": "incremental",
                        "added": len(rows) - index.n, "chunks": len(rows)}
        report = self.build_ann(path=path, **kwargs)
        report["action"] = "rebuild"
        report["synced"] = report.get("built", False)
        return report

    def search(self, query: str, limit: int = 10) -> list[dict]:
        """Return the ``limit`` chunks most cosine-similar to ``query``.

        Uses the optional persisted ANN index when it is available and fresh;
        otherwise the exact brute-force scan. Both paths return the same shape;
        ANN scores are true cosines (L2-normalized inner product).
        """
        query_vec = self.client.embed(query)
        index = self._load_ann()
        if index is not None:
            try:
                return self._ann_search(index, query_vec, limit)
            except Exception:  # noqa: BLE001 - fall back to exact scan
                pass
        rows = self.storage.iter_embeddings(self.model, self.version)
        if not rows:
            return []
        import numpy as np  # noqa: PLC0415 - local import; numpy is a hard dep here
        dim = len(rows[0]["vector"]) // 4
        if dim and all(len(r["vector"]) == dim * 4 for r in rows):
            # Zero-copy: concatenate the fixed-width float32 BLOBs and view.
            buf = b"".join(bytes(r["vector"]) for r in rows)
            matrix = np.frombuffer(buf, dtype=np.float32).reshape(len(rows), dim)
        else:
            matrix = np.asarray([unpack_vector(r["vector"]) for r in rows],
                                dtype=np.float32)
        q = np.asarray(query_vec, dtype=np.float32)
        qn = float(np.linalg.norm(q))
        if qn == 0.0 or matrix.shape[1] != q.shape[0]:
            return []
        norms = np.linalg.norm(matrix, axis=1)
        norms[norms == 0.0] = 1.0
        scores = (matrix @ q) / (norms * qn)
        order = np.argsort(-scores)[:limit]
        hits = []
        for i in order:
            row = rows[int(i)]
            doc = self.storage.get_document(row["document_id"]) or {}
            content = doc.get("content") or ""
            hits.append({
                "document_id": row["document_id"],
                "title": row["title"],
                "citation": row["citation"],
                "type": row["type"],
                "year": row["year"],
                "chunk_index": row["chunk_index"],
                "char_start": row["char_start"],
                "char_end": row["char_end"],
                "score": float(scores[int(i)]),
                "snippet": content[row["char_start"]:row["char_end"]],
            })
        return hits

    def stats(self) -> dict:
        return self.storage.embedding_stats(self.model, self.version)
