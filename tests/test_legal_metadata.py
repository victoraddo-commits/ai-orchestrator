"""Tests for legal metadata + version control module (17O-C)."""

import json
import os
import tempfile
from pathlib import Path
from datetime import datetime, timezone

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from legal_metadata import (
    DocumentStatus,
    CourtLevel,
    LegislationType,
    GhanaLegalCitation,
    LegalMetadata,
    ImmutableStorage,
    create_metadata_template,
)


class TestDocumentStatus:
    """Verify the document status enumeration."""

    def test_required_statuses_exist(self):
        """All required status values must be present."""
        assert DocumentStatus.CURRENT.value == "current"
        assert DocumentStatus.OVERRULED.value == "overruled"
        assert DocumentStatus.AMENDED.value == "amended"
        assert DocumentStatus.REPEALED.value == "repealed"
        assert DocumentStatus.HISTORICAL.value == "historical"

    def test_status_values_are_strings(self):
        assert isinstance(DocumentStatus.CURRENT.value, str)


class TestCourtLevel:
    """Verify court hierarchy enumeration."""

    def test_supreme_court_is_highest(self):
        assert CourtLevel.SUPREME_COURT.value == "Supreme Court"

    def test_not_applicable_for_legislation(self):
        assert CourtLevel.NOT_APPLICABLE.value == "N/A"


class TestGhanaLegalCitation:
    """Verify citation format."""

    def test_citation_with_all_fields(self):
        citation = GhanaLegalCitation(
            citation_text="[2003-2004] SCGLR 1",
            neutral_citation="J1/1/2004",
            year=2004,
            volume="2003-2004",
            page=1,
        )
        assert citation.year == 2004
        assert citation.volume == "2003-2004"
        assert "SCGLR" in citation.citation_text

    def test_citation_minimal(self):
        citation = GhanaLegalCitation(citation_text="Act 651")
        assert citation.citation_text == "Act 651"
        assert citation.year is None


class TestLegalMetadata:
    """Verify the metadata schema."""

    def test_default_jurisdiction_is_ghana(self):
        meta = LegalMetadata(document_type="legislation")
        assert meta.jurisdiction == "Ghana"

    def test_metadata_to_dict_roundtrip(self):
        """Metadata should survive JSON serialization and deserialization."""
        original = LegalMetadata(
            document_type="case_law",
            court="Supreme Court",
            year=2020,
            judge="Arku JSC",
            plaintiff="Republic",
            defendant="Appellant",
            citation=GhanaLegalCitation(
                citation_text="[2020] SCGLR 45",
                year=2020,
                page=45,
            ),
            status=DocumentStatus.CURRENT,
            subject_areas=["Criminal Law", "Evidence"],
            keywords=["murder", "circumstantial evidence"],
            taxonomy_category="03",
            taxonomy_subcategory="Supreme Court",
            source_url="https://example.com/case.pdf",
        )

        serialized = original.to_dict()
        restored = LegalMetadata.from_dict(serialized)

        assert restored.jurisdiction == "Ghana"
        assert restored.court == "Supreme Court"
        assert restored.year == 2020
        assert restored.judge == "Arku JSC"
        assert restored.plaintiff == "Republic"
        assert restored.defendant == "Appellant"
        assert restored.citation.citation_text == "[2020] SCGLR 45"
        assert restored.status == DocumentStatus.CURRENT
        assert "Criminal Law" in restored.subject_areas
        assert restored.taxonomy_category == "03"

    def test_all_field_types_present(self):
        """17O-C requirement: metadata schema with all required fields:
        jurisdiction, court, year, citation, judge, parties, status."""
        meta = LegalMetadata(
            document_type="case_law",
            jurisdiction="Ghana",
            court="High Court",
            year=2018,
            citation=GhanaLegalCitation(citation_text="Suit No. HR/45/18"),
            judge="Mensah J",
            plaintiff="Kwame v",
            defendant="Akua",
            status=DocumentStatus.CURRENT,
        )

        # All 7 required fields
        assert meta.jurisdiction == "Ghana"
        assert meta.court == "High Court"
        assert meta.year == 2018
        assert meta.citation is not None
        assert meta.judge == "Mensah J"
        assert meta.plaintiff == "Kwame v" or meta.defendant == "Akua"
        assert meta.status == DocumentStatus.CURRENT

    def test_amendment_chain_fields(self):
        """Amendments should be trackable."""
        meta = LegalMetadata(
            document_type="legislation",
            amended_by=["Act 900"],
            amends=["Act 651"],
            repealed_by="Act 1050",
        )
        assert "Act 900" in meta.amended_by
        assert "Act 651" in meta.amends
        assert meta.repealed_by == "Act 1050"


