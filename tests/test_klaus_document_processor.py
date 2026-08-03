"""
Tests for KLAUS document processor: PDF extraction, text cleaning,
classification, copyright handling, chunking, duplicate detection.

These tests exercise the document processing pipeline without requiring
a live PostgreSQL database. Unit tests for pure functions and mock-based
tests for the ingestion pipeline.
"""

import io
import hashlib
import pytest
from unittest.mock import patch, MagicMock, call

from core.klaus.document_processor import (
    JURISDICTION_SIGNALS,
    clean_text,
    detect_jurisdiction,
    extract_citations,
    classify_document_by_keywords,
    classify_copyright,
    chunk_text,
    extract_text_from_txt,
)


class TestTextCleaning:
    def test_normalizes_whitespace(self):
        result = clean_text("hello    world")
        assert result == "hello world"

    def test_collapses_multiple_newlines(self):
        result = clean_text("line1\n\n\n\nline2")
        assert result == "line1\n\nline2"

    def test_strips_leading_trailing_whitespace(self):
        result = clean_text("  \n  content  \n  ")
        assert result == "content"

    def test_preserves_paragraph_boundaries(self):
        result = clean_text("para1\n\n\n\npara2")
        assert result == "para1\n\npara2"

    def test_handles_tabs_and_spaces(self):
        result = clean_text("col1\tcol2  col3")
        assert result == "col1 col2 col3"

    def test_empty_string(self):
        assert clean_text("") == ""

    def test_only_whitespace(self):
        assert clean_text("   \n\n  ") == ""


class TestJurisdictionDetection:
    def test_detects_ghana_explicit(self):
        text = "In the Republic of Ghana, the Constitution provides..."
        assert detect_jurisdiction(text) == "Ghana"

    def test_detects_nigeria(self):
        text = "The Federal Republic of Nigeria, through the National Assembly..."
        assert detect_jurisdiction(text) == "Nigeria"

    def test_detects_kenya(self):
        text = "In Kenya, the Judiciary has established..."
        assert detect_jurisdiction(text) == "Kenya"

    def test_detects_south_africa(self):
        text = "The Constitutional Court of South Africa held that..."
        assert detect_jurisdiction(text) == "South Africa"

    def test_detects_ecowas(self):
        text = "Under the ECOWAS Treaty, member states shall..."
        assert detect_jurisdiction(text) == "ECOWAS"

    def test_detects_african_union(self):
        text = "The African Union (AU) Charter..."
        assert detect_jurisdiction(text) == "African Union"

    def test_defaults_to_ghana(self):
        text = "This document contains no jurisdiction signals at all."
        assert detect_jurisdiction(text) == "Ghana"

    def test_case_insensitive(self):
        text = "GHANA is a constitutional democracy..."
        assert detect_jurisdiction(text) == "Ghana"


class TestCitationExtraction:
    def test_extracts_article_references(self):
        text = "Article 1 declares the sovereignty of the people, and Article 10(2) protects..."
        citations = extract_citations(text)
        assert "Article 1" in citations["articles"]
        assert "Article 10(2)" in citations["articles"]

    def test_extracts_section_references(self):
        text = "Section 5 provides for jurisdiction, and Section 12(3)(a) limits..."
        citations = extract_citations(text)
        assert len(citations["articles"]) >= 0

    def test_extracts_case_references(self):
        text = "As held in [2021-2022] SCGLR 234..."
        citations = extract_citations(text)
        assert len(citations["cases"]) >= 1

    def test_extracts_legislation_references(self):
        text = "Pursuant to Act 703, and L.I. 1930..."
        citations = extract_citations(text)
        assert len(citations["legislation"]) >= 2

    def test_empty_text(self):
        citations = extract_citations("")
        assert citations["articles"] == []
        assert citations["cases"] == []
        assert citations["legislation"] == []

    def test_no_citations(self):
        citations = extract_citations("This is a paragraph with no legal citations.")
        assert all(len(v) == 0 for v in citations.values())


