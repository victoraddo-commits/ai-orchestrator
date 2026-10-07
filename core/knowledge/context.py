"""Knowledge Fabric — grounded context builder (RAG, §16/§48).

Turns a permission-aware PRIMARY+SECONDARY query into a context preamble with
citations, for the owning module (Kai chat, Juris, …) to inject into a model
prompt. Unauthorized content never enters the context (enforced by the store).
"""
from __future__ import annotations


def build_context(store, principal, query: str, limit: int = 5) -> dict:
    q = store.query(principal, query, limit)
    lines = []
    citations = []
    if q["primary"]:
        lines.append("PRIMARY KAI KNOWLEDGE (authoritative):")
        for h in q["primary"]:
            lines.append(f"- [{h['title']}] {h['chunk']}")
    if q["secondary"]:
        lines.append("AUTHORIZED SECONDARY KNOWLEDGE (reference; does not "
                     "supersede primary):")
        for h in q["secondary"]:
            lines.append(f"- [{h['title']}] {h['chunk']}")
    if q["conflict"]:
        lines.append("NOTE: both primary and secondary matched; the primary "
                     "source remains authoritative.")
    for h in q["primary"] + q["secondary"]:
        citations.append({"document_id": h["document_id"], "title": h["title"],
                          "tier": h["tier"], "version": h["version"]})
    return {"context": "\n".join(lines), "citations": citations,
            "conflict": q["conflict"], "authority": q["authority"]}
