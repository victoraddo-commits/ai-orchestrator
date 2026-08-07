"""17O-D: Legal Quality Control Agents for Ghana Legal Knowledge Base.

Four QC agents ensuring the integrity of the legal knowledge base:
  1. SourceVerificationAgent — every entry traceable to primary source
  2. ClassificationAccuracyAgent — verify taxonomy categorization
  3. DuplicateDetectionAgent — find near-duplicates (cosine similarity ≥ 0.85)
  4. OutdatedLawAgent — flag potentially overruled/amended/repealed law

Designed to integrate with 17O-B (legal taxonomy) and 17O-C (metadata storage).
"""

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from math import sqrt
from pathlib import Path
from typing import Optional, List, Dict, Any, Set, Tuple
import logging

logger = logging.getLogger("legal_qc")


# ---------------------------------------------------------------------------
# QC Result types
# ---------------------------------------------------------------------------

class QCSeverity(str, Enum):
    """Severity of a QC finding."""
    CRITICAL = "critical"    # Must fix — source unverifiable, wrong classification
    WARNING = "warning"      # Should review — potential duplicate, possibly outdated
    INFO = "info"            # Informational — recommended check


class QCStatus(str, Enum):
    """Overall QC pass/fail for a document."""
    PASSED = "passed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


@dataclass
class QCFinding:
    """A single QC finding against a document."""
    agent: str                     # Which agent generated this
    document_id: str
    severity: QCSeverity
    category: str                  # "source_verification", "classification", "duplicate", "outdated"
    description: str
    recommendation: str
    evidence: Dict[str, Any] = field(default_factory=dict)
    detected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class QCReport:
    """Aggregate QC report for a document or batch."""
    document_id: str
    status: QCStatus
    findings: List[QCFinding] = field(default_factory=list)
    passed_agents: int = 0
    failed_agents: int = 0
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d["findings"] = [asdict(f) for f in self.findings]
        for fdict in d["findings"]:
            fdict["severity"] = fdict["severity"] if isinstance(fdict["severity"], str) else fdict["severity"].value
        return d


# ---------------------------------------------------------------------------
# Agent 1: Source Verification
# ---------------------------------------------------------------------------

KNOWN_GHANA_LEGAL_SOURCES: Set[str] = {
    "parliament.gh",
    "judiciary.gov.gh",
    "gazette.gov.gh",
    "laws.ghanalegal.com",
    "ghalii.org",           # Ghana Legal Information Institute
    "data.gov.gh",
    "mojagd.gov.gh",        # Ministry of Justice
    "nlc.gov.gh",           # National Law Council — not a valid source, removed
}

# 17O-C removed nlc.gov.gh — it's a council, not a publication source.
KNOWN_GHANA_LEGAL_SOURCES.discard("nlc.gov.gh")

REQUIRED_SOURCE_FIELDS = {"source_url", "source_type"}
VALID_SOURCE_TYPES = {"pdf", "html", "txt", "json", "xml", "docx"}


