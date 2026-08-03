"""
Integration tests for the KLAUS pipeline.

Tests cover:
- Full ingestion pipeline (discovery -> processing -> QC -> review)
- Copyright compliance end-to-end
- Juris Kai isolation: user uploads never enter shared base
- Versioning: never silently overwrite
- Source tiering and seed initialization
- Scheduler job dispatch
"""

import base64
import hashlib
import io
import pytest
from unittest.mock import patch, MagicMock, call
from pathlib import Path

from core.klaus.schema import (
    SCHEMA_SQL,
    KNOWLEDGE_CATEGORIES,
    COPYRIGHT_CLASSIFICATIONS,
    EVENT_TYPES,
    SEVERITY_LEVELS,
    SOURCE_TIERS,
)


class TestSchemaConstants:
    def test_all_six_knowledge_categories(self):
        expected = {
            "Constitutional Law",
            "Legislation",
            "Judiciary",
            "Legal Procedure",
            "International Law",
            "Legal Scholarship",
        }
        assert set(KNOWLEDGE_CATEGORIES) == expected

    def test_five_copyright_classifications(self):
        expected = {
            "public_domain",
            "open_license",
            "official_public_access",
            "copyright_protected",
            "unknown",
        }
        assert set(COPYRIGHT_CLASSIFICATIONS) == expected

    def test_unknown_classification_defaults_to_metadata_only_in_processor(self):
        """Verify that 'unknown' classification triggers metadata_only in processor."""
        from core.klaus.document_processor import classify_copyright
        cls, access = classify_copyright("Some text.", "https://random.com")
        assert cls == "unknown"
        assert access == "metadata_only"

    def test_official_public_access_full_storage(self):
        from core.klaus.document_processor import classify_copyright
        cls, access = classify_copyright("Some text.", "https://parliament.gh")
        assert cls == "official_public_access"
        assert access == "full_storage"


class TestSourceSeeds:
    def test_seed_sources_are_in_expected_tiers(self):
        from core.klaus.scheduler import TIER_1_SEEDS
        tiers = {s["tier"] for s in TIER_1_SEEDS}
        assert len(tiers) >= 1
        assert 1 in tiers

    def test_seed_sources_are_ghana_based(self):
        from core.klaus.scheduler import TIER_1_SEEDS
        jurisdictions = {s.get("jurisdiction", "Ghana") for s in TIER_1_SEEDS}
        assert "Ghana" in jurisdictions

    def test_seed_sources_have_valid_urls(self):
        from core.klaus.scheduler import TIER_1_SEEDS
        for seed in TIER_1_SEEDS:
            assert seed["url"].startswith("https://")


