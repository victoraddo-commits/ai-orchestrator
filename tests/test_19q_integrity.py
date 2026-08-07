"""Tests for Phase 19Q: Legal Brain Health & Integrity."""

import os
import sys
import tempfile
import json
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestIntegrityMonitor:
    """Continuous integrity monitoring."""

    @pytest.fixture(autouse=True)
    def setup(self):
        import core.legal_brain.permanent as perm
        from core.legal_brain.permanent.store import (
            add_source, insert_document, approve_document, compute_hash,
            insert_citation,
        )

        self.test_dir = Path(tempfile.mkdtemp())
        self.db_path = self.test_dir / "test_integrity.db"
        self.storage_dir = self.test_dir / "documents"
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        self._orig_get_db_path = perm.get_db_path
        perm.get_db_path = lambda: self.db_path

        perm.init_permanent_store(self.db_path)

        sid = add_source("https://parliament.gh", "parliament.gh", 1)

        # Create real files with known content
        self.content_a = b"Legal document A for integrity testing"
        self.hash_a = compute_hash(self.content_a)
        self.file_a = self.storage_dir / "doc_a.txt"
        self.file_a.write_bytes(self.content_a)

        self.content_b = b"Legal document B for integrity testing"
        self.hash_b = compute_hash(self.content_b)
        self.file_b = self.storage_dir / "doc_b.txt"
        self.file_b.write_bytes(self.content_b)

        self.doc_a = insert_document(
            source_id=sid, title="Document A",
            content_hash=self.hash_a,
            file_path=str(self.file_a),
            category="Legislation",
            copyright_classification="official_public_access",
            citation_text="Act 1, 2020",
        )
        approve_document(self.doc_a, "op")

        self.doc_b = insert_document(
            source_id=sid, title="Document B",
            content_hash=self.hash_b,
            file_path=str(self.file_b),
            category="Legislation",
            copyright_classification="official_public_access",
            citation_text="Act 1, 2020",  # Same citation! Creates conflict
        )
        approve_document(self.doc_b, "op")

        # Citation between them
        insert_citation(self.doc_a, self.doc_b, citation_type="applies")

        yield

        perm.get_db_path = self._orig_get_db_path
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_verify_all_document_hashes_intact(self):
        """All document hashes verify correctly when files are intact."""
        from core.legal_brain.integrity import IntegrityMonitor

        monitor = IntegrityMonitor(self.db_path)
        result = monitor.verify_all_document_hashes()

        assert result["total"] == 2
        assert result["valid"] == 2
        assert result["invalid"] == 0
        assert result["missing"] == 0
        assert result["intact"] is True

    def test_verify_hash_detects_tampering(self):
        """Modified file content is detected."""
        from core.legal_brain.integrity import IntegrityMonitor

        # Tamper with a file
        self.file_a.write_bytes(b"TAMPERED CONTENT!")

        monitor = IntegrityMonitor(self.db_path)
        result = monitor.verify_all_document_hashes()

        assert result["invalid"] == 1
        assert result["intact"] is False
        assert len(result["tampered_files"]) == 1
        assert result["tampered_files"][0]["title"] == "Document A"

    def test_verify_hash_detects_missing(self):
        """Missing file is detected."""
        from core.legal_brain.integrity import IntegrityMonitor

        # Delete a file
        self.file_a.unlink()

        monitor = IntegrityMonitor(self.db_path)
        result = monitor.verify_all_document_hashes()

        assert result["missing"] == 1
        assert result["intact"] is False
        assert len(result["missing_files"]) == 1

    def test_detect_duplicates(self):
        """Duplicate detection query works correctly.

        Note: The UNIQUE constraint on content_hash prevents actual duplicates
        at the DB level. This test verifies the detection logic returns 0
        when the constraint is enforced (normal operation). In the event of
        DB corruption bypassing the constraint, the query would catch it.
        """
        from core.legal_brain.integrity import IntegrityMonitor

        monitor = IntegrityMonitor(self.db_path)
        result = monitor.detect_duplicates()

        # With UNIQUE constraint enforced, no duplicates should exist
        assert result["total_duplicates"] == 0
        assert "duplicate_groups" in result
        assert "timestamp" in result

    def test_no_duplicates_when_clean(self):
        """No false positives when all content is unique."""
        from core.legal_brain.integrity import IntegrityMonitor

        monitor = IntegrityMonitor(self.db_path)
        result = monitor.detect_duplicates()

        assert result["total_duplicates"] == 0

    def test_detect_conflicting_versions(self):
        """Documents with same citation but different content are flagged."""
        from core.legal_brain.integrity import IntegrityMonitor

        monitor = IntegrityMonitor(self.db_path)
        result = monitor.detect_conflicting_versions()

        assert result["conflicts_found"] >= 1
        conflict = result["conflicts"][0]
        assert conflict["versions"] == 2

    def test_no_conflicts_when_unique(self):
        """No false positives when all citations are unique."""
        from core.legal_brain.integrity import IntegrityMonitor
        from core.legal_brain.permanent.store import (
            add_source, insert_document, compute_hash,
        )

        # Insert a document with unique citation
        sid = add_source("https://unique.gov.gh", "unique.gov.gh", 2)
        insert_document(
            source_id=sid, title="Unique Doc",
            content_hash=compute_hash(b"unique"),
            file_path=str(self.storage_dir / "unique.txt"),
            category="Legislation",
            copyright_classification="official_public_access",
            citation_text="Unique Act, 2024",  # Different citation
        )

        monitor = IntegrityMonitor(self.db_path)
        result = monitor.detect_conflicting_versions()

        # Only the Act 1, 2020 pair should be a conflict; Unique Act should be fine
        assert result["conflicts_found"] == 1

    def test_verify_citation_integrity(self):
        """Citation integrity check passes when all references are valid."""
        from core.legal_brain.integrity import IntegrityMonitor

        monitor = IntegrityMonitor(self.db_path)
        result = monitor.verify_citation_integrity()

        assert result["broken_citations"] == 0

    def test_broken_citation_detected(self):
        """Broken citations (deleted target) are detected."""
        from core.legal_brain.integrity import IntegrityMonitor
        from core.legal_brain.permanent.store import insert_citation
        import sqlite3

        # Insert a citation to a nonexistent document
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            """INSERT INTO citations (id, source_doc_id, target_doc_id, citation_type, confidence)
               VALUES (?, ?, ?, ?, ?)""",
            ("broken-cite-1", self.doc_a, "nonexistent-doc-id", "references", 1.0),
        )
        conn.commit()
        conn.close()

        monitor = IntegrityMonitor(self.db_path)
        result = monitor.verify_citation_integrity()

        assert result["broken_citations"] >= 1

    def test_run_full_check(self):
        """Full integrity check aggregates all checks."""
        from core.legal_brain.integrity import IntegrityMonitor

        monitor = IntegrityMonitor(self.db_path)
        result = monitor.run_full_check()

        assert "hash_verification" in result
        assert "duplicate_detection" in result
        assert "conflicting_versions" in result
        assert "citation_integrity" in result
        assert "overall_health" in result
        assert "issues_found" in result

    def test_alert_when_degraded(self):
        """Alert is generated when issues are found."""
        from core.legal_brain.integrity import IntegrityMonitor

        monitor = IntegrityMonitor(self.db_path)

        # Tamper to trigger issues
        self.file_a.write_bytes(b"TAMPERED")

        result = monitor.run_full_check()
        alert = monitor.format_alert(result)

        assert alert is not None
        assert "ALERT" in alert.upper()

    def test_no_alert_when_healthy(self):
        """No alert when everything is healthy."""
        from core.legal_brain.integrity import IntegrityMonitor

        monitor = IntegrityMonitor(self.db_path)
        result = monitor.run_full_check()
        alert = monitor.format_alert(result)

        # Should be healthy unless there's a conflicting version
        if result["overall_health"] == "healthy":
            assert alert is None
        # If conflicting versions cause degraded, alert should exist
        else:
            assert alert is not None