class SourceVerificationAgent:
    """Every entry in the knowledge base must be traceable to a primary source.

    Checks:
      - Document has a source_url
      - Source URL is from a recognized Ghana legal source
      - Source type is valid
      - Original content checksum matches stored content
    """

    NAME = "source_verification"

    def verify(self, document_id: str, metadata: Dict[str, Any],
               content: Optional[bytes] = None) -> List[QCFinding]:
        findings: List[QCFinding] = []

        # Check source URL exists
        source_url = metadata.get("source_url", "")
        if not source_url:
            findings.append(QCFinding(
                agent=self.NAME,
                document_id=document_id,
                severity=QCSeverity.CRITICAL,
                category="source_verification",
                description="Document has no source_url — cannot trace to primary source",
                recommendation="Add a source_url pointing to the original Ghana legal source",
            ))
            return findings

        # Check source URL is from a recognized source
        recognized = False
        for known_source in KNOWN_GHANA_LEGAL_SOURCES:
            if known_source in source_url.lower():
                recognized = True
                break

        if not recognized:
            findings.append(QCFinding(
                agent=self.NAME,
                document_id=document_id,
                severity=QCSeverity.WARNING,
                category="source_verification",
                description=f"Source URL '{source_url[:100]}' is not from a recognized Ghana legal source",
                recommendation="Verify the source is a legitimate primary legal source. Recognized sources include: parliament.gh, judiciary.gov.gh, gazette.gov.gh, ghalii.org",
                evidence={"source_url": source_url},
            ))

        # Check source type
        source_type = metadata.get("source_type", "")
        if source_type and source_type.lower() not in VALID_SOURCE_TYPES:
            findings.append(QCFinding(
                agent=self.NAME,
                document_id=document_id,
                severity=QCSeverity.WARNING,
                category="source_verification",
                description=f"Source type '{source_type}' is not a recognized document format",
                recommendation="Use a standard format: pdf, html, txt, json, xml, or docx",
                evidence={"source_type": source_type},
            ))

        # Check content integrity if provided
        if content is not None:
            stored_checksum = metadata.get("checksum")
            if stored_checksum:
                actual_checksum = hashlib.sha256(content).hexdigest()
                if stored_checksum != actual_checksum:
                    findings.append(QCFinding(
                        agent=self.NAME,
                        document_id=document_id,
                        severity=QCSeverity.CRITICAL,
                        category="source_verification",
                        description="Content checksum mismatch — stored content may be corrupted or tampered",
                        recommendation="Re-download the document from its primary source",
                        evidence={
                            "stored_checksum": stored_checksum,
                            "actual_checksum": actual_checksum,
                        },
                    ))

        return findings


# ---------------------------------------------------------------------------
# Agent 2: Classification Accuracy
# ---------------------------------------------------------------------------

# Legal terminology signals for each taxonomy category
CATEGORY_SIGNALS: Dict[str, List[str]] = {
    "01": ["constitution", "constitutional", "article", "fundamental rights",
           "directive principles", "supreme law", "sovereignty"],
    "02": ["act", "legislative instrument", "regulation", "bye-law",
           "parliament", "enacted", "gazette", "executive instrument"],
    "03": ["judgment", "ruling", "appellant", "respondent", "plaintiff",
           "defendant", "appeal", "held that", "court", "jsc", "ratio decidendi"],
    "04": ["procedure", "rules of court", "evidence", "civil procedure",
           "criminal procedure", "motion", "pleadings", "writ", "summons"],
    "05": ["tax", "labour", "land", "commercial", "family", "mining",
           "intellectual property", "environmental", "insurance", "banking"],
    "06": ["treaty", "convention", "protocol", "ratified", "international",
           "ecowas", "african union", "united nations", "bilateral", "extradition"],
    "07": ["commentary", "analysis", "article", "journal", "textbook",
           "law reform", "academic", "opinion", "critique", "theory"],
}

OVERRULING_TERMS = ["overruled", "overruling", "no longer good law",
                    "reversed", "set aside", "quashed"]