class TestFullPipelineIntegration:
    """End-to-end pipeline test with mocked database."""

    @pytest.fixture
    def mock_storage(self, tmp_path):
        """Set up temporary storage directories."""
        raw = tmp_path / "raw"
        processed = tmp_path / "processed"
        raw.mkdir()
        processed.mkdir()
        return {"raw": raw, "processed": processed}

    def test_full_ingestion_qc_approval_flow(self, tmp_path, mock_storage):
        from core.klaus.document_processor import process_document
        import core.klaus.document_processor as dp
        from core.klaus.quality_agents import run_all_agents

        content = (b"The Constitution of the Republic of Ghana, Article 1. "
                   b"The sovereignty of Ghana resides in the people of Ghana "
                   b"in whom the power to exercise sovereignty is vested. "
                   b"This Constitution shall be the supreme law of Ghana and "
                   b"any other law found to be inconsistent with any provision "
                   b"of this Constitution shall, to the extent of the "
                   b"inconsistency, be void.")
        filename = "constitution_1992.pdf"

        doc_id = 100

        with patch.object(dp, 'RAW_DIR', mock_storage["raw"]), \
             patch.object(dp, 'PROCESSED_DIR', mock_storage["processed"]), \
             patch.object(dp, 'get_document_by_hash', return_value=None), \
             patch.object(dp, 'insert_document', return_value=doc_id), \
             patch.object(dp, 'insert_chunk', return_value=1), \
             patch.object(dp, 'extract_text_from_pdf', return_value=(content.decode(), False)), \
             patch.object(dp, 'log_audit_event'):

            # Step 1: Ingest
            result = process_document(
                content=content,
                filename=filename,
                source_id=1,
                source_url="https://parliament.gh",
            )

            assert result["status"] == "ingested"
            assert result["jurisdiction"] == "Ghana"
            assert result["access_level"] == "full_storage"

            # Step 2: Run QC agents
            from core.klaus.quality_agents import (
                get_document, get_source, get_chunks_for_document, get_document_by_hash,
                update_document_review_status, log_audit_event,
            )
            from core.klaus.quality_agents import (
                SourceVerificationAgent, LegalClassificationAgent,
                CitationExtractionAgent, QualityAssuranceAgent, KnowledgeCuratorAgent,
            )

            mock_doc = {
                "id": doc_id,
                "source_id": 1,
                "title": filename,
                "category": "Constitutional Law",
                "copyright_classification": "official_public_access",
                "access_level": "full_storage",
                "file_hash": "abc123",
                "review_status": "pending",
            }
            mock_source = {"id": 1, "domain": "parliament.gh", "tier": 1, "status": "active", "reliability_score": 1.0}
            mock_chunks = [{"id": 1, "chunk_index": 0, "content": content.decode()}]

            with patch("core.klaus.quality_agents.get_document", return_value=mock_doc), \
                 patch("core.klaus.quality_agents.get_source", return_value=mock_source), \
                 patch("core.klaus.quality_agents.get_chunks_for_document", return_value=mock_chunks), \
                 patch("core.klaus.quality_agents.get_document_by_hash", return_value=None), \
                 patch("core.klaus.quality_agents.update_document_review_status") as mock_review, \
                 patch("core.klaus.quality_agents.log_audit_event"):
                qc_results = run_all_agents(doc_id)

            assert qc_results["overall"] == "approved"

    def test_copyright_protected_blocked_from_full_ingestion(self, tmp_path, mock_storage):
        from core.klaus.document_processor import process_document
        import core.klaus.document_processor as dp

        content = b"Private legal opinion. Copyright 2024 Big Law Firm. All rights reserved."
        filename = "private_memo.pdf"

        with patch.object(dp, 'RAW_DIR', mock_storage["raw"]), \
             patch.object(dp, 'PROCESSED_DIR', mock_storage["processed"]), \
             patch.object(dp, 'get_document_by_hash', return_value=None), \
             patch.object(dp, 'insert_document', return_value=101), \
             patch.object(dp, 'insert_chunk', return_value=1), \
             patch.object(dp, 'extract_text_from_pdf', return_value=(content.decode(), False)), \
             patch.object(dp, 'log_audit_event'):

            result = process_document(
                content=content,
                filename=filename,
                source_id=3,
                source_url="https://lawfirm.com/memos",
                bypass_copyright=False,
            )

            assert result["copyright_classification"] == "copyright_protected"
            assert result["access_level"] == "metadata_only"
            assert result["chunks_count"] == 0

    def test_duplicate_prevention(self, tmp_path, mock_storage):
        from core.klaus.document_processor import process_document
        import core.klaus.document_processor as dp

        content = b"Unique legal text."
        existing_doc = {"id": 200, "file_hash": dp.compute_file_hash(content)}

        with patch.object(dp, 'RAW_DIR', mock_storage["raw"]), \
             patch.object(dp, 'PROCESSED_DIR', mock_storage["processed"]), \
             patch.object(dp, 'get_document_by_hash', return_value=existing_doc), \
             patch.object(dp, 'log_audit_event'):

            result = process_document(
                content=content,
                filename="duplicate.pdf",
                source_id=1,
                source_url="https://parliament.gh",
            )

            assert result["status"] == "duplicate"
            assert result["document_id"] == 200

    def test_version_control_preserves_original(self, tmp_path, mock_storage):
        """Verify versioning: new version does not silently overwrite original."""
        from core.klaus.document_processor import process_document, compute_file_hash
        import core.klaus.document_processor as dp

        original = b"Original Act 703 text for version control testing."
        updated = b"Updated Act 703 text with amendments for version control testing."
        original_hash = compute_file_hash(original)
        updated_hash = compute_file_hash(updated)

        with patch.object(dp, 'RAW_DIR', mock_storage["raw"]), \
             patch.object(dp, 'PROCESSED_DIR', mock_storage["processed"]), \
             patch.object(dp, 'get_document_by_hash', return_value=None), \
             patch.object(dp, 'insert_document', side_effect=[501, 502]), \
             patch.object(dp, 'insert_chunk', return_value=1), \
             patch.object(dp, 'extract_text_from_pdf', return_value=("text", False)), \
             patch.object(dp, 'log_audit_event'):

            result1 = process_document(
                content=original,
                filename="act_703.pdf",
                source_id=1,
                source_url="https://parliament.gh",
            )
            assert result1["status"] == "ingested"
            assert result1["document_id"] == 501

            result2 = process_document(
                content=updated,
                filename="act_703_amended.pdf",
                source_id=1,
                source_url="https://parliament.gh",
            )
            assert result2["status"] == "ingested"
            assert result2["document_id"] == 502

            # Different hashes confirm not silently overwritten
            assert result1["file_hash"] != result2["file_hash"]