class TestImmutableStorage:
    """Verify content-addressable immutable storage."""

    @pytest.fixture
    def storage(self):
        """Create an isolated storage instance."""
        with tempfile.TemporaryDirectory() as tmp:
            storage = ImmutableStorage(
                storage_dir=os.path.join(tmp, "storage"),
                db_path=os.path.join(tmp, "test.db"),
            )
            yield storage

    def test_store_and_retrieve_document(self, storage):
        """Basic store and retrieve cycle."""
        content = b"The Republic of Ghana Labour Act, 2003"
        metadata = LegalMetadata(
            document_type="legislation",
            year=2003,
            citation=GhanaLegalCitation(citation_text="Act 651"),
            subject_areas=["Labour Law"],
            taxonomy_category="02",
            taxonomy_subcategory="Acts of Parliament",
        )

        vhash, vnum = storage.store_document(
            "ACT-651", "Labour Act, 2003", content, metadata
        )

        assert vnum == 1
        assert len(vhash) == 64  # SHA-256

        # Retrieve
        doc = storage.get_document("ACT-651")
        assert doc is not None
        assert doc["title"] == "Labour Act, 2003"
        assert doc["content_bytes"] == content
        assert doc["version_number"] == 1
        assert doc["metadata"].citation.citation_text == "Act 651"

    def test_version_control_adds_versions(self, storage):
        """17O-C requirement: version control for legal documents."""
        content_v1 = b"Original text of the Act"
        meta_v1 = LegalMetadata(
            document_type="legislation",
            status=DocumentStatus.CURRENT,
            citation=GhanaLegalCitation(citation_text="Act 123"),
        )

        vhash1, vnum1 = storage.store_document("ACT-123", "Test Act", content_v1, meta_v1)
        assert vnum1 == 1

        # Add amendment
        content_v2 = b"Amended text of the Act with new section 5A"
        meta_v2 = LegalMetadata(
            document_type="legislation",
            status=DocumentStatus.AMENDED,
            citation=GhanaLegalCitation(citation_text="Act 123 (as amended)"),
            amended_by=["Act 456"],
        )

        vhash2, vnum2 = storage.store_document(
            "ACT-123", "Test Act (Amended)", content_v2, meta_v2,
            change_description="Added section 5A"
        )
        assert vnum2 == 2

        # Get latest version
        latest = storage.get_document("ACT-123")
        assert latest["version_number"] == 2
        assert latest["content_bytes"] == content_v2
        assert latest["metadata"].status == DocumentStatus.AMENDED

        # Get specific version
        v1 = storage.get_document("ACT-123", version=1)
        assert v1["version_number"] == 1
        assert v1["content_bytes"] == content_v1
        assert v1["metadata"].status == DocumentStatus.CURRENT

    def test_immutable_storage_no_overwrite(self, storage):
        """17O-C requirement: immutable storage. Content is never overwritten."""
        content_v1 = b"Version 1 content"
        vhash1, _ = storage.store_document(
            "IMMUTABLE-1", "Immutable Doc", content_v1,
            LegalMetadata(document_type="legislation", citation=GhanaLegalCitation(citation_text="Test"))
        )

        # Write v2
        content_v2 = b"Version 2 content"
        vhash2, _ = storage.store_document(
            "IMMUTABLE-1", "Immutable Doc v2", content_v2,
            LegalMetadata(document_type="legislation", citation=GhanaLegalCitation(citation_text="Test v2")),
            change_description="Updated content"
        )

        # Both versions still accessible
        v1 = storage.get_document("IMMUTABLE-1", version=1)
        v2 = storage.get_document("IMMUTABLE-1", version=2)

        assert v1["content_bytes"] == content_v1
        assert v2["content_bytes"] == content_v2
        assert v1["version_hash"] != v2["version_hash"]

    def test_audit_trail_records_all_actions(self, storage):
        """17O-C requirement: immutable storage with audit trail."""
        vhash, _ = storage.store_document(
            "AUDIT-1", "Auditable Document", b"Content",
            LegalMetadata(document_type="legislation", citation=GhanaLegalCitation(citation_text="Test"))
        )

        # Update metadata
        storage.update_metadata(
            "AUDIT-1",
            LegalMetadata(
                document_type="legislation",
                status=DocumentStatus.OVERRULED,
                citation=GhanaLegalCitation(citation_text="Test"),
                status_note="Overruled by Act 999",
            ),
            created_by="legal_editor",
            change_description="Changed status to overruled"
        )

        # Check audit trail
        trail = storage.get_audit_trail("AUDIT-1")
        assert len(trail) >= 2  # created + status_changed

        actions = [t["action"] for t in trail]
        assert "created" in actions
        assert "status_changed" in actions

        # Audit entries should have timestamps
        for entry in trail:
            assert entry["performed_at"] is not None
            assert entry["performed_by"] is not None

    def test_integrity_verification(self, storage):
        """Storage integrity can be verified."""
        content = b"Test content for integrity check"
        storage.store_document(
            "INTEG-1", "Integrity Test", content,
            LegalMetadata(document_type="legislation", citation=GhanaLegalCitation(citation_text="Test"))
        )

        report = storage.verify_integrity()
        assert report["issues_found"] == 0

    def test_integrity_detects_missing_content(self, storage):
        """Integrity check detects missing content."""
        content = b"Corruptible content"
        storage.store_document(
            "CORRUPT-1", "Corruption Test", content,
            LegalMetadata(document_type="legislation", citation=GhanaLegalCitation(citation_text="Test"))
        )

        # Delete content on disk to simulate corruption
        doc = storage.get_document("CORRUPT-1")
        content_path = storage.storage_dir / doc["content_path"]
        content_path.unlink()

        report = storage.verify_integrity("CORRUPT-1")
        assert report["issues_found"] > 0
        assert any("content_missing" in str(i["issue"]) for i in report["issues"])

    def test_list_documents_with_filters(self, storage):
        """Document listing with filter support."""
        # Store multiple docs
        for i, (doc_id, title, doc_type, court, year) in enumerate([
            ("ACT-1", "First Act", "legislation", None, 2020),
            ("CASE-1", "First Case", "case_law", "Supreme Court", 2020),
            ("CASE-2", "Second Case", "case_law", "High Court", 2019),
        ]):
            storage.store_document(
                doc_id, title, f"Content {i}".encode(),
                LegalMetadata(
                    document_type=doc_type,
                    court=court,
                    year=year,
                    citation=GhanaLegalCitation(citation_text=title),
                )
            )

        # Filter by type
        legislation = storage.list_documents(document_type="legislation")
        assert len(legislation) == 1
        assert legislation[0]["id"] == "ACT-1"

        # Filter by court
        supreme = storage.list_documents(court="Supreme Court")
        assert len(supreme) == 1
        assert supreme[0]["id"] == "CASE-1"

        # Filter by year
        docs_2020 = storage.list_documents(year=2020)
        assert len(docs_2020) == 2

    def test_content_deduplication(self, storage):
        """Identical content should be stored only once on disk (content-addressed).

        Version hashes differ (different documents) but content path is shared."""
        content = b"Identical document content"
        vhash1, _ = storage.store_document(
            "DOC-A", "Document A", content,
            LegalMetadata(document_type="legislation", citation=GhanaLegalCitation(citation_text="A"))
        )
        vhash2, _ = storage.store_document(
            "DOC-B", "Document B", content,  # Same content!
            LegalMetadata(document_type="legislation", citation=GhanaLegalCitation(citation_text="B"))
        )

        # Different docs → different version hashes
        assert vhash1 != vhash2
        # But content is stored only once (same content_path)
        doc_a = storage.get_document("DOC-A")
        doc_b = storage.get_document("DOC-B")
        # Content is the same
        assert doc_a["content_bytes"] == doc_b["content_bytes"]
        # Both content files share the same inode/disk location
        import os
        path_a = storage.storage_dir / doc_a["content_path"]
        path_b = storage.storage_dir / doc_b["content_path"]
        # Same content hash → stored at same relative path pattern
        assert path_a.name == path_b.name  # content hash filename is the same

    def test_version_history(self, storage):
        """Version history should list all versions."""
        for i, content in enumerate([b"v1", b"v2", b"v3"], start=1):
            storage.store_document(
                "VHIST-1", "Versioned Doc", content,
                LegalMetadata(document_type="legislation", citation=GhanaLegalCitation(citation_text=f"v{i}")),
                change_description=f"Version {i}"
            )

        history = storage.get_version_history("VHIST-1")
        assert len(history) == 3
        assert history[0]["version_number"] == 1
        assert history[1]["version_number"] == 2
        assert history[2]["version_number"] == 3


class TestMetadataTemplate:
    def test_legislation_template(self):
        meta = create_metadata_template("legislation")
        assert meta.document_type == "legislation"
        assert meta.taxonomy_category == "02"
        assert meta.status == DocumentStatus.CURRENT

    def test_case_law_template(self):
        meta = create_metadata_template("case_law")
        assert meta.document_type == "case_law"
        assert meta.taxonomy_category == "03"

    def test_constitution_template(self):
        meta = create_metadata_template("constitution")
        assert meta.document_type == "constitution"
        assert meta.taxonomy_category == "01"
        assert "Constitutional Law" in meta.subject_areas


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
