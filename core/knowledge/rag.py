"""Knowledge Fabric → RAG context for the live Kai chat (roadmap 27G).

Populates ``signals["knowledge_context"]`` (consumed by
``core.kai.conversation.build_chat_prompt``) from the permission-aware Knowledge
Fabric store, so Kai chat answers are grounded in authorized PRIMARY/SECONDARY
documents with citations.

Embeddings: :func:`embed` is a pluggable MODEL-BASED hook (OpenAI-compatible
embeddings endpoint via ``KAI_EMBEDDING_URL``/``KAI_EMBEDDING_KEY``/
``KAI_EMBEDDING_MODEL``). With no model configured it returns ``None`` and
retrieval uses the store's stdlib TF-IDF hybrid search — so grounding works
today and upgrades to vector search when an embedding model is available.
"""
from __future__ import annotations

import os

from core.knowledge.model import Principal
from core.knowledge.store import KnowledgeStore

_DEFAULT_DB = os.environ.get("KAI_KNOWLEDGE_DB",
                             "/opt/ai-orchestrator/memory/knowledge_fabric.db")
_STORE = None


def _store() -> KnowledgeStore:
    global _STORE
    if _STORE is None:
        _STORE = KnowledgeStore(_DEFAULT_DB)
    return _STORE


def embed(text: str):
    """Model-based embedding, or None when no embedding model is configured."""
    url = os.environ.get("KAI_EMBEDDING_URL", "")
    if not url or not text:
        return None
    try:
        import requests
        resp = requests.post(
            url,
            json={"model": os.environ.get("KAI_EMBEDDING_MODEL", "text-embedding-3-small"),
                  "input": text},
            headers={"Authorization": f"Bearer {os.environ.get('KAI_EMBEDDING_KEY', '')}"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]
    except Exception:  # noqa: BLE001 - never break retrieval on embedding failure
        return None


def build_context(query: str, principal: Principal | None = None,
                  store: KnowledgeStore | None = None, limit: int = 5) -> str | None:
    """Return a citation-ready context block, or None when nothing matches.

    Permission filtering happens inside the store BEFORE ranking, so
    unauthorized documents never reach the model.
    """
    if not query or not query.strip():
        return None
    try:
        st = store or _store()
    except Exception:  # noqa: BLE001 - no store reachable -> no grounding
        return None
    p = principal or Principal("operator", is_system_admin=True)
    try:
        result = st.query(p, query, limit=limit)
    except Exception:  # noqa: BLE001
        return None
    hits = (result.get("primary") or []) + (result.get("secondary") or [])
    if not hits:
        return None
    lines = []
    for h in hits:
        snippet = (h.get("chunk") or "").strip().replace("\n", " ")
        if len(snippet) > 400:
            snippet = snippet[:400] + "…"
        lines.append(f"- [{h.get('tier', 'SECONDARY')}] {h.get('title', 'untitled')}: {snippet}")
    header = ("Sources are permission-filtered; PRIMARY is authoritative and "
              "SECONDARY is reference. Cite when used.")
    return header + "\n" + "\n".join(lines)


def build_signals(query: str, signals: dict | None = None,
                  principal: Principal | None = None) -> dict:
    """Return ``signals`` with ``knowledge_context`` populated (best-effort)."""
    out = dict(signals or {})
    if out.get("knowledge_context"):
        return out
    ctx = build_context(query, principal=principal)
    if ctx:
        out["knowledge_context"] = ctx
    return out