class ClassificationAccuracyAgent:
    """Verify that documents are correctly classified into the 7 taxonomy categories.

    Uses keyword signal analysis to check if the document content matches its
    assigned category. If signals from a different category dominate, flags
    for review.
    """

    NAME = "classification_accuracy"

    def verify(self, document_id: str, metadata: Dict[str, Any],
               content: Optional[bytes] = None) -> List[QCFinding]:
        findings: List[QCFinding] = []

        assigned_category = metadata.get("taxonomy_category", "")
        if not assigned_category:
            findings.append(QCFinding(
                agent=self.NAME,
                document_id=document_id,
                severity=QCSeverity.CRITICAL,
                category="classification",
                description="Document has no taxonomy_category assigned",
                recommendation="Assign one of the 7 legal taxonomy categories (01-07)",
            ))
            return findings

        # If we have content, do signal analysis
        if content is not None and assigned_category in CATEGORY_SIGNALS:
            text = content.decode("utf-8", errors="ignore").lower()
            results = self._score_categories(text)

            best_category = max(results, key=results.get)
            best_score = results[best_category]
            assigned_score = results.get(assigned_category, 0)

            # If assigned category score is 0 but another has strong signal
            if assigned_score == 0 and best_score >= 3:
                findings.append(QCFinding(
                    agent=self.NAME,
                    document_id=document_id,
                    severity=QCSeverity.WARNING,
                    category="classification",
                    description=f"Document classified as '{assigned_category}' but content signals strongest for '{best_category}' (score: {best_score})",
                    recommendation=f"Review classification — consider reassigning to '{best_category}'",
                    evidence={"category_signals": results},
                ))
            # If another category scores substantially higher
            elif (best_score - assigned_score) >= 4 and best_category != assigned_category:
                findings.append(QCFinding(
                    agent=self.NAME,
                    document_id=document_id,
                    severity=QCSeverity.WARNING,
                    category="classification",
                    description=f"Document classified as '{assigned_category}' but '{best_category}' scores much higher ({best_score} vs {assigned_score})",
                    recommendation="Review and likely reclassify",
                    evidence={"assigned_score": assigned_score, "best_score": best_score,
                              "best_category": best_category},
                ))

        return findings

    def _score_categories(self, text: str) -> Dict[str, int]:
        """Count keyword signals per category."""
        scores: Dict[str, int] = {}
        for category, signals in CATEGORY_SIGNALS.items():
            score = sum(1 for s in signals if s in text)
            scores[category] = score
        return scores


# ---------------------------------------------------------------------------
# Agent 3: Duplicate Detection
# ---------------------------------------------------------------------------

class DuplicateDetectionAgent:
    """Detect near-duplicate documents using cosine similarity.

    17O-D requirement: threshold of 0.85 cosine similarity.

    Uses TF (term frequency) vectors over word bigrams for efficient
    comparison. For large document sets, this can be batched.
    """

    NAME = "duplicate_detection"
    SIMILARITY_THRESHOLD = 0.85  # 17O-D requirement

    def find_duplicates(
        self,
        document_id: str,
        content: bytes,
        existing_docs: List[Tuple[str, bytes]],
    ) -> List[QCFinding]:
        """Check if document_id is a near-duplicate of any existing document.

        Args:
            document_id: ID of document being checked
            content: Content bytes of the document
            existing_docs: List of (doc_id, content_bytes) for existing docs
        """
        findings: List[QCFinding] = []

        if not content:
            return findings

        target_vec = self._tokenize_to_vector(content)

        for (other_id, other_content) in existing_docs:
            if other_id == document_id:
                continue
            if not other_content:
                continue

            other_vec = self._tokenize_to_vector(other_content)
            similarity = self._cosine_similarity(target_vec, other_vec)

            if similarity >= self.SIMILARITY_THRESHOLD:
                findings.append(QCFinding(
                    agent=self.NAME,
                    document_id=document_id,
                    severity=QCSeverity.WARNING,
                    category="duplicate",
                    description=f"Document is {similarity:.1%} similar to '{other_id}' (threshold: {self.SIMILARITY_THRESHOLD:.0%})",
                    recommendation="Review both documents — they may be duplicates or different versions of the same legal instrument",
                    evidence={
                        "similarity_score": round(similarity, 4),
                        "similar_document_id": other_id,
                        "threshold": self.SIMILARITY_THRESHOLD,
                    },
                ))

        return findings

    def _tokenize_to_vector(self, content: bytes) -> Dict[str, int]:
        """Tokenize content into word bigram frequency vector."""
        text = content.decode("utf-8", errors="ignore").lower()
        # Normalize whitespace
        text = re.sub(r"\s+", " ", text)
        words = text.split()

        # Use bigrams for better discrimination
        bigrams = []
        for i in range(len(words) - 1):
            bigrams.append(f"{words[i]} {words[i+1]}")

        # Also include single words for coverage
        all_tokens = words + bigrams

        return Counter(all_tokens)

    def _cosine_similarity(self, vec1: Dict[str, int], vec2: Dict[str, int]) -> float:
        """Compute cosine similarity between two frequency vectors."""
        if not vec1 or not vec2:
            return 0.0

        # Dot product
        dot = sum(vec1.get(k, 0) * vec2.get(k, 0) for k in set(vec1) | set(vec2))

        # Magnitudes
        mag1 = sqrt(sum(v ** 2 for v in vec1.values()))
        mag2 = sqrt(sum(v ** 2 for v in vec2.values()))

        if mag1 == 0 or mag2 == 0:
            return 0.0

        return dot / (mag1 * mag2)


