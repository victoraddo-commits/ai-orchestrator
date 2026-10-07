"""Knowledge Fabric — store (SQLite backend for the fabric).

Implements the model from ``schema.sql`` (spaces, members, documents, versions,
ACL, chunks, audit) with **permission-aware retrieval**: every list/search result
is filtered through ``core.knowledge.authz`` BEFORE it is returned, so
unauthorized documents/chunks never reach a caller (or the model).

This is the local/library backend (stdlib SQLite). Production may use the
Postgres/pgvector schema in ``schema.sql`` with the same semantics.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import time
import uuid

from core.knowledge.authz import can_read, readable_document_ids
from core.knowledge.model import DocumentACL, KnowledgeTier, Principal, Visibility


def _tokens(s: str) -> list:
    return re.findall(r"[a-z0-9]+", (s or "").lower())


def _tf(tokens: list) -> dict:
    d = {}
    for t in tokens:
        d[t] = d.get(t, 0) + 1
    return d


def _cosine(a: dict, b: dict) -> float:
    if not a or not b:
        return 0.0
    dot = sum(a.get(k, 0) * v for k, v in b.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0

_DDL = """
CREATE TABLE IF NOT EXISTS kf_spaces(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT, type TEXT,
  owner_user_id TEXT, knowledge_tier TEXT NOT NULL DEFAULT 'SECONDARY',
  visibility TEXT NOT NULL DEFAULT 'PRIVATE', created_at REAL);
CREATE TABLE IF NOT EXISTS kf_space_members(
  space_id TEXT NOT NULL, user_id TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'READER',
  PRIMARY KEY(space_id, user_id));
CREATE TABLE IF NOT EXISTS kf_documents(
  id TEXT PRIMARY KEY, space_id TEXT, owner_user_id TEXT NOT NULL, owner_group_id TEXT,
  knowledge_tier TEXT NOT NULL DEFAULT 'SECONDARY', title TEXT, mime_type TEXT,
  visibility TEXT NOT NULL DEFAULT 'PRIVATE', current_version INTEGER NOT NULL DEFAULT 1,
  created_at REAL, updated_at REAL);
CREATE TABLE IF NOT EXISTS kf_versions(
  id TEXT PRIMARY KEY, document_id TEXT NOT NULL, version INTEGER NOT NULL,
  content TEXT, created_by TEXT, created_at REAL, UNIQUE(document_id, version));
CREATE TABLE IF NOT EXISTS kf_acl(
  document_id TEXT NOT NULL, principal_type TEXT NOT NULL, principal_id TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'READER',
  PRIMARY KEY(document_id, principal_type, principal_id, role));
CREATE TABLE IF NOT EXISTS kf_chunks(
  id INTEGER PRIMARY KEY AUTOINCREMENT, document_id TEXT NOT NULL, version INTEGER,
  chunk_index INTEGER, content TEXT, knowledge_tier TEXT, space_id TEXT,
  owner_user_id TEXT, visibility TEXT);
CREATE TABLE IF NOT EXISTS kf_audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, actor TEXT, action TEXT,
  document_id TEXT, space_id TEXT, detail TEXT);
