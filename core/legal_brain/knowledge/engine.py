"""Legal Knowledge Engine — entity extraction, citation graph, transitive queries.

Built on the permanent Legal Brain document store. Operates on:
  - sources: legal source registry
  - documents: permanent WORM documents
  - citations: document→document citation links
"""

import sqlite3
import json
import uuid
from pathlib import Path
from typing import Optional, Dict, Any, List, Set, Tuple
from collections import defaultdict

from ..permanent import get_connection as get_perm_connection
from ..config import KNOWLEDGE_DB_PATH, DEFAULT_JURISDICTION

# Knowledge graph specific schema (adds to permanent DB)
_KG_SCHEMA = """
CREATE TABLE IF NOT EXISTS kg_entities (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,         -- 'court', 'statute', 'judge', 'principle', 'case'
    name TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    aliases TEXT,                       -- JSON array of alternate names
    jurisdiction TEXT DEFAULT 'Ghana',
    metadata TEXT,                      -- JSON: year, court_type, etc.
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS kg_relationships (
    id TEXT PRIMARY KEY,
    source_entity_id TEXT REFERENCES kg_entities(id),
    target_entity_id TEXT REFERENCES kg_entities(id),
    relationship_type TEXT NOT NULL,    -- 'amends', 'overrules', 'applies', 'defines', 'cited_in'
    doc_id TEXT REFERENCES documents(id),
    confidence REAL DEFAULT 1.0,
    context TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS kg_transitive_closure (
    ancestor_id TEXT NOT NULL,
    descendant_id TEXT NOT NULL,
    depth INTEGER NOT NULL DEFAULT 1,
    path TEXT,                          -- JSON array of entity IDs in the path
    PRIMARY KEY (ancestor_id, descendant_id)
);

CREATE INDEX IF NOT EXISTS idx_kg_entities_type ON kg_entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_kg_entities_name ON kg_entities(canonical_name);
CREATE INDEX IF NOT EXISTS idx_kg_relationships_source ON kg_relationships(source_entity_id);
CREATE INDEX IF NOT EXISTS idx_kg_relationships_target ON kg_relationships(target_entity_id);
CREATE INDEX IF NOT EXISTS idx_kg_relationships_type ON kg_relationships(relationship_type);
CREATE INDEX IF NOT EXISTS idx_kg_tc_ancestor ON kg_transitive_closure(ancestor_id);
CREATE INDEX IF NOT EXISTS idx_kg_tc_descendant ON kg_transitive_closure(descendant_id);
"""

# Entity extraction patterns
_COURT_PATTERNS = [
    "supreme court", "high court", "court of appeal", "circuit court",
    "district court", "regional tribunal", "fast track high court",
    "supreme court of ghana", "court of appeal of ghana", "high court of ghana",
]

_RELATIONSHIP_MARKERS = {
    "overrules": ["overruled", "overrules", "no longer good law", "expressly overruled"],
    "amends": ["amended by", "amends", "as amended", "amendment to"],
    "applies": ["applied in", "applying", "pursuant to", "under section", "under article"],
    "defines": ["defined in", "defines", "as defined by"],
    "distinguishes": ["distinguished from", "distinguished", "not applicable to"],
    "follows": ["follows", "applied the reasoning of", "as held in"],
}


