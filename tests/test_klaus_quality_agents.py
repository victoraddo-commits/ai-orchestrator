"""
Tests for KLAUS quality control agents: SourceVerification, LegalClassification,
CitationExtraction, QualityAssurance, KnowledgeCurator.

All tests use mocked database calls to validate agent logic without
requiring a live PostgreSQL connection.
"""

import pytest
from unittest.mock import patch, MagicMock, PropertyMock

from core.klaus.quality_agents import (
    SourceVerificationAgent,
    LegalClassificationAgent,
    CitationExtractionAgent,
    QualityAssuranceAgent,
    KnowledgeCuratorAgent,
    run_all_agents,
    VerificationResult,
)


def _fake_doc(id, source_id, category, copyright_cls, access_level, file_hash, review_status="pending"):
    return {
        "id": id,
        "source_id": source_id,
        "title": f"Test Document {id}",
        "file_hash": file_hash,
        "file_path": f"/tmp/doc_{id}.pdf",
        "category": category,
        "jurisdiction": "Ghana",
        "copyright_classification": copyright_cls,
        "access_level": access_level,
        "review_status": review_status,
        "version": 1,
    }


def _fake_source(id, domain, tier, status="active", reliability=1.0):
    return {
        "id": id,
        "url": f"https://{domain}",
        "domain": domain,
        "tier": tier,
        "status": status,
        "reliability_score": reliability,
    }


def _make_chunks(document_id, texts):
    return [
        {"id": i + 1, "chunk_index": i, "content": t, "metadata": None}
        for i, t in enumerate(texts)
    ]


class TestSourceVerificationAgent:
    def test_verifies_gov_domain_source(self):
        agent = SourceVerificationAgent()
        doc = _fake_doc(1, 1, "Legislation", "official_public_access", "full_storage", "abc123")
        source = _fake_source(1, "judiciary.gov.gh", 1)

        with patch("core.klaus.quality_agents.get_document", return_value=doc), \
             patch("core.klaus.quality_agents.get_source", return_value=source), \
             patch("core.klaus.quality_agents.log_audit_event") as mock_log:
            result = agent.verify(1)

        assert result.passed is True
        assert "Source verified" in result.reason

    def test_rejects_broken_source(self):
        agent = SourceVerificationAgent()
        doc = _fake_doc(1, 1, "Legislation", "official_public_access", "full_storage", "abc123")
        source = _fake_source(1, "broken.gov.gh", 1, status="broken")

        with patch("core.klaus.quality_agents.get_document", return_value=doc), \
             patch("core.klaus.quality_agents.get_source", return_value=source), \
             patch("core.klaus.quality_agents.log_audit_event"):
            result = agent.verify(1)

        assert result.passed is False
        assert "broken" in result.reason

    def test_rejects_low_reliability(self):
        agent = SourceVerificationAgent()
        doc = _fake_doc(1, 1, "Legislation", "official_public_access", "full_storage", "abc123")
        source = _fake_source(1, "dodgy.site", 1, reliability=0.1)

        with patch("core.klaus.quality_agents.get_document", return_value=doc), \
             patch("core.klaus.quality_agents.get_source", return_value=source), \
             patch("core.klaus.quality_agents.log_audit_event"):
            result = agent.verify(1)

        assert result.passed is False
        assert "reliability" in result.reason.lower()

    def test_document_not_found(self):
        agent = SourceVerificationAgent()
        with patch("core.klaus.quality_agents.get_document", return_value=None), \
             patch("core.klaus.quality_agents.log_audit_event"):
            result = agent.verify(999)

        assert result.passed is False
        assert "not found" in result.reason.lower()

    def test_source_not_found(self):
        agent = SourceVerificationAgent()
        doc = _fake_doc(1, 1, "Legislation", "official_public_access", "full_storage", "abc123")

        with patch("core.klaus.quality_agents.get_document", return_value=doc), \
             patch("core.klaus.quality_agents.get_source", return_value=None), \
             patch("core.klaus.quality_agents.log_audit_event"):
            result = agent.verify(1)

        assert result.passed is False
        assert "not found in catalog" in result.reason.lower()

    def test_warns_tier1_non_gov_domain(self):
        agent = SourceVerificationAgent()
        doc = _fake_doc(1, 1, "Legislation", "official_public_access", "full_storage", "abc123")
        source = _fake_source(1, "legal-docs.org", 1)

        with patch("core.klaus.quality_agents.get_document", return_value=doc), \
             patch("core.klaus.quality_agents.get_source", return_value=source), \
             patch("core.klaus.quality_agents.log_audit_event"):
            result = agent.verify(1)

        assert result.passed is True
        assert "Warning" in result.reason
        assert "tier_1_non_gov_domain" in result.warnings


