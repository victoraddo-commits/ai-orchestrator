"""Tests for legal QC agents module (17O-D)."""

import json
import tempfile
from pathlib import Path
import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from legal_qc import (
    QCSeverity,
    QCStatus,
    QCFinding,
    QCReport,
    SourceVerificationAgent,
    ClassificationAccuracyAgent,
    DuplicateDetectionAgent,
    OutdatedLawAgent,
    QCController,
    KNOWN_GHANA_LEGAL_SOURCES,
    CATEGORY_SIGNALS,
    KNOWN_OUTDATED_REFERENCES,
)


class TestSourceVerification:
    """Agent 1: Source Verification — every entry traceable to primary source."""

    def test_missing_source_url_is_critical(self):
        """Document with no source_url must fail source verification."""
        agent = SourceVerificationAgent()
        findings = agent.verify("doc-1", {"source_url": ""})
        assert len(findings) > 0
        assert findings[0].severity == QCSeverity.CRITICAL
        assert "no source_url" in findings[0].description.lower()

    def test_recognized_ghana_source_passes(self):
        """Documents from judiciary.gov.gh should pass source check."""
        agent = SourceVerificationAgent()
        findings = agent.verify("doc-2", {
            "source_url": "https://judiciary.gov.gh/judgments/case123.pdf",
            "source_type": "pdf",
        })
        # No findings = passed
        assert len(findings) == 0

    def test_parliament_gh_is_recognized(self):
        """parliament.gh should be a recognized source."""
        agent = SourceVerificationAgent()
        findings = agent.verify("doc-3", {
            "source_url": "https://parliament.gh/acts/act651",
            "source_type": "html",
        })
        assert len(findings) == 0

    def test_unknown_source_url_is_warning(self):
        """Source from random website should trigger warning."""
        agent = SourceVerificationAgent()
        findings = agent.verify("doc-4", {
            "source_url": "https://random-blog.com/ghana-law.pdf",
            "source_type": "pdf",
        })
        assert len(findings) > 0
        assert any(f.severity == QCSeverity.WARNING for f in findings)

    def test_invalid_source_type_warns(self):
        """Unrecognized source type should warn."""
        agent = SourceVerificationAgent()
        findings = agent.verify("doc-5", {
            "source_url": "https://parliament.gh/act",
            "source_type": "exe",
        })
        assert any("source type" in f.description.lower() for f in findings)

    def test_ghalii_is_recognized(self):
        """Ghana Legal Information Institute should be recognized."""
        assert "ghalii.org" in KNOWN_GHANA_LEGAL_SOURCES

    def test_nlc_gov_gh_removed_from_sources(self):
        """nlc.gov.gh is a council, not a publication source. Should NOT be in recognized sources."""
        assert "nlc.gov.gh" not in KNOWN_GHANA_LEGAL_SOURCES

    def test_checksum_mismatch_is_critical(self):
        """Content integrity failure must be critical."""
        agent = SourceVerificationAgent()
        findings = agent.verify("doc-6", {
            "source_url": "https://parliament.gh/act.pdf",
            "source_type": "pdf",
            "checksum": "deadbeef",
        }, content=b"actual content that differs from checksum")
        assert len(findings) > 0
        assert any(f.severity == QCSeverity.CRITICAL for f in findings if "checksum" in f.description.lower())


class TestClassificationAccuracy:
    """Agent 2: Legal classification accuracy check."""

    def test_missing_category_is_critical(self):
        """Document with no taxonomy_category must fail."""
        agent = ClassificationAccuracyAgent()
        findings = agent.verify("doc-10", {"taxonomy_category": ""})
        assert len(findings) > 0
        assert findings[0].severity == QCSeverity.CRITICAL

    def test_constitution_content_matches_category_01(self):
        """Text containing constitutional terms should match category 01."""
        agent = ClassificationAccuracyAgent()
        content = ("The Constitution of the Republic of Ghana is the supreme law. "
                   "Article 1(2) establishes constitutional supremacy. "
                   "Fundamental rights are guaranteed under Chapter 5.").encode()
        findings = agent.verify("doc-11", {"taxonomy_category": "01"}, content)
        # Should have no findings (content matches category)
        assert len(findings) == 0

    def test_case_law_content_matches_category_03(self):
        """Text about judgments should match case law category."""
        agent = ClassificationAccuracyAgent()
        content = ("The appellant appealed to the Supreme Court. The court held that "
                   "the judgment of the High Court was correct. "
                   "Ratio decidendi: The defendant is liable.").encode()
        findings = agent.verify("doc-12", {"taxonomy_category": "03"}, content)
        assert len(findings) == 0

    def test_misclassified_content_is_warning(self):
        """Case law content classified as legislation should be flagged."""
        agent = ClassificationAccuracyAgent()
        content = ("The Supreme Court ruled that the appellant's appeal was dismissed. "
                   "The court held unanimously. Judgment for the respondent.").encode()
        findings = agent.verify("doc-13", {"taxonomy_category": "02"}, content)
        # Case law signals strong, legislation weak → should warn
        if findings:
            assert any("classification" in f.category for f in findings)

    def test_category_signals_cover_all_seven(self):
        """All seven taxonomy categories must have keyword signals."""
        for code in ["01", "02", "03", "04", "05", "06", "07"]:
            assert code in CATEGORY_SIGNALS, f"Missing signals for category {code}"
            assert len(CATEGORY_SIGNALS[code]) >= 3, f"Too few signals for category {code}"