# ---------------------------------------------------------------------------
# Agent 4: Outdated Law Detection
# ---------------------------------------------------------------------------

# Known overruled/repealed Ghanaian legislation and cases for reference
KNOWN_OUTDATED_REFERENCES: Dict[str, str] = {
    # Legislation
    "Act 29": "Repealed by NRCD 5 (Criminal Code)",
    "Act 30": "Repealed — Criminal Procedure Code, 1960",
    # Cases known to be overruled
    # (Ghana-specific references would be populated from the knowledge base)
}

OUTDATED_STATUS_KEYWORDS: Dict[str, List[str]] = {
    "overruled": ["overruled by", "reversed by", "set aside by",
                  "no longer good law", "not followed"],
    "amended": ["amended by", "as amended by", "modified by"],
    "repealed": ["repealed by", "revoked by", "abolished"],
    "superseded": ["superseded by", "replaced by"],
}

CURRENT_YEAR = datetime.now().year
# Legislation older than this triggers a stale check
STALE_YEAR_THRESHOLD = 20  # Documents > 20 years old get flagged for review


class OutdatedLawAgent:
    """Flag potentially outdated legal documents.

    Checks:
      - Document status metadata (overruled/amended/repealed)
      - Known outdated references
      - Keyword detection for "overruled by", "amended by", etc.
      - Age-based staleness (legislation > 20 years without amendment)
    """

    NAME = "outdated_law"

    def verify(self, document_id: str, metadata: Dict[str, Any],
               content: Optional[bytes] = None) -> List[QCFinding]:
        findings: List[QCFinding] = []

        # Check document status
        doc_status = metadata.get("status", "")
        if doc_status in ("overruled", "repealed", "superseded"):
            findings.append(QCFinding(
                agent=self.NAME,
                document_id=document_id,
                severity=QCSeverity.WARNING,
                category="outdated",
                description=f"Document status is '{doc_status}' — it may no longer be good law",
                recommendation="Ensure users are warned this document is not current authority",
                evidence={"status": doc_status},
            ))

        # Check known outdated references by citation
        citation = metadata.get("citation", {})
        if isinstance(citation, dict):
            citation_text = citation.get("citation_text", "")
        elif isinstance(citation, str):
            citation_text = citation
        else:
            citation_text = ""

        if citation_text in KNOWN_OUTDATED_REFERENCES:
            findings.append(QCFinding(
                agent=self.NAME,
                document_id=document_id,
                severity=QCSeverity.CRITICAL,
                category="outdated",
                description=f"Known outdated: {KNOWN_OUTDATED_REFERENCES[citation_text]}",
                recommendation="Replace with current version or mark as historical reference only",
                evidence={"citation": citation_text,
                          "known_status": KNOWN_OUTDATED_REFERENCES[citation_text]},
            ))

        # Content-based keyword detection
        if content is not None:
            text = content.decode("utf-8", errors="ignore").lower()
            for status, keywords in OUTDATED_STATUS_KEYWORDS.items():
                for kw in keywords:
                    if kw in text:
                        findings.append(QCFinding(
                            agent=self.NAME,
                            document_id=document_id,
                            severity=QCSeverity.INFO,
                            category="outdated",
                            description=f"Text contains '{kw}' — document may reference {status} law",
                            recommendation="Verify if the referenced law is still current and document reflects amendments",
                            evidence={"keyword_found": kw, "potential_status": status},
                        ))
                        break  # One per status category max

        # Age-based check for legislation
        doc_type = metadata.get("document_type", "")
        year = metadata.get("year")
        if doc_type == "legislation" and year and (CURRENT_YEAR - year) > STALE_YEAR_THRESHOLD:
            # Check if it has been amended
            amended_by = metadata.get("amended_by", [])
            if not amended_by:
                findings.append(QCFinding(
                    agent=self.NAME,
                    document_id=document_id,
                    severity=QCSeverity.INFO,
                    category="outdated",
                    description=f"Legislation from {year} ({CURRENT_YEAR - year} years old) has no recorded amendments",
                    recommendation="Research whether this legislation has been subsequently amended or remains in original form",
                    evidence={"year": year, "age_years": CURRENT_YEAR - year},
                ))

        return findings