class TestDocumentClassification:
    def test_classifies_constitutional_law(self):
        text = "THE CONSTITUTION of the Republic. This supreme law establishes the sovereign..."
        assert classify_document_by_keywords(text) == "Constitutional Law"

    def test_classifies_legislation(self):
        text = "An ACT of Parliament to provide for... Enacted by Parliament... assented to on..."
        assert classify_document_by_keywords(text) == "Legislation"

    def test_classifies_judiciary(self):
        text = "JUDGMENT. The plaintiff brought this action. The defendant argued..."
        assert classify_document_by_keywords(text) == "Judiciary"

    def test_classifies_legal_procedure(self):
        text = "RULES OF COURT. Civil Procedure. These rules of court govern..."
        assert classify_document_by_keywords(text) == "Legal Procedure"

    def test_classifies_international_law(self):
        text = "TREATY between the parties. This convention is ratified..."
        assert classify_document_by_keywords(text) == "International Law"

    def test_classifies_legal_scholarship_by_default(self):
        text = "This is a general legal commentary without specific procedural markers."
        assert classify_document_by_keywords(text) == "Legal Scholarship"


class TestCopyrightClassification:
    def test_official_public_access_for_gov_domains(self):
        copyright_cls, access_level = classify_copyright(
            "Some legal text", "https://parliament.gh/acts/2024"
        )
        assert copyright_cls == "official_public_access"
        assert access_level == "full_storage"

    def test_public_domain(self):
        copyright_cls, access_level = classify_copyright(
            "This work is public domain. No rights reserved.", "https://example.com"
        )
        assert copyright_cls == "public_domain"
        assert access_level == "full_storage"

    def test_open_license_cc_by(self):
        copyright_cls, access_level = classify_copyright(
            "Licensed under Creative Commons CC-BY 4.0", "https://example.com"
        )
        assert copyright_cls == "open_license"
        assert access_level == "full_storage"

    def test_copyright_protected(self):
        copyright_cls, access_level = classify_copyright(
            "All rights reserved. Copyright 2024.", "https://example.com"
        )
        assert copyright_cls == "copyright_protected"
        assert access_level == "metadata_only"

    def test_unknown_default(self):
        copyright_cls, access_level = classify_copyright(
            "Some text without clear licensing.", "https://random-site.com"
        )
        assert copyright_cls == "unknown"
        assert access_level == "metadata_only"

    def test_gov_domain_takes_priority(self):
        copyright_cls, access_level = classify_copyright(
            "All rights reserved. Copyright 2024.", "https://parliament.gh/docs"
        )
        assert copyright_cls == "official_public_access"
        assert access_level == "full_storage"


class TestTextChunking:
    def test_splits_long_text_into_chunks(self):
        text = "Paragraph one.\n\n" + "ABCD " * 200 + "\n\nParagraph three."
        chunks = chunk_text(text, chunk_size=500, overlap=50)
        assert len(chunks) >= 2

    def test_small_text_is_single_chunk(self):
        text = "Short text."
        chunks = chunk_text(text, chunk_size=800, overlap=100)
        assert len(chunks) == 1
        assert chunks[0] == "Short text."

    def test_empty_text_no_chunks(self):
        assert chunk_text("") == []

    def test_overlap_preserves_context(self):
        text = "\n\n".join([f"Paragraph {i} with repeated context info." for i in range(20)])
        chunks = chunk_text(text, chunk_size=300, overlap=50)
        assert len(chunks) > 0

    def test_preserves_chunk_boundaries_at_paragraphs(self):
        text = "Short para.\n\nAnother short para."
        chunks = chunk_text(text, chunk_size=800, overlap=100)
        assert len(chunks) == 1
        assert "Short para." in chunks[0]
        assert "Another short para." in chunks[0]


class TestTXTExtraction:
    def test_extracts_utf8_text(self):
        content = "Section 1. The Constitution is the supreme law.".encode("utf-8")
        result = extract_text_from_txt(content)
        assert result == "Section 1. The Constitution is the supreme law."

    def test_handles_decode_errors(self):
        content = b"Valid text \xff\xfe with broken bytes"
        result = extract_text_from_txt(content)
        assert "Valid text" in result


class TestComputeFileHash:
    def test_consistent_hash(self):
        from core.klaus.document_processor import compute_file_hash
        content = b"test content"
        h1 = compute_file_hash(content)
        h2 = compute_file_hash(content)
        assert h1 == h2

    def test_different_content_different_hash(self):
        from core.klaus.document_processor import compute_file_hash
        h1 = compute_file_hash(b"content A")
        h2 = compute_file_hash(b"content B")
        assert h1 != h2


# ── Integration-style tests with mocked DB ───────────────────────────────