class KnowledgeEngine:
    """Legal knowledge graph engine built on the permanent document store."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path

    def init(self):
        """Initialize knowledge graph tables."""
        with get_perm_connection(self.db_path) as conn:
            conn.executescript(_KG_SCHEMA)

    # ── Entity Extraction ──────────────────────────────────────────────

    def extract_entities_from_document(
        self, document_id: str
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Extract legal entities from a document's metadata and citations.

        Returns dict of entity_type → list of entities found.
        """
        from ..permanent.store import get_document

        doc = get_document(document_id)
        if not doc:
            return {}

        entities: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

        # Courts from metadata
        if doc.get("court"):
            court_name = doc["court"]
            entities["court"].append({
                "entity_type": "court",
                "name": court_name,
                "canonical_name": court_name.lower(),
                "year": doc.get("year"),
            })

        # Document itself as a "case" or "statute" entity
        doc_entity_type = "statute" if doc.get("category") in ("Legislation", "Constitutional Law") else "case"
        title = doc["title"]
        entities[doc_entity_type].append({
            "entity_type": doc_entity_type,
            "name": title,
            "canonical_name": title.lower(),
            "category": doc.get("category"),
            "year": doc.get("year"),
            "citation": doc.get("citation_text"),
        })

        # Extract from citation text
        if doc.get("citation_text"):
            # Look for court names in citation
            citation_lower = doc["citation_text"].lower()
            for pattern in _COURT_PATTERNS:
                if pattern in citation_lower:
                    entities["court"].append({
                        "entity_type": "court",
                        "name": pattern.title(),
                        "canonical_name": pattern,
                        "year": doc.get("year"),
                    })

        return dict(entities)

    def extract_and_register_entities(self, document_id: str) -> int:
        """Extract entities from a document and register them in the KG.

        Returns count of new entities registered.
        """
        extracted = self.extract_entities_from_document(document_id)
        count = 0

        for entity_type, entity_list in extracted.items():
            for entity in entity_list:
                if self.register_entity(
                    entity_type=entity["entity_type"],
                    name=entity["name"],
                    canonical_name=entity["canonical_name"],
                    metadata=entity,
                ):
                    count += 1

        return count

    def register_entity(
        self,
        entity_type: str,
        name: str,
        canonical_name: str,
        metadata: Optional[Dict[str, Any]] = None,
        jurisdiction: str = DEFAULT_JURISDICTION,
    ) -> Optional[str]:
        """Register a legal entity. Returns entity ID or None if already exists."""
        self.init()

        with get_perm_connection(self.db_path) as conn:
            existing = conn.execute(
                "SELECT id FROM kg_entities WHERE canonical_name = ? AND entity_type = ?",
                (canonical_name.lower(), entity_type),
            ).fetchone()

            if existing:
                return None  # Already registered

            entity_id = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO kg_entities
                   (id, entity_type, name, canonical_name, jurisdiction, metadata)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (entity_id, entity_type, name, canonical_name.lower(),
                 jurisdiction, json.dumps(metadata) if metadata else None),
            )
        return entity_id

    def get_entity(self, entity_id: str) -> Optional[Dict[str, Any]]:
        self.init()
        with get_perm_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM kg_entities WHERE id = ?", (entity_id,)
            ).fetchone()
            return dict(row) if row else None

    def find_entity(
        self, name: str, entity_type: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Find an entity by name (case-insensitive)."""
        self.init()
        with get_perm_connection(self.db_path) as conn:
            if entity_type:
                row = conn.execute(
                    """SELECT * FROM kg_entities
                       WHERE canonical_name = ? AND entity_type = ?""",
                    (name.lower(), entity_type),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM kg_entities WHERE canonical_name = ?",
                    (name.lower(),),
                ).fetchone()
            return dict(row) if row else None

    def list_entities(
        self,
        entity_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        self.init()
        with get_perm_connection(self.db_path) as conn:
            if entity_type:
                rows = conn.execute(
                    "SELECT * FROM kg_entities WHERE entity_type = ? ORDER BY name LIMIT ?",
                    (entity_type, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM kg_entities ORDER BY entity_type, name LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    # ── Citation Graph ─────────────────────────────────────────────────

    def build_citation_graph_from_permanent(self) -> Dict[str, Any]:
        """Populate the knowledge graph from permanent store citations.

        Reads all citations from permanent store and creates KG relationships.
        """
        self.init()

        with get_perm_connection(self.db_path) as conn:
            # Get all citations from the permanent store
            citations = [dict(r) for r in conn.execute(
                "SELECT * FROM citations ORDER BY created_at"
            ).fetchall()]

        stats = {"citations_processed": 0, "relationships_created": 0, "entities_created": 0}

        for citation in citations:
            source_id = citation["source_doc_id"]
            target_id = citation["target_doc_id"]

            # Register source and target as entities
            entities_source = self.extract_and_register_entities(source_id)
            stats["entities_created"] += entities_source

            if target_id:
                entities_target = self.extract_and_register_entities(target_id)
                stats["entities_created"] += entities_target

            # Map document IDs to entity IDs
            source_entity = self._get_entity_for_document(source_id)
            target_entity = self._get_entity_for_document(target_id) if target_id else None

            if source_entity and target_entity:
                self.add_relationship(
                    source_entity["id"],
                    target_entity["id"],
                    citation["citation_type"],
                    doc_id=source_id,
                    context=citation.get("context_snippet"),
                    confidence=citation.get("confidence", 1.0),
                )
                stats["relationships_created"] += 1

            stats["citations_processed"] += 1

        # Build transitive closure
        if stats["relationships_created"] > 0:
            self._rebuild_transitive_closure()

        return stats

    def add_relationship(
        self,
        source_entity_id: str,
        target_entity_id: str,
        relationship_type: str,
        doc_id: Optional[str] = None,
        context: Optional[str] = None,
        confidence: float = 1.0,
    ) -> str:
        """Add a directed relationship between two entities."""
        self.init()
        rel_id = str(uuid.uuid4())
        with get_perm_connection(self.db_path) as conn:
            conn.execute(
                """INSERT INTO kg_relationships
                   (id, source_entity_id, target_entity_id, relationship_type,
                    doc_id, confidence, context)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (rel_id, source_entity_id, target_entity_id, relationship_type,
                 doc_id, confidence, context),
            )
        return rel_id

    def get_relationships(
        self,
        entity_id: str,
        direction: str = "outgoing",  # 'outgoing' or 'incoming'
        relationship_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get relationships for an entity."""
        self.init()

        if direction == "outgoing":
            col = "r.source_entity_id"
        else:
            col = "r.target_entity_id"

        clauses = [f"{col} = ?"]
        params: List[Any] = [entity_id]
        if relationship_type:
            clauses.append("r.relationship_type = ?")
            params.append(relationship_type)

        with get_perm_connection(self.db_path) as conn:
            rows = conn.execute(
                f"""SELECT r.*,
                    se.name as source_name, te.name as target_name
                    FROM kg_relationships r
                    JOIN kg_entities se ON r.source_entity_id = se.id
                    JOIN kg_entities te ON r.target_entity_id = te.id
                    WHERE {' AND '.join(clauses)}
                    ORDER BY r.created_at""",
                params,
            ).fetchall()
            return [dict(r) for r in rows]

    # ── Transitive Closure ─────────────────────────────────────────────

    def _rebuild_transitive_closure(self):
        """Rebuild the transitive closure of the knowledge graph.

        Uses Floyd-Warshall-like approach: for every pair of entities,
        compute if one is reachable from the other and at what depth.
        """
        self.init()

        # Clear existing
        with get_perm_connection(self.db_path) as conn:
            conn.execute("DELETE FROM kg_transitive_closure")

            # Get all direct relationships
            rels = conn.execute(
                "SELECT source_entity_id, target_entity_id FROM kg_relationships"
            ).fetchall()

        # Build adjacency
        edges: Dict[str, Set[str]] = defaultdict(set)
        all_entities: Set[str] = set()
        for rel in rels:
            edges[rel["source_entity_id"]].add(rel["target_entity_id"])
            all_entities.add(rel["source_entity_id"])
            all_entities.add(rel["target_entity_id"])

        # Direct relationships (depth=1)
        with get_perm_connection(self.db_path) as conn:
            for src, targets in edges.items():
                for tgt in targets:
                    conn.execute(
                        """INSERT OR IGNORE INTO kg_transitive_closure
                           (ancestor_id, descendant_id, depth, path)
                           VALUES (?, ?, 1, ?)""",
                        (src, tgt, json.dumps([src, tgt])),
                    )

        # Floyd-Warshall: compute transitive closure
        entities_list = list(all_entities)
        changed = True
        max_iterations = len(entities_list) ** 2
        iterations = 0

        while changed and iterations < max_iterations:
            changed = False
            iterations += 1

            with get_perm_connection(self.db_path) as conn:
                # Find new paths: A→B→C means A→C
                new_paths = conn.execute(
                    """SELECT DISTINCT tc1.ancestor_id, tc1.descendant_id as mid,
                            tc2.descendant_id as target, tc1.depth + tc2.depth as new_depth
                       FROM kg_transitive_closure tc1
                       JOIN kg_transitive_closure tc2 ON tc1.descendant_id = tc2.ancestor_id
                       LEFT JOIN kg_transitive_closure existing
                         ON existing.ancestor_id = tc1.ancestor_id
                        AND existing.descendant_id = tc2.descendant_id
                       WHERE existing.ancestor_id IS NULL
                       LIMIT 1000"""
                ).fetchall()

                for path in new_paths:
                    conn.execute(
                        """INSERT OR IGNORE INTO kg_transitive_closure
                           (ancestor_id, descendant_id, depth, path)
                           VALUES (?, ?, ?, ?)""",
                        (path["ancestor_id"], path["target"], path["new_depth"],
                         json.dumps([path["ancestor_id"], path["mid"], path["target"]])),
                    )
                    changed = True

    def get_transitive_dependencies(
        self, entity_id: str, max_depth: int = 10
    ) -> List[Dict[str, Any]]:
        """Get all entities transitively cited by this entity.

        "What does this case ultimately depend on?"
        """
        self.init()
        with get_perm_connection(self.db_path) as conn:
            rows = conn.execute(
                """SELECT tc.depth, tc.descendant_id, e.name, e.entity_type
                   FROM kg_transitive_closure tc
                   JOIN kg_entities e ON tc.descendant_id = e.id
                   WHERE tc.ancestor_id = ? AND tc.depth <= ?
                   ORDER BY tc.depth""",
                (entity_id, max_depth),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_transitive_dependents(
        self, entity_id: str, max_depth: int = 10
    ) -> List[Dict[str, Any]]:
        """Get all entities that transitively cite this entity.

        "What cases depend on this authority?"
        """
        self.init()
        with get_perm_connection(self.db_path) as conn:
            rows = conn.execute(
                """SELECT tc.depth, tc.ancestor_id, e.name, e.entity_type
                   FROM kg_transitive_closure tc
                   JOIN kg_entities e ON tc.ancestor_id = e.id
                   WHERE tc.descendant_id = ? AND tc.depth <= ?
                   ORDER BY tc.depth""",
                (entity_id, max_depth),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_knowledge_graph_stats(self) -> Dict[str, Any]:
        """Get statistics about the knowledge graph."""
        self.init()
        with get_perm_connection(self.db_path) as conn:
            entity_count = conn.execute(
                "SELECT COUNT(*) as c FROM kg_entities"
            ).fetchone()["c"]
            rel_count = conn.execute(
                "SELECT COUNT(*) as c FROM kg_relationships"
            ).fetchone()["c"]
            tc_count = conn.execute(
                "SELECT COUNT(*) as c FROM kg_transitive_closure"
            ).fetchone()["c"]

            # Breakdown by entity type
            entity_types = conn.execute(
                """SELECT entity_type, COUNT(*) as cnt
                   FROM kg_entities GROUP BY entity_type ORDER BY cnt DESC"""
            ).fetchall()

        return {
            "entities": entity_count,
            "relationships": rel_count,
            "transitive_closure_entries": tc_count,
            "entity_types": {r["entity_type"]: r["cnt"] for r in entity_types},
        }

    # ── Helpers ─────────────────────────────────────────────────────────

    def _get_entity_for_document(self, document_id: str) -> Optional[Dict[str, Any]]:
        """Find or create the KG entity corresponding to a document."""
        from ..permanent.store import get_document

        doc = get_document(document_id)
        if not doc:
            return None

        self.init()
        with get_perm_connection(self.db_path) as conn:
            # Try to find by canonical name
            canonical = doc["title"].lower()
            row = conn.execute(
                "SELECT * FROM kg_entities WHERE canonical_name = ?",
                (canonical,),
            ).fetchone()

            if row:
                return dict(row)

        # Register as entity
        entity_type = "statute" if doc.get("category") in ("Legislation", "Constitutional Law") else "case"
        entity_id = self.register_entity(
            entity_type=entity_type,
            name=doc["title"],
            canonical_name=canonical,
            metadata={
                "doc_id": document_id,
                "citation": doc.get("citation_text"),
                "year": doc.get("year"),
                "category": doc.get("category"),
            },
        )
        if entity_id:
            return self.get_entity(entity_id)

        return None


def init_knowledge_store(db_path: Optional[Path] = None):
    """Convenience function to initialize the knowledge graph tables."""
    engine = KnowledgeEngine(db_path)
    engine.init()
    return engine