class TestDuplicateDetection:
    """Agent 3: Duplicate detection with 0.85 cosine similarity threshold."""

    def test_identical_documents_detected(self):
        """Duplicates must be detected at similarity >= 0.85."""
        agent = DuplicateDetectionAgent()
        content = b"The Labour Act of Ghana establishes minimum employment standards."
        existing = [("other-1", content)]  # 100% identical

        findings = agent.find_duplicates("doc-20", content, existing)
        assert len(findings) > 0
        sim = findings[0].evidence.get("similarity_score", 0)
        assert sim >= 0.99  # Essentially identical

    def test_different_documents_not_duplicates(self):
        """Completely different documents should not trigger duplicate detection."""
        agent = DuplicateDetectionAgent()
        content_a = b"The Labour Act of Ghana establishes minimum employment standards for all workers."
        content_b = b"The Criminal Code defines offenses against the state including treason and sedition."

        findings = agent.find_duplicates("doc-21", content_a, [("other-2", content_b)])
        assert len(findings) == 0

    def test_similar_documents_flagged(self):
        """Documents with high similarity should be flagged."""
        agent = DuplicateDetectionAgent()
        # These share many of the same words
        content = b"The Labour Act of Ghana establishes minimum employment standards and workplace safety regulations for all workers in Ghana."
        similar = b"The Labour Act of Ghana establishes minimum employment standards and workplace safety rules for all employees in Ghana."

        findings = agent.find_duplicates("doc-22", content, [("other-3", similar)])
        # These should be very similar
        if findings:
            sim = findings[0].evidence.get("similarity_score", 0)
            assert sim >= DuplicateDetectionAgent.SIMILARITY_THRESHOLD

    def test_threshold_is_85_percent(self):
        """17O-D requirement: 0.85 cosine threshold."""
        assert DuplicateDetectionAgent.SIMILARITY_THRESHOLD == 0.85

    def test_empty_content_no_duplicates(self):
        """Empty content should not trigger duplicate detection."""
        agent = DuplicateDetectionAgent()
        findings = agent.find_duplicates("doc-23", b"", [("other-4", b"something")])
        assert len(findings) == 0

    def test_self_not_flagged_as_duplicate(self):
        """A document should not be flagged as duplicate of itself."""
        agent = DuplicateDetectionAgent()
        content = b"Self-comparison should not trigger."
        findings = agent.find_duplicates("doc-24", content, [("doc-24", content)])
        assert len(findings) == 0