class TestLegalClassificationAgent:
    def test_valid_classification(self):
        agent = LegalClassificationAgent()
        doc = _fake_doc(1, 1, "Constitutional Law", "public_domain", "full_storage", "abc123")

        with patch("core.klaus.quality_agents.get_document", return_value=doc), \
             patch("core.klaus.quality_agents.log_audit_event"):
            result = agent.verify(1)

        assert result.passed is True

    def test_rejects_invalid_category(self):
        agent = LegalClassificationAgent()
        doc = _fake_doc(1, 1, "InvalidCategory", "public_domain", "full_storage", "abc123")

        with patch("core.klaus.quality_agents.get_document", return_value=doc), \
             patch("core.klaus.quality_agents.log_audit_event"):
            result = agent.verify(1)

        assert result.passed is False

    def test_rejects_invalid_copyright(self):
        agent = LegalClassificationAgent()
        doc = _fake_doc(1, 1, "Legislation", "invalid_copyright", "full_storage", "abc123")

        with patch("core.klaus.quality_agents.get_document", return_value=doc), \
             patch("core.klaus.quality_agents.log_audit_event"):
            result = agent.verify(1)

        assert result.passed is False

    def test_warns_copyright_protected_with_full_storage(self):
        agent = LegalClassificationAgent()
        doc = _fake_doc(1, 1, "Legal Scholarship", "copyright_protected", "full_storage", "abc123")

        with patch("core.klaus.quality_agents.get_document", return_value=doc), \
             patch("core.klaus.quality_agents.log_audit_event"):
            result = agent.verify(1)

        assert result.passed is True
        assert len(result.warnings) >= 1

    def test_rejects_invalid_access_level(self):
        agent = LegalClassificationAgent()
        doc = _fake_doc(1, 1, "Legislation", "public_domain", "invalid_level", "abc123")

        with patch("core.klaus.quality_agents.get_document", return_value=doc), \
             patch("core.klaus.quality_agents.log_audit_event"):
            result = agent.verify(1)

        assert result.passed is False


class TestCitationExtractionAgent:
    def test_extracts_citations_from_chunks(self):
        agent = CitationExtractionAgent()
        doc = _fake_doc(1, 1, "Judiciary", "public_domain", "full_storage", "abc123")
        chunks = _make_chunks(1, [
            "Article 1 is foundational. Section 5 provides for jurisdiction.",
            "As held in [2021] SCGLR 234, and Act 703 section 12.",
            "L.I. 1930 provides further procedural rules.",
        ])

        with patch("core.klaus.quality_agents.get_document", return_value=doc), \
             patch("core.klaus.quality_agents.get_chunks_for_document", return_value=chunks), \
             patch("core.klaus.quality_agents.log_audit_event"):
            result = agent.verify(1)

        assert result.passed is True
        assert "citations" in result.reason.lower()

    def test_no_citations_for_legal_scholarship_is_ok(self):
        agent = CitationExtractionAgent()
        doc = _fake_doc(1, 1, "Legal Scholarship", "public_domain", "full_storage", "abc123")
        chunks = _make_chunks(1, [
            "This paper explores legal theory in the Ghanaian context."
        ])

        with patch("core.klaus.quality_agents.get_document", return_value=doc), \
             patch("core.klaus.quality_agents.get_chunks_for_document", return_value=chunks), \
             patch("core.klaus.quality_agents.log_audit_event"):
            result = agent.verify(1)

        assert result.passed is True

    def test_warns_no_citations_for_non_scholarship(self):
        agent = CitationExtractionAgent()
        doc = _fake_doc(1, 1, "Judiciary", "public_domain", "full_storage", "abc123")
        chunks = _make_chunks(1, [
            "A document about the judiciary with no citations whatsoever."
        ])

        with patch("core.klaus.quality_agents.get_document", return_value=doc), \
             patch("core.klaus.quality_agents.get_chunks_for_document", return_value=chunks), \
             patch("core.klaus.quality_agents.log_audit_event"):
            result = agent.verify(1)

        assert result.passed is True
        assert "no_citations_found" in result.warnings


