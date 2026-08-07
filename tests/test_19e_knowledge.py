"""Tests for Phase 19E: Legal Knowledge Engine."""

import os
import sys
import tempfile
import json
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestKnowledgeEngine:
    """Legal knowledge graph engine."""

    @pytest.fixture(autouse=True)
    def setup(self):
        import core.legal_brain.permanent as perm
        from core.legal_brain.permanent.store import add_source, insert_document, compute_hash

        self.test_dir = Path(tempfile.mkdtemp())
        self.db_path = self.test_dir / "test_kg.db"
        self.storage_dir = self.test_dir / "documents"
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        self._orig_get_db_path = perm.get_db_path
        perm.get_db_path = lambda: self.db_path

        perm.init_permanent_store(self.db_path)

        # Create test documents with citation relationships
        sid = add_source("https://parliament.gh", "parliament.gh", 1)

        # Constitution (root authority)
        self.const_id = insert_document(
            source_id=sid, title="Constitution of Ghana 1992",
            content_hash=compute_hash(b"constitution"),
            file_path=str(self.storage_dir / "const.txt"),
            category="Constitutional Law",
            copyright_classification="official_public_access",
            citation_text="[1992] GH Const.",
            court="Supreme Court", year=1992,
            review_status="approved",
        )

        # Criminal Code (cites Constitution)
        self.crim_id = insert_document(
            source_id=sid, title="Criminal Code 1960 (Act 29)",
            content_hash=compute_hash(b"criminal code"),
            file_path=str(self.storage_dir / "criminal.txt"),
            category="Legislation",
            copyright_classification="official_public_access",
            citation_text="Act 29, 1960",
            year=1960,
            review_status="approved",
        )

        # Case law (cites both Constitution and Criminal Code)
        self.case_id = insert_document(
            source_id=sid, title="Republic v Mensah [2020] GHSC 15",
            content_hash=compute_hash(b"case law"),
            file_path=str(self.storage_dir / "case.txt"),
            category="Judiciary",
            copyright_classification="official_public_access",
            citation_text="[2020] GHSC 15",
            court="Supreme Court of Ghana", year=2020,
            review_status="approved",
        )

        # Create citations in permanent store
        from core.legal_brain.permanent.store import insert_citation
        insert_citation(self.crim_id, self.const_id, citation_type="applies",
                        context_snippet="Pursuant to Article 12")
        insert_citation(self.case_id, self.const_id, citation_type="applies",
                        context_snippet="Per Article 14")
        insert_citation(self.case_id, self.crim_id, citation_type="applies",
                        context_snippet="As defined in Act 29 s.1")

        yield

        perm.get_db_path = self._orig_get_db_path
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_init_knowledge_store(self):
        """Knowledge graph tables are created."""
        from core.legal_brain.knowledge import init_knowledge_store

        engine = init_knowledge_store(self.db_path)
        assert engine is not None

        # Verify tables exist
        import sqlite3
        conn = sqlite3.connect(str(self.db_path))
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        conn.close()

        assert "kg_entities" in tables
        assert "kg_relationships" in tables
        assert "kg_transitive_closure" in tables

    def test_extract_entities(self):
        """Entities are extracted from document metadata."""
        from core.legal_brain.knowledge import KnowledgeEngine

        engine = KnowledgeEngine(self.db_path)
        entities = engine.extract_entities_from_document(self.const_id)

        # Should have constitutional law as statute entity
        assert "statute" in entities
        titles = [e["name"] for e in entities["statute"]]
        assert any("Constitution" in t for t in titles)

    def test_register_entity(self):
        """Entity registration works."""
        from core.legal_brain.knowledge import KnowledgeEngine

        engine = KnowledgeEngine(self.db_path)
        eid = engine.register_entity(
            entity_type="court",
            name="Supreme Court of Ghana",
            canonical_name="supreme court of ghana",
        )
        assert eid is not None

        entity = engine.get_entity(eid)
        assert entity["name"] == "Supreme Court of Ghana"
        assert entity["entity_type"] == "court"

    def test_register_duplicate_entity(self):
        """Duplicate entity registration returns None."""
        from core.legal_brain.knowledge import KnowledgeEngine

        engine = KnowledgeEngine(self.db_path)
        eid1 = engine.register_entity("court", "High Court", "high court")
        assert eid1 is not None

        eid2 = engine.register_entity("court", "High Court", "high court")
        assert eid2 is None  # Duplicate

    def test_find_entity(self):
        """Entity lookup by name works case-insensitively."""
        from core.legal_brain.knowledge import KnowledgeEngine

        engine = KnowledgeEngine(self.db_path)
        engine.register_entity("court", "Supreme Court", "supreme court")

        found = engine.find_entity("Supreme Court")
        assert found is not None
        assert found["entity_type"] == "court"

        # Case insensitive
        found2 = engine.find_entity("SUPREME COURT")
        assert found2 is not None

    def test_list_entities(self):
        """Entity listing works with filtering."""
        from core.legal_brain.knowledge import KnowledgeEngine

        engine = KnowledgeEngine(self.db_path)
        engine.register_entity("court", "Court A", "court a")
        engine.register_entity("court", "Court B", "court b")
        engine.register_entity("statute", "Act X", "act x")

        courts = engine.list_entities(entity_type="court")
        assert len(courts) == 2

        all_ents = engine.list_entities()
        assert len(all_ents) == 3

    def test_build_citation_graph(self):
        """Citation graph is built from permanent store citations."""
        from core.legal_brain.knowledge import KnowledgeEngine

        engine = KnowledgeEngine(self.db_path)
        stats = engine.build_citation_graph_from_permanent()

        assert stats["citations_processed"] == 3
        assert stats["relationships_created"] > 0

        # Verify KG stats
        kg_stats = engine.get_knowledge_graph_stats()
        assert kg_stats["entities"] > 0
        assert kg_stats["relationships"] > 0

    def test_relationships(self):
        """Relationship queries work."""
        from core.legal_brain.knowledge import KnowledgeEngine

        engine = KnowledgeEngine(self.db_path)
        engine.build_citation_graph_from_permanent()

        # Find the Constitution entity
        const_entity = engine.find_entity("Constitution of Ghana 1992")
        assert const_entity is not None

        # Outgoing from Constitution (it's cited BY others)
        incoming = engine.get_relationships(const_entity["id"], direction="incoming")
        assert len(incoming) > 0

    def test_transitive_closure(self):
        """Transitive closure shows indirect dependencies."""
        from core.legal_brain.knowledge import KnowledgeEngine

        engine = KnowledgeEngine(self.db_path)
        engine.build_citation_graph_from_permanent()

        # The case cites Crim Code, which cites Constitution
        # So case transitively depends on Constitution
        case_entity = engine.find_entity("Republic v Mensah [2020] GHSC 15")
        assert case_entity is not None

        deps = engine.get_transitive_dependencies(case_entity["id"])
        assert len(deps) >= 2  # Direct + transitive

        # Should have Constitution as a dependency (through Crim Code)
        dep_names = [d["name"] for d in deps]
        assert any("Constitution" in n for n in dep_names)

    def test_transitive_dependents(self):
        """Finding what depends on an authority works."""
        from core.legal_brain.knowledge import KnowledgeEngine

        engine = KnowledgeEngine(self.db_path)
        engine.build_citation_graph_from_permanent()

        const_entity = engine.find_entity("Constitution of Ghana 1992")
        assert const_entity is not None

        dependents = engine.get_transitive_dependents(const_entity["id"])
        assert len(dependents) >= 2  # Criminal Code + Case

    def test_ghana_only_scope(self):
        """Knowledge graph respects Ghana-only jurisdiction."""
        from core.legal_brain.knowledge import KnowledgeEngine

        engine = KnowledgeEngine(self.db_path)

        # All entities should have Ghana jurisdiction
        eid = engine.register_entity("court", "Ghana Court", "ghana court")
        entity = engine.get_entity(eid)
        assert entity["jurisdiction"] == "Ghana"

    def test_knowledge_graph_stats(self):
        """Stats aggregation works."""
        from core.legal_brain.knowledge import KnowledgeEngine

        engine = KnowledgeEngine(self.db_path)
        engine.build_citation_graph_from_permanent()

        stats = engine.get_knowledge_graph_stats()
        assert stats["entities"] > 0
        assert stats["relationships"] > 0
        assert "entity_types" in stats