# ---------------------------------------------------------------------------
# QC Orchestrator
# ---------------------------------------------------------------------------

class QCController:
    """Orchestrate all four QC agents against a document and produce a report."""

    def __init__(self):
        self.source_agent = SourceVerificationAgent()
        self.classification_agent = ClassificationAccuracyAgent()
        self.duplicate_agent = DuplicateDetectionAgent()
        self.outdated_agent = OutdatedLawAgent()

    def run_full_qc(
        self,
        document_id: str,
        metadata: Dict[str, Any],
        content: Optional[bytes] = None,
        existing_documents: Optional[List[Tuple[str, bytes]]] = None,
    ) -> QCReport:
        """Run all four QC agents and produce an aggregate report."""
        all_findings: List[QCFinding] = []

        # Agent 1: Source Verification
        source_findings = self.source_agent.verify(document_id, metadata, content)
        all_findings.extend(source_findings)

        # Agent 2: Classification Accuracy
        class_findings = self.classification_agent.verify(document_id, metadata, content)
        all_findings.extend(class_findings)

        # Agent 3: Duplicate Detection
        if content and existing_documents:
            dup_findings = self.duplicate_agent.find_duplicates(
                document_id, content, existing_documents
            )
            all_findings.extend(dup_findings)

        # Agent 4: Outdated Law
        outdated_findings = self.outdated_agent.verify(document_id, metadata, content)
        all_findings.extend(outdated_findings)

        # Determine overall status
        criticals = [f for f in all_findings if f.severity == QCSeverity.CRITICAL]
        warnings = [f for f in all_findings if f.severity == QCSeverity.WARNING]

        if criticals:
            status = QCStatus.FAILED
        elif warnings:
            status = QCStatus.NEEDS_REVIEW
        else:
            status = QCStatus.PASSED

        # Count agent results
        agents_with_issues = set(f.agent for f in all_findings)
        all_agents = {"source_verification", "classification_accuracy",
                       "duplicate_detection", "outdated_law"}

        return QCReport(
            document_id=document_id,
            status=status,
            findings=all_findings,
            passed_agents=len(all_agents - agents_with_issues),
            failed_agents=len(agents_with_issues),
        )

    def batch_qc(
        self,
        documents: List[Tuple[str, Dict[str, Any], Optional[bytes]]],
    ) -> List[QCReport]:
        """Run QC on a batch of documents, with duplicate detection across the batch."""
        # Build existing documents list for duplicate detection
        docs_with_content = [
            (doc_id, content) for doc_id, _, content in documents
            if content is not None
        ]

        reports = []
        for doc_id, metadata, content in documents:
            report = self.run_full_qc(doc_id, metadata, content, docs_with_content)
            reports.append(report)

        return reports


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Ghana Legal KB — Quality Control")
    sub = parser.add_subparsers(dest="command")

    check_parser = sub.add_parser("check", help="Run QC on a document")
    check_parser.add_argument("--doc-id", required=True)
    check_parser.add_argument("--metadata", help="JSON metadata string")
    check_parser.add_argument("--content-file", help="Path to document text for content analysis")

    args = parser.parse_args()

    if args.command == "check":
        controller = QCController()
        metadata = json.loads(args.metadata) if args.metadata else {}
        content = Path(args.content_file).read_bytes() if args.content_file else None

        report = controller.run_full_qc(args.doc_id, metadata, content)
        print(json.dumps(report.to_dict(), indent=2, default=str))
    else:
        parser.print_help()