class TestQualityAssuranceAgent:
    def test_checks_chunks(self):
        agent = QualityAssuranceAgent()
        doc = _fake_doc(1, 1, "Legislation", "public_domain", "full_storage", "abc123")
        chunks = _make_chunks(1, [
            "Paragraph one with proper legal content."
        ])

        with patch("core.klaus.quality_agents.get_document", return_value=doc), \
             patch("core.klaus.quality_agents.get_chunks_for_document", return_value=chunks), \
             patch("core.klaus.quality_agents.get_document_by_hash", return_value=None), \
             patch("core.klaus.quality_agents.log_audit_event"):
            result = agent.verify(1)

        assert result.passed is True

    def test_warns_empty_chunks_for_full_storage(self):
        agent = QualityAssuranceAgent()
        doc = _fake_doc(1, 1, "Legislation", "public_domain", "full_storage", "abc123")

        with patch("core.klaus.quality_agents.get_document", return_value=doc), \
             patch("core.klaus.quality_agents.get_chunks_for_document", return_value=[]), \
             patch("core.klaus.quality_agents.get_document_by_hash", return_value=None), \
             patch("core.klaus.quality_agents.log_audit_event"):
            result = agent.verify(1)

        assert result.passed is True
        assert "no chunks" in " ".join(result.warnings).lower() or any("chunks" in w.lower() for w in result.warnings)

    def test_detects_duplicate_hash(self):
        agent = QualityAssuranceAgent()
        doc = _fake_doc(1, 1, "Legislation", "public_domain", "full_storage", "abc123")
        chunks = _make_chunks(1, ["Some content."])
        duplicate_doc = _fake_doc(2, 1, "Legislation", "public_domain", "full_storage", "abc123")

        with patch("core.klaus.quality_agents.get_document", return_value=doc), \
             patch("core.klaus.quality_agents.get_chunks_for_document", return_value=chunks), \
             patch("core.klaus.quality_agents.get_document_by_hash", return_value=duplicate_doc), \
             patch("core.klaus.quality_agents.log_audit_event"):
            result = agent.verify(1)

        assert result.passed is True
        assert len(result.warnings) >= 1

    def test_detects_ocr_noise(self):
        agent = QualityAssuranceAgent()
        doc = _fake_doc(1, 1, "Legislation", "public_domain", "full_storage", "abc123")
        noisy_text = "ABCDEFGHIJKLMNOP ?????????????? |||||||||| ABCDEFGHIJKLMNOPQRSTUV ???? XYZABCDEFGH"
        chunks = _make_chunks(1, [noisy_text])

        with patch("core.klaus.quality_agents.get_document", return_value=doc), \
             patch("core.klaus.quality_agents.get_chunks_for_document", return_value=chunks), \
             patch("core.klaus.quality_agents.get_document_by_hash", return_value=None), \
             patch("core.klaus.quality_agents.log_audit_event"):
            result = agent.verify(1)

        assert result.passed is True
        assert any("OCR" in w or "noise" in w for w in result.warnings)

    def test_metadata_only_no_chunks_ok(self):
        agent = QualityAssuranceAgent()
        doc = _fake_doc(1, 1, "Legal Scholarship", "copyright_protected", "metadata_only", "abc123")

        with patch("core.klaus.quality_agents.get_document", return_value=doc), \
             patch("core.klaus.quality_agents.get_chunks_for_document", return_value=[]), \
             patch("core.klaus.quality_agents.get_document_by_hash", return_value=None), \
             patch("core.klaus.quality_agents.log_audit_event"):
            result = agent.verify(1)

        assert result.passed is True