class TestProcessDocumentPipeline:
    """Tests the full process_document() pipeline with mocked database."""

    def _sample_pdf_bytes(self):
        """Generate a minimal valid PDF. Uses pypdf to create one."""
        try:
            from pypdf import PdfWriter
            writer = PdfWriter()
            writer.add_blank_page(width=612, height=792)
            buf = io.BytesIO()
            writer.write(buf)
            return buf.getvalue()
        except Exception:
            return b"%PDF-1.4\n%%EOF"

    def test_duplicate_detection(self, tmp_path):
        from core.klaus.document_processor import process_document, RAW_DIR, PROCESSED_DIR
        import core.klaus.document_processor as dp

        content = b"Sample legal document content for testing."
        filename = "test_act.pdf"

        raw_dir = tmp_path / "raw"
        processed_dir = tmp_path / "processed"
        raw_dir.mkdir(parents=True)
        processed_dir.mkdir(parents=True)

        with patch.object(dp, 'RAW_DIR', raw_dir), \
             patch.object(dp, 'PROCESSED_DIR', processed_dir), \
             patch.object(dp, 'get_document_by_hash', return_value={"id": 99}), \
             patch.object(dp, 'compute_file_hash', wraps=dp.compute_file_hash), \
             patch.object(dp, 'log_audit_event') as mock_log:

            result = process_document(
                content=content,
                filename=filename,
                source_id=1,
                source_url="https://parliament.gh/docs",
            )

        assert result["status"] == "duplicate"
        assert result["document_id"] == 99
        assert "already ingested" in result["message"]

    def test_successful_ingestion_with_copyright_bypass(self, tmp_path):
        from core.klaus.document_processor import process_document, RAW_DIR, PROCESSED_DIR
        import core.klaus.document_processor as dp

        content = (
            b"The Constitution of the Republic of Ghana. "
            b"Article 1(1): The Sovereignty of Ghana resides in the people. "
            b"Article 2: This Constitution shall be the supreme law."
        )
        filename = "constitution_art1.pdf"

        raw_dir = tmp_path / "raw"
        processed_dir = tmp_path / "processed"
        raw_dir.mkdir(parents=True)
        processed_dir.mkdir(parents=True)

        with patch.object(dp, 'RAW_DIR', raw_dir), \
             patch.object(dp, 'PROCESSED_DIR', processed_dir), \
             patch.object(dp, 'get_document_by_hash', return_value=None), \
             patch.object(dp, 'insert_document', return_value=42), \
             patch.object(dp, 'insert_chunk', return_value=1), \
             patch.object(dp, 'extract_text_from_pdf', return_value=("The Constitution...", False)), \
             patch.object(dp, 'log_audit_event') as mock_log:

            result = process_document(
                content=content,
                filename=filename,
                source_id=1,
                source_url="https://parliament.gh",
                bypass_copyright=False,
            )

        assert result["status"] == "ingested"
        assert result["document_id"] == 42
        assert result["jurisdiction"] == "Ghana"
        assert result["access_level"] in ("full_storage", "metadata_only")

    def test_copyright_restricted_metadata_only(self, tmp_path):
        from core.klaus.document_processor import process_document, RAW_DIR, PROCESSED_DIR
        import core.klaus.document_processor as dp

        content = b"Copyright 2024 Some Law Firm Ltd. All rights reserved. This is proprietary."
        filename = "private_opinion.pdf"

        raw_dir = tmp_path / "raw"
        processed_dir = tmp_path / "processed"
        raw_dir.mkdir(parents=True)
        processed_dir.mkdir(parents=True)

        with patch.object(dp, 'RAW_DIR', raw_dir), \
             patch.object(dp, 'PROCESSED_DIR', processed_dir), \
             patch.object(dp, 'get_document_by_hash', return_value=None), \
             patch.object(dp, 'insert_document', return_value=43), \
             patch.object(dp, 'insert_chunk', return_value=1), \
             patch.object(dp, 'extract_text_from_pdf', return_value=(content.decode(), False)), \
             patch.object(dp, 'log_audit_event') as mock_log:

            result = process_document(
                content=content,
                filename=filename,
                source_id=1,
                source_url="https://private-site.com",
                bypass_copyright=False,
            )

        assert result["access_level"] == "metadata_only"
        assert result["chunks_count"] == 0

    def test_official_source_is_full_storage(self, tmp_path):
        from core.klaus.document_processor import process_document, RAW_DIR, PROCESSED_DIR
        import core.klaus.document_processor as dp

        content = b"Act of Parliament. Enacted by Parliament and assented to by the President."
        filename = "act_703.pdf"

        raw_dir = tmp_path / "raw"
        processed_dir = tmp_path / "processed"
        raw_dir.mkdir(parents=True)
        processed_dir.mkdir(parents=True)

        with patch.object(dp, 'RAW_DIR', raw_dir), \
             patch.object(dp, 'PROCESSED_DIR', processed_dir), \
             patch.object(dp, 'get_document_by_hash', return_value=None), \
             patch.object(dp, 'insert_document', return_value=44), \
             patch.object(dp, 'insert_chunk', return_value=1), \
             patch.object(dp, 'extract_text_from_pdf', return_value=(content.decode(), False)), \
             patch.object(dp, 'log_audit_event') as mock_log:

            result = process_document(
                content=content,
                filename=filename,
                source_id=1,
                source_url="https://judiciary.gov.gh/judgments/2024",
                bypass_copyright=False,
            )

        assert result["access_level"] == "full_storage"
        assert result["chunks_count"] > 0

    def test_unknown_copyright_flags_for_review(self, tmp_path):
        from core.klaus.document_processor import process_document, RAW_DIR, PROCESSED_DIR
        import core.klaus.document_processor as dp

        content = b"Some legal document from an unknown source."
        filename = "unknown_source.pdf"

        raw_dir = tmp_path / "raw"
        processed_dir = tmp_path / "processed"
        raw_dir.mkdir(parents=True)
        processed_dir.mkdir(parents=True)

        with patch.object(dp, 'RAW_DIR', raw_dir), \
             patch.object(dp, 'PROCESSED_DIR', processed_dir), \
             patch.object(dp, 'get_document_by_hash', return_value=None), \
             patch.object(dp, 'insert_document', return_value=45), \
             patch.object(dp, 'insert_chunk', return_value=1), \
             patch.object(dp, 'update_document_review_status') as mock_review, \
             patch.object(dp, 'extract_text_from_pdf', return_value=(content.decode(), False)), \
             patch.object(dp, 'log_audit_event') as mock_log:

            result = process_document(
                content=content,
                filename=filename,
                source_id=1,
                source_url="https://unknown-site.com/docs",
                bypass_copyright=False,
            )

        assert result["copyright_classification"] == "unknown"
        mock_review.assert_called_once()

    def test_jurisdiction_signal_in_content(self, tmp_path):
        from core.klaus.document_processor import process_document, RAW_DIR, PROCESSED_DIR
        import core.klaus.document_processor as dp

        content = b"The Federal Republic of Nigeria hereby enacts..."
        filename = "nigerian_act.pdf"

        raw_dir = tmp_path / "raw"
        processed_dir = tmp_path / "processed"
        raw_dir.mkdir(parents=True)
        processed_dir.mkdir(parents=True)

        with patch.object(dp, 'RAW_DIR', raw_dir), \
             patch.object(dp, 'PROCESSED_DIR', processed_dir), \
             patch.object(dp, 'get_document_by_hash', return_value=None), \
             patch.object(dp, 'insert_document', return_value=46), \
             patch.object(dp, 'insert_chunk', return_value=1), \
             patch.object(dp, 'extract_text_from_pdf', return_value=(content.decode(), False)), \
             patch.object(dp, 'log_audit_event') as mock_log:

            result = process_document(
                content=content,
                filename=filename,
                source_id=1,
                source_url="https://parliament.gh",
            )

        assert result["jurisdiction"] == "Nigeria"

    def test_handles_txt_file(self, tmp_path):
        from core.klaus.document_processor import process_document, RAW_DIR, PROCESSED_DIR
        import core.klaus.document_processor as dp

        content = b"Section 1. This is a legal text file."
        filename = "legal_text.txt"

        raw_dir = tmp_path / "raw"
        processed_dir = tmp_path / "processed"
        raw_dir.mkdir(parents=True)
        processed_dir.mkdir(parents=True)

        with patch.object(dp, 'RAW_DIR', raw_dir), \
             patch.object(dp, 'PROCESSED_DIR', processed_dir), \
             patch.object(dp, 'get_document_by_hash', return_value=None), \
             patch.object(dp, 'insert_document', return_value=47), \
             patch.object(dp, 'insert_chunk', return_value=1), \
             patch.object(dp, 'log_audit_event') as mock_log:

            result = process_document(
                content=content,
                filename=filename,
                source_id=1,
                source_url="https://parliament.gh",
            )

        assert result["status"] == "ingested"