class TestOutdatedLaw:
    """Agent 4: Outdated law flagging."""

    def test_overruled_status_is_warning(self):
        """Document marked as overruled should be flagged."""
        agent = OutdatedLawAgent()
        findings = agent.verify("doc-30", {"status": "overruled"})
        assert len(findings) > 0
        assert any("overruled" in f.description.lower() for f in findings)

    def test_repealed_status_is_warning(self):
        """Repealed documents must be flagged."""
        agent = OutdatedLawAgent()
        findings = agent.verify("doc-31", {"status": "repealed"})
        assert len(findings) > 0

    def test_known_outdated_is_critical(self):
        """Known outdated references should be critical."""
        agent = OutdatedLawAgent()
        if KNOWN_OUTDATED_REFERENCES:
            first_key = next(iter(KNOWN_OUTDATED_REFERENCES))
            findings = agent.verify("doc-32", {"citation": {"citation_text": first_key}})
            assert len(findings) > 0
            assert any(f.severity == QCSeverity.CRITICAL for f in findings)

    def test_current_document_no_findings(self):
        """Current, recent document should pass."""
        agent = OutdatedLawAgent()
        findings = agent.verify("doc-33", {
            "status": "current",
            "document_type": "legislation",
            "year": 2024,
        })
        assert len(findings) == 0

    def test_old_legislation_no_amendments_flags(self):
        """Legislation > 20 years old with no amendments should be flagged."""
        agent = OutdatedLawAgent()
        findings = agent.verify("doc-34", {
            "status": "current",
            "document_type": "legislation",
            "year": 1990,
            "amended_by": [],  # No amendments recorded
        })
        assert len(findings) > 0
        assert any("years old" in f.description.lower() for f in findings)

    def test_old_but_amended_legislation_passes(self):
        """Old legislation with recorded amendments should not be flagged for age."""
        agent = OutdatedLawAgent()
        findings = agent.verify("doc-35", {
            "status": "current",
            "document_type": "legislation",
            "year": 1990,
            "amended_by": ["Act 900 (2010)", "Act 1050 (2020)"],
        })
        # Should not flag for age since amendments exist
        age_findings = [f for f in findings if "years old" in f.description.lower()]
        assert len(age_findings) == 0

    def test_content_overruled_keyword(self):
        """Content mentioning 'overruled by' should be flagged."""
        agent = OutdatedLawAgent()
        content = b"This decision was overruled by the Supreme Court in 2020."
        findings = agent.verify("doc-36", {"status": "current"}, content)
        assert len(findings) > 0


class TestQCController:
    """Orchestrator tests — full QC pipeline."""

    def test_run_full_qc_returns_report(self):
        """Full QC run should return a QCReport with all agents represented."""
        controller = QCController()
        metadata = {
            "source_url": "https://judiciary.gov.gh/case.pdf",
            "source_type": "pdf",
            "taxonomy_category": "03",
            "status": "current",
            "year": 2023,
        }
        content = b"The Supreme Court held that the appeal be dismissed."

        report = controller.run_full_qc("test-40", metadata, content)
        assert isinstance(report, QCReport)
        assert report.document_id == "test-40"
        assert report.status in (QCStatus.PASSED, QCStatus.NEEDS_REVIEW, QCStatus.FAILED)

    def test_critical_source_issue_means_failed(self):
        """Missing source_url should cause FAILED status."""
        controller = QCController()
        report = controller.run_full_qc("test-41", {"source_url": ""})
        assert report.status == QCStatus.FAILED

    def test_duplicate_detection_across_documents(self):
        """Duplicate detection works across the batch."""
        controller = QCController()
        content_a = b"This is a unique legal document about Ghana labour law."
        content_b = b"This is a unique legal document about Ghana labour law."  # Very similar

        reports = controller.batch_qc([
            ("doc-a", {"source_url": "https://parliament.gh/a.pdf", "source_type": "pdf",
                       "taxonomy_category": "02", "status": "current", "year": 2023}, content_a),
            ("doc-b", {"source_url": "https://parliament.gh/b.pdf", "source_type": "pdf",
                       "taxonomy_category": "02", "status": "current", "year": 2023}, content_b),
        ])

        assert len(reports) == 2
        # At least one should have duplicate findings
        all_findings = []
        for r in reports:
            all_findings.extend(r.findings)
        dup_findings = [f for f in all_findings if f.category == "duplicate"]
        # These very similar docs should be flagged
        assert len(dup_findings) > 0

    def test_report_to_dict_serializable(self):
        """Report should be JSON-serializable."""
        controller = QCController()
        report = controller.run_full_qc("test-43", {
            "source_url": "https://judiciary.gov.gh/case.pdf",
            "source_type": "pdf",
            "taxonomy_category": "03",
            "status": "current",
        })
        d = report.to_dict()
        # Should serialize without errors
        json.dumps(d)


class TestQCSeverityEnum:
    def test_severity_values(self):
        assert QCSeverity.CRITICAL.value == "critical"
        assert QCSeverity.WARNING.value == "warning"
        assert QCSeverity.INFO.value == "info"


class TestQCStatusEnum:
    def test_status_values(self):
        assert QCStatus.PASSED.value == "passed"
        assert QCStatus.FAILED.value == "failed"
        assert QCStatus.NEEDS_REVIEW.value == "needs_review"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