class TestKnowledgeCuratorAgent:
    def test_approves_clean_document(self):
        curator = KnowledgeCuratorAgent()
        doc = _fake_doc(1, 1, "Constitutional Law", "public_domain", "full_storage", "abc123")
        agent_results = {
            "source_verification": VerificationResult(True, "Source verified"),
            "legal_classification": VerificationResult(True, "Classification valid"),
            "citation_extraction": VerificationResult(True, "Found 5 citations"),
            "quality_assurance": VerificationResult(True, "QA passed"),
        }

        with patch("core.klaus.quality_agents.get_document", return_value=doc), \
             patch("core.klaus.quality_agents.update_document_review_status") as mock_review, \
             patch("core.klaus.quality_agents.log_audit_event"):
            passed, result = curator.curate(1, agent_results)

        assert passed is True
        assert result["status"] == "approved"
        mock_review.assert_called_with(1, "approved")

    def test_flags_on_any_agent_failure(self):
        curator = KnowledgeCuratorAgent()
        doc = _fake_doc(1, 1, "Constitutional Law", "public_domain", "full_storage", "abc123")
        agent_results = {
            "source_verification": VerificationResult(False, "Source broken"),
            "legal_classification": VerificationResult(True, "Classification valid"),
            "citation_extraction": VerificationResult(True, "Found citations"),
            "quality_assurance": VerificationResult(True, "QA passed"),
        }

        with patch("core.klaus.quality_agents.get_document", return_value=doc), \
             patch("core.klaus.quality_agents.update_document_review_status") as mock_review, \
             patch("core.klaus.quality_agents.log_audit_event"):
            passed, result = curator.curate(1, agent_results)

        assert passed is False
        assert result["status"] == "flagged"
        mock_review.assert_called_with(1, "flagged")

    def test_flags_unknown_copyright(self):
        curator = KnowledgeCuratorAgent()
        doc = _fake_doc(1, 1, "Legislation", "unknown", "full_storage", "abc123")
        agent_results = {
            "source_verification": VerificationResult(True, "Source verified"),
            "legal_classification": VerificationResult(True, "Classification valid",
                                                       warnings=["copyright=unknown but access_level=full_storage"]),
            "citation_extraction": VerificationResult(True, "Found citations"),
            "quality_assurance": VerificationResult(True, "QA passed"),
        }

        with patch("core.klaus.quality_agents.get_document", return_value=doc), \
             patch("core.klaus.quality_agents.update_document_review_status") as mock_review, \
             patch("core.klaus.quality_agents.log_audit_event"):
            passed, result = curator.curate(1, agent_results)

        assert passed is False
        assert result["status"] == "flagged"

    def test_document_not_found(self):
        curator = KnowledgeCuratorAgent()
        agent_results = {
            "source_verification": VerificationResult(True, "OK"),
        }

        with patch("core.klaus.quality_agents.get_document", return_value=None):
            passed, result = curator.curate(999, agent_results)

        assert passed is False
        assert result["status"] == "error"

    def test_appends_all_warnings(self):
        curator = KnowledgeCuratorAgent()
        doc = _fake_doc(1, 1, "Legislation", "public_domain", "full_storage", "abc123")
        agent_results = {
            "source_verification": VerificationResult(True, "OK", ["warn1"]),
            "legal_classification": VerificationResult(True, "OK", ["warn2"]),
            "citation_extraction": VerificationResult(True, "OK"),
            "quality_assurance": VerificationResult(True, "OK", ["warn3"]),
        }

        with patch("core.klaus.quality_agents.get_document", return_value=doc), \
             patch("core.klaus.quality_agents.update_document_review_status"), \
             patch("core.klaus.quality_agents.log_audit_event"):
            passed, result = curator.curate(1, agent_results)

        assert len(result["warnings"]) == 3


class TestRunAllAgents:
    def test_runs_all_five_agents(self):
        doc = _fake_doc(1, 1, "Constitutional Law", "public_domain", "full_storage", "abc123")
        chunks = _make_chunks(1, ["Article 1 establishes the Constitution."])

        with patch("core.klaus.quality_agents.get_document", return_value=doc), \
             patch("core.klaus.quality_agents.get_source", return_value=_fake_source(1, "parliament.gh", 1)), \
             patch("core.klaus.quality_agents.get_chunks_for_document", return_value=chunks), \
             patch("core.klaus.quality_agents.get_document_by_hash", return_value=None), \
             patch("core.klaus.quality_agents.update_document_review_status"), \
             patch("core.klaus.quality_agents.log_audit_event"):

            results = run_all_agents(1)

        assert "source_verification" in results
        assert "legal_classification" in results
        assert "citation_extraction" in results
        assert "quality_assurance" in results
        assert "knowledge_curator" in results
        assert "overall" in results

    def test_rejected_document_ends_up_flagged(self):
        doc = _fake_doc(1, 1, "InvalidCat", "invalid_cr", "bad_level", "abc123")

        with patch("core.klaus.quality_agents.get_document", return_value=doc), \
             patch("core.klaus.quality_agents.get_source", return_value=_fake_source(1, "broken.gov.gh", 1, "broken")), \
             patch("core.klaus.quality_agents.get_chunks_for_document", return_value=[]), \
             patch("core.klaus.quality_agents.get_document_by_hash", return_value=None), \
             patch("core.klaus.quality_agents.update_document_review_status"), \
             patch("core.klaus.quality_agents.log_audit_event"):

            results = run_all_agents(1)

        assert results["overall"] in ("flagged", "failed")
        assert results["legal_classification"]["passed"] is False