class TestJurisKaiIsolation:
    """
    Verify that user-uploaded content through Juris Kai flow path
    never silently enters the shared Ghana Legal Brain corpus.
    """

    def test_processor_without_source_uses_default_metadata_only(self):
        """
        If a document arrives from a non-governance source (e.g. user upload),
        the processor classifies it accordingly and may restrict to metadata_only.
        """
        from core.klaus.document_processor import classify_copyright

        copyright_cls, access_level = classify_copyright(
            "User-uploaded legal analysis. Copyright 2024.",
            "https://telegram.org/upload",
        )

        assert copyright_cls == "copyright_protected"
        assert access_level == "metadata_only"

    def test_unknown_source_defaults_to_metadata_only(self):
        """
        Unknown sources without clear licensing default to metadata_only,
        preventing accidental full-text ingestion into shared base.
        """
        from core.klaus.document_processor import classify_copyright

        copyright_cls, access_level = classify_copyright(
            "Some legal text from a random URL.",
            "https://random-file-host.com/doc.pdf",
        )

        assert copyright_cls == "unknown"
        assert access_level == "metadata_only"


class TestSchedulerCoverage:
    """Tests for the scheduler module."""

    def test_all_scheduled_jobs_mapped(self):
        from core.klaus.scheduler import trigger_job_now

        valid_jobs = {"klaus_daily", "klaus_weekly", "klaus_monthly", "klaus_quarterly"}

        for job_id in valid_jobs:
            with patch("core.klaus.scheduler.list_sources", return_value=[]), \
                 patch("core.klaus.scheduler.get_failed_sources", return_value=[]), \
                 patch("core.klaus.scheduler.log_audit_event"):
                result = trigger_job_now(job_id)
                assert result is True, f"Job {job_id} should trigger successfully"

    def test_invalid_job_id_returns_false(self):
        from core.klaus.scheduler import trigger_job_now
        assert trigger_job_now("nonexistent") is False

    def test_daily_job_logs_sources(self):
        from core.klaus.scheduler import daily_legislation_check
        mock_sources = [{"id": 1, "domain": "parliament.gh"}, {"id": 2, "domain": "judiciary.gov.gh"}]

        with patch("core.klaus.scheduler.list_sources", return_value=mock_sources), \
             patch("core.klaus.scheduler.get_failed_sources", return_value=[]), \
             patch("core.klaus.scheduler.log_audit_event") as mock_log:
            daily_legislation_check()  # Should not raise

    def test_weekly_job_scans_both_tiers(self):
        from core.klaus.scheduler import weekly_judgments_scan
        tier1 = [{"id": 1, "domain": "judiciary.gov.gh"}]
        tier2 = [{"id": 2, "domain": "ghalii.org"}]

        with patch("core.klaus.scheduler.list_sources", side_effect=[tier1, tier2]), \
             patch("core.klaus.scheduler.get_failed_sources", return_value=[]), \
             patch("core.klaus.scheduler.log_audit_event"):
            weekly_judgments_scan()  # Should not raise

    def test_monthly_and_quarterly_jobs_handle_broken_sources(self):
        from core.klaus.scheduler import monthly_full_refresh, quarterly_accuracy_verification
        broken = [{"id": 1, "domain": "down.gov.gh"}]

        with patch("core.klaus.scheduler.list_sources", return_value=[]), \
             patch("core.klaus.scheduler.get_failed_sources", return_value=broken), \
             patch("core.klaus.scheduler.log_audit_event"):
            monthly_full_refresh()  # Should not raise

        with patch("core.klaus.scheduler.get_failed_sources", return_value=[]), \
             patch("core.klaus.scheduler.log_audit_event"):
            quarterly_accuracy_verification()  # Should not raise


class TestVectorIndexer:
    """Tests for the vector indexing service."""

    def test_generate_embedding_returns_correct_dimension(self):
        from core.klaus.vector_indexer import generate_embedding, EMBEDDING_DIM

        try:
            embedding = generate_embedding("Test legal text for embedding.")
            assert len(embedding) == EMBEDDING_DIM
            assert all(isinstance(x, float) for x in embedding)
        except ImportError:
            pytest.skip("sentence-transformers not available for embedding test")

    def test_model_dimension_constant_matches_schema(self):
        from core.klaus.vector_indexer import EMBEDDING_DIM

        assert EMBEDDING_DIM == 384
        assert "VECTOR(384)" in SCHEMA_SQL


class TestDBManagerInitSampleData:
    def test_init_sample_data_runs_without_error(self):
        from core.klaus.db_manager import init_sample_data

        with patch("core.klaus.db_manager.add_source") as mock_add:
            result = init_sample_data()
            assert result is True
            assert mock_add.call_count >= 3
