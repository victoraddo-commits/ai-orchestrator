"""Tests for Phase 19L: Legal Trust Engine."""

import os
import sys
import tempfile
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestTrustEngine:
    """Legal trust scoring."""

    @pytest.fixture(autouse=True)
    def setup(self):
        import core.legal_brain.permanent as perm
        from core.legal_brain.permanent.store import (
            add_source, insert_document, approve_document, compute_hash,
        )

        self.test_dir = Path(tempfile.mkdtemp())
        self.db_path = self.test_dir / "test_trust.db"
        self.storage_dir = self.test_dir / "documents"
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        self._orig_get_db_path = perm.get_db_path
        perm.get_db_path = lambda: self.db_path

        perm.init_permanent_store(self.db_path)

        # Sources at different tiers
        self.t1_src = add_source("https://parliament.gh", "parliament.gh", 1)
        self.t2_src = add_source("https://ghalii.org", "ghalii.org", 2)
        self.t3_src = add_source("https://example.com", "example.com", 3)

        # Approved document from Tier 1
        self.approved_doc = insert_document(
            source_id=self.t1_src, title="Constitution",
            content_hash=compute_hash(b"constitution"),
            file_path=str(self.storage_dir / "const.txt"),
            category="Constitutional Law",
            copyright_classification="official_public_access",
            jurisdiction="Ghana",
        )
        approve_document(self.approved_doc, "op")

        # Pending document from Tier 2
        self.pending_doc = insert_document(
            source_id=self.t2_src, title="Pending Law",
            content_hash=compute_hash(b"pending"),
            file_path=str(self.storage_dir / "pending.txt"),
            category="Legislation",
            copyright_classification="open_license",
            jurisdiction="Ghana",
        )

        # Citation between them
        from core.legal_brain.permanent.store import insert_citation
        insert_citation(self.pending_doc, self.approved_doc, citation_type="applies")

        yield

        perm.get_db_path = self._orig_get_db_path
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_score_source_tier_1(self):
        """Tier 1 sources get the highest trust scores."""
        from core.legal_brain.trust import TrustEngine

        engine = TrustEngine(self.db_path)
        result = engine.score_source(self.t1_src)

        assert result["score"] >= 0.85
        assert result["tier"] == 1

    def test_score_source_tier_3(self):
        """Tier 3 sources get low trust scores."""
        from core.legal_brain.trust import TrustEngine

        engine = TrustEngine(self.db_path)
        result = engine.score_source(self.t3_src)

        assert result["score"] < 0.60
        assert result["tier"] == 3

    def test_score_source_not_found(self):
        """Missing source returns zero score."""
        from core.legal_brain.trust import TrustEngine

        engine = TrustEngine(self.db_path)
        result = engine.score_source("nonexistent")

        assert result["score"] == 0.0
        assert "not found" in result["reason"]

    def test_score_all_sources(self):
        """All sources are scored."""
        from core.legal_brain.trust import TrustEngine

        engine = TrustEngine(self.db_path)
        results = engine.score_all_sources()

        assert len(results) == 3
        # Tier 1 should score highest
        scores = [r["score"] for r in results]
        assert max(scores) >= 0.85

    def test_score_document_approved(self):
        """Approved documents from Tier 1 score high."""
        from core.legal_brain.trust import TrustEngine

        engine = TrustEngine(self.db_path)
        result = engine.score_document(self.approved_doc)

        assert result["score"] >= 0.75
        assert result["confidence_level"] in ("high", "medium")

    def test_score_document_pending(self):
        """Pending documents score lower than approved."""
        from core.legal_brain.trust import TrustEngine

        engine = TrustEngine(self.db_path)
        result = engine.score_document(self.pending_doc)

        # Pending document should score lower than approved
        approved_result = engine.score_document(self.approved_doc)
        assert result["score"] < approved_result["score"]
        assert result["review_status"] == "pending"

    def test_score_document_not_found(self):
        """Missing document returns zero."""
        from core.legal_brain.trust import TrustEngine

        engine = TrustEngine(self.db_path)
        result = engine.score_document("nonexistent")

        assert result["score"] == 0.0

    def test_verify_citation_valid(self):
        """Valid citations are verified."""
        from core.legal_brain.trust import TrustEngine

        engine = TrustEngine(self.db_path)
        result = engine.verify_citation(self.pending_doc, self.approved_doc)

        assert result["valid"] is True
        assert result["score"] > 0

    def test_verify_citation_invalid(self):
        """Invalid citation returns zero score."""
        from core.legal_brain.trust import TrustEngine

        engine = TrustEngine(self.db_path)
        result = engine.verify_citation("nonexistent1", "nonexistent2")

        assert result["valid"] is False
        assert result["score"] == 0.0

    def test_verify_citations_batch(self):
        """Multiple citations verified."""
        from core.legal_brain.trust import TrustEngine

        engine = TrustEngine(self.db_path)
        pairs = [
            (self.pending_doc, self.approved_doc),
            (self.pending_doc, "nonexistent"),
        ]
        results = engine.verify_citations(pairs)
        assert len(results) == 2
        assert results[0]["valid"] is True
        assert results[1]["valid"] is False

    def test_score_ai_response(self):
        """AI response scoring based on authorities."""
        from core.legal_brain.trust import TrustEngine

        engine = TrustEngine(self.db_path)

        authorities = [
            {"doc_id": self.approved_doc, "title": "Constitution", "similarity": 0.95},
        ]
        cited = [self.approved_doc]

        result = engine.score_ai_response(authorities, cited, model_confidence=0.90)

        assert result["score"] >= 0.7
        assert result["citation_coverage"] == 1.0
        assert result["citations_count"] == 1

    def test_score_ai_response_no_authorities(self):
        """Response with no authorities scores zero."""
        from core.legal_brain.trust import TrustEngine

        engine = TrustEngine(self.db_path)
        result = engine.score_ai_response([], [], model_confidence=0.5)

        assert result["score"] == 0.0
        assert result["flag_for_review"] is True

    def test_score_ai_response_low_coverage(self):
        """Low citation coverage flags response."""
        from core.legal_brain.trust import TrustEngine

        engine = TrustEngine(self.db_path)

        # Many authorities retrieved but none cited
        authorities = [
            {"doc_id": self.approved_doc, "title": "T1"},
            {"doc_id": self.approved_doc, "title": "T2"},
        ]
        cited: list = []  # Nothing cited

        result = engine.score_ai_response(authorities, cited)
        assert result["citation_coverage"] == 0.0
        assert result["flag_for_review"] is True

    def test_trust_summary(self):
        """Trust summary aggregates across the system."""
        from core.legal_brain.trust import TrustEngine

        engine = TrustEngine(self.db_path)
        summary = engine.get_trust_summary()

        assert summary["sources_scored"] == 3
        assert summary["source_breakdown"]["tier_1"] == 1
        assert summary["source_breakdown"]["tier_2"] == 1
        assert summary["source_breakdown"]["tier_3"] == 1

    def test_confidence_labels(self):
        """Confidence labels cover the full scoring range."""
        from core.legal_brain.trust import TrustEngine

        engine = TrustEngine(self.db_path)

        assert engine._confidence_label(0.95) == "high"
        assert engine._confidence_label(0.80) == "medium"
        assert engine._confidence_label(0.50) == "low"
        assert engine._confidence_label(0.10) == "very_low"