"""


class KnowledgeStore:
    def __init__(self, path: str = ":memory:"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_DDL)

    # -- audit ---------------------------------------------------------------
    def _audit(self, actor, action, document_id=None, space_id=None, detail=None):
        self.db.execute("INSERT INTO kf_audit(ts,actor,action,document_id,space_id,detail) "
                        "VALUES (?,?,?,?,?,?)",
                        (time.time(), actor, action, document_id, space_id,
                         json.dumps(detail) if detail is not None else None))
        self.db.commit()

    # -- spaces --------------------------------------------------------------
    def create_space(self, name, owner_user_id, *, type="shared",
                     tier=KnowledgeTier.SECONDARY, visibility=Visibility.PRIVATE,
                     description=None) -> str:
        sid = uuid.uuid4().hex[:12]
        self.db.execute("INSERT INTO kf_spaces(id,name,description,type,owner_user_id,"
                        "knowledge_tier,visibility,created_at) VALUES (?,?,?,?,?,?,?,?)",
                        (sid, name, description, type, owner_user_id, tier.value,
                         visibility.value, time.time()))
        self.db.execute("INSERT OR REPLACE INTO kf_space_members(space_id,user_id,role) "
                        "VALUES (?,?,?)", (sid, owner_user_id, "MANAGER"))
        self.db.commit()
        self._audit(owner_user_id, "space.create", space_id=sid, detail={"name": name})
        return sid

    def add_member(self, space_id, user_id, role="READER", actor="system"):
        self.db.execute("INSERT OR REPLACE INTO kf_space_members(space_id,user_id,role) "
                        "VALUES (?,?,?)", (space_id, user_id, role))
        self.db.commit()
        self._audit(actor, "space.member", space_id=space_id, detail={"user": user_id, "role": role})

    def space_ids_for(self, user_id: str) -> set:
        rows = self.db.execute("SELECT space_id FROM kf_space_members WHERE user_id=?", (user_id,))
        return {r["space_id"] for r in rows}

    # -- documents -----------------------------------------------------------
    def create_document(self, owner_user_id, title, content, *, space_id=None,
                        visibility=Visibility.PRIVATE, tier=KnowledgeTier.SECONDARY,
                        mime_type="text/plain") -> str:
        did = uuid.uuid4().hex[:12]
        now = time.time()
        self.db.execute("INSERT INTO kf_documents(id,space_id,owner_user_id,knowledge_tier,"
                        "title,mime_type,visibility,current_version,created_at,updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (did, space_id, owner_user_id, tier.value, title, mime_type,
                         visibility.value, 1, now, now))
        self._write_version(did, 1, content, owner_user_id)
        self.db.commit()
        self._audit(owner_user_id, "document.create", document_id=did, space_id=space_id)
        return did

    def _write_version(self, did, version, content, actor):
        vid = uuid.uuid4().hex[:12]
        self.db.execute("INSERT INTO kf_versions(id,document_id,version,content,created_by,"
                        "created_at) VALUES (?,?,?,?,?,?)",
                        (vid, did, version, content, actor, time.time()))
        self._index_chunks(did, version, content)

    def _index_chunks(self, did, version, content):
        row = self.db.execute("SELECT space_id,owner_user_id,knowledge_tier,visibility "
                              "FROM kf_documents WHERE id=?", (did,)).fetchone()
        self.db.execute("DELETE FROM kf_chunks WHERE document_id=?", (did,))
        for i, para in enumerate(c for c in (content or "").split("\n\n") if c.strip()):
            self.db.execute("INSERT INTO kf_chunks(document_id,version,chunk_index,content,"
                            "knowledge_tier,space_id,owner_user_id,visibility) "
                            "VALUES (?,?,?,?,?,?,?,?)",
                            (did, version, i, para, row["knowledge_tier"],
                             row["space_id"], row["owner_user_id"], row["visibility"]))

    def add_version(self, did, content, actor) -> int:
        cur = self.db.execute("SELECT current_version FROM kf_documents WHERE id=?", (did,)).fetchone()
        if not cur:
            raise KeyError(did)
        nv = cur["current_version"] + 1
        self._write_version(did, nv, content, actor)
        self.db.execute("UPDATE kf_documents SET current_version=?, updated_at=? WHERE id=?",
                        (nv, time.time(), did))
        self.db.commit()
        self._audit(actor, "document.version", document_id=did, detail={"version": nv})
        return nv

    def grant(self, did, principal_type, principal_id, role="READER", actor="system"):
        self.db.execute("INSERT OR REPLACE INTO kf_acl(document_id,principal_type,"
                        "principal_id,role) VALUES (?,?,?,?)",
                        (did, principal_type, principal_id, role))
        self.db.commit()
        self._audit(actor, "acl.grant", document_id=did,
                    detail={"type": principal_type, "id": principal_id, "role": role})

    def revoke(self, did, principal_type, principal_id, role="READER", actor="system"):
        self.db.execute("DELETE FROM kf_acl WHERE document_id=? AND principal_type=? "
                        "AND principal_id=? AND role=?",
                        (did, principal_type, principal_id, role))
        self.db.commit()
        self._audit(actor, "acl.revoke", document_id=did,
                    detail={"type": principal_type, "id": principal_id, "role": role})

    # -- authorization -------------------------------------------------------
    def _acl(self, doc_row) -> DocumentACL:
        did = doc_row["id"]
        acl = DocumentACL(document_id=did, owner_user_id=doc_row["owner_user_id"],
                          tier=KnowledgeTier(doc_row["knowledge_tier"]),
                          visibility=Visibility(doc_row["visibility"]),
                          space_id=doc_row["space_id"])
        for r in self.db.execute("SELECT principal_type,principal_id,role FROM kf_acl "
                                 "WHERE document_id=?", (did,)):
            if r["principal_type"] == "user":
                {"READER": acl.readers, "EDITOR": acl.editors,
                 "MANAGER": acl.managers}.get(r["role"], acl.readers).add(r["principal_id"])
            else:
                if r["role"] == "READER":
                    acl.group_readers.add(r["principal_id"])
        return acl

    def list_readable(self, principal: Principal) -> list:
        rows = self.db.execute("SELECT * FROM kf_documents").fetchall()
        out = []
        for row in rows:
            if can_read(principal, self._acl(row)):
                out.append({"id": row["id"], "title": row["title"],
                            "visibility": row["visibility"], "tier": row["knowledge_tier"],
                            "space_id": row["space_id"], "version": row["current_version"]})
        return out

    def search(self, principal: Principal, query: str) -> list:
        """Authorization-filtered keyword search over chunks."""
        readable = readable_document_ids(principal, [self._acl(r) for r in
                                                     self.db.execute("SELECT * FROM kf_documents")])
        if not readable:
            return []
        q = (query or "").lower()
        out = []
        rows = self.db.execute("SELECT * FROM kf_chunks").fetchall()
        for c in rows:
            if c["document_id"] not in readable:
                continue
            if q and q not in (c["content"] or "").lower():
                continue
            out.append({"document_id": c["document_id"], "chunk": c["content"]})
        return out

    # -- hybrid retrieval + unified RAG query (§16/§25/§48) ------------------
    def _readable_docs(self, principal: Principal) -> dict:
        docs = {}
        for r in self.db.execute("SELECT * FROM kf_documents"):
            if can_read(principal, self._acl(r)):
                docs[r["id"]] = {"title": r["title"], "tier": r["knowledge_tier"],
                                 "space_id": r["space_id"], "version": r["current_version"]}
        return docs

    def hybrid_search(self, principal: Principal, query: str, limit: int = 10) -> list:
        """Authorization-filtered hybrid search: TF-IDF cosine + keyword boost.

        No external model required (stdlib); retrieval is filtered by
        authorization BEFORE ranking so unauthorized chunks never appear.
        """
        docs = self._readable_docs(principal)
        if not docs:
            return []
        qv = _tf(_tokens(query))
        scored = []
        for c in self.db.execute("SELECT * FROM kf_chunks"):
            if c["document_id"] not in docs:
                continue
            content = c["content"] or ""
            sim = _cosine(qv, _tf(_tokens(content)))
            kw = 1.0 if query and query.lower() in content.lower() else 0.0
            score = sim + kw
            if score > 0:
                d = docs[c["document_id"]]
                scored.append((score, {"document_id": c["document_id"], "chunk": content,
                                       "title": d["title"], "tier": d["tier"],
                                       "space_id": d["space_id"], "version": d["version"]}))
        scored.sort(key=lambda x: -x[0])
        return [s for _, s in scored[:limit]]

    def query(self, principal: Principal, q: str, limit: int = 10) -> dict:
        """Unified PRIMARY + authorized SECONDARY retrieval with authority ranking.

        PRIMARY remains authoritative; secondary is reference. Conflicts are
        surfaced, never silently resolved (§17/§49).
        """
        hits = self.hybrid_search(principal, q, limit)
        primary = [h for h in hits if h["tier"] == "PRIMARY"]
        secondary = [h for h in hits if h["tier"] != "PRIMARY"]
        return {
            "query": q,
            "primary": primary,
            "secondary": secondary,
            "authority": "PRIMARY > SECONDARY",
            "conflict": bool(primary and secondary),
            "note": ("Primary and secondary sources both matched; primary is "
                     "authoritative and secondary is not treated as superseding it."
                     if primary and secondary else None),
        }

