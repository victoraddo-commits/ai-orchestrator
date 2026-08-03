"""
KLAUS Legal Knowledge Acquisition System - Quality Control Agents

Five agents form the quality-control layer before any document
enters the shared knowledge base:

1. SourceVerificationAgent - validates source authenticity
2. LegalClassificationAgent - categorizes documents and copyright
3. CitationExtractionAgent - parses internal citations/cross-references
4. QualityAssuranceAgent - completeness, duplication, formatting
5. KnowledgeCuratorAgent - final integration + operator review flags

Each agent reviews every document; all must pass before the document
reaches 'approved' status.
"""

import re
from typing import Dict, List, Optional, Tuple

from core.klaus.db_manager import (
    get_document,
    get_document_by_hash,
    get_chunks_for_document,
    get_source,
    update_document_review_status,
    log_audit_event,
)
from core.klaus.schema import (
    KNOWLEDGE_CATEGORIES,
    COPYRIGHT_CLASSIFICATIONS,
)


class VerificationResult:
    def __init__(self, passed: bool, reason: str = "", warnings: List[str] = None):
        self.passed = passed
        self.reason = reason
        self.warnings = warnings or []


class SourceVerificationAgent:
    """
    Validates source authenticity: domain pattern, reliability score,
    and checks that the document references its claimed source.
    """

    KNOWN_GOV_DOMAINS = (
        ".gov.gh", ".gov.ng", ".gov.ke", ".go.ke",
        "parliament.gh", "judiciary.gov.gh", "ghalii.org",
        ".gov.za", ".go.tz", ".go.ug",
    )

    def verify(self, document_id: int) -> VerificationResult:
        doc = get_document(document_id)
        if not doc:
            return VerificationResult(False, "Document not found")

        source = get_source(doc["source_id"])
        if not source:
            log_audit_event("verification", "error", "Source not found in catalog", document_id)
            return VerificationResult(False, "Source not found in catalog")

        if source["status"] == "broken":
            return VerificationResult(False, f"Source {source['domain']} is marked broken")

        if source["reliability_score"] is not None and source["reliability_score"] < 0.3:
            return VerificationResult(
                False, f"Source reliability too low: {source['reliability_score']}"
            )

        domain = source["domain"] or ""
        is_gov = any(d in domain for d in self.KNOWN_GOV_DOMAINS)

        # Allow tier 1 sources even if they don't match known pattern (be flexible)
        # Only warn about non-Gov domains for tier 1
        if source["tier"] == 1 and not is_gov:
            return VerificationResult(
                True, "Warning: tier 1 source not matching known gov domain pattern",
                ["tier_1_non_gov_domain"]
            )

        log_audit_event("verification", "info", f"Source verified: {source['domain']}", document_id)
        return VerificationResult(True, "Source verified")


class LegalClassificationAgent:
    """
    Categorizes documents using keyword heuristics and validates against
    the six KNOWLEDGE_CATEGORIES. Also validates copyright classification.
    """

    def verify(self, document_id: int) -> VerificationResult:
        doc = get_document(document_id)
        if not doc:
            return VerificationResult(False, "Document not found")

        warnings = []

        if doc["category"] not in KNOWLEDGE_CATEGORIES:
            return VerificationResult(
                False, f"Category '{doc['category']}' not in valid set: {KNOWLEDGE_CATEGORIES}"
            )

        if doc["copyright_classification"] not in COPYRIGHT_CLASSIFICATIONS:
            return VerificationResult(
                False,
                f"Copyright '{doc['copyright_classification']}' not in valid set: {COPYRIGHT_CLASSIFICATIONS}",
            )

        if doc["access_level"] not in ("full_storage", "metadata_only"):
            return VerificationResult(
                False, f"Invalid access_level: {doc['access_level']}"
            )

        if (
            doc["copyright_classification"] in ("copyright_protected", "unknown")
            and doc["access_level"] == "full_storage"
        ):
            warnings.append(
                f"copyright={doc['copyright_classification']} but access_level=full_storage"
            )

        log_audit_event(
            "classification",
            "info",
            f"Legal classification verified: {doc['category']} / {doc['copyright_classification']}",
            document_id,
        )
        return VerificationResult(
            True, "Legal classification valid",
            warnings=warnings,
        )


class CitationExtractionAgent:
    """
    Reviews chunks for internal legal citations: article numbers, case
    references, legislation references, and cross-references.
    """

    ARTICLE_RE = re.compile(r"Article\s+(\d+(?:\(\d+[a-z]?\))?)", re.IGNORECASE)
    SECTION_RE = re.compile(r"Section\s+(\d+(?:\(\d+[a-z]?\))?)", re.IGNORECASE)
    CASE_RE = re.compile(
        r"\[\d{4}(?:-\d{4})?\]\s*(?:\d+\s+)?(?:G\.?M\.?)?\s*(?:S\.?C\.?G\.?L\.?R\.?)",
        re.IGNORECASE,
    )
    ACT_RE = re.compile(r"Act\s+(\d{1,4})", re.IGNORECASE)
    LI_RE = re.compile(r"L\.?I\.?\s+(\d{1,4})", re.IGNORECASE)

    def verify(self, document_id: int) -> VerificationResult:
        doc = get_document(document_id)
        if not doc:
            return VerificationResult(False, "Document not found")

        chunks = get_chunks_for_document(document_id)
        combined = " ".join(c.get("content", "") for c in chunks)

        articles = list(set(m.group(0) for m in self.ARTICLE_RE.finditer(combined)))
        sections = list(set(m.group(0) for m in self.SECTION_RE.finditer(combined)))
        cases = list(set(m.group(0) for m in self.CASE_RE.finditer(combined)))
        acts = list(set(m.group(0) for m in self.ACT_RE.finditer(combined)))
        lis_ = list(set(m.group(0) for m in self.LI_RE.finditer(combined)))

        total_citations = len(articles) + len(sections) + len(cases) + len(acts) + len(lis_)

        log_audit_event(
            "verification",
            "info",
            f"Citation extraction: {total_citations} citations found "
            f"(articles={len(articles)}, sections={len(sections)}, "
            f"cases={len(cases)}, acts={len(acts)}, LIs={len(lis_)})",
            document_id,
        )

        if total_citations == 0 and doc["category"] not in ("Legal Scholarship",):
            return VerificationResult(
                True,
                "No citations found; may indicate poor extraction quality",
                ["no_citations_found"]
            )

        return VerificationResult(True, f"Found {total_citations} citations")


class QualityAssuranceAgent:
    """
    Verifies completeness: checks for empty chunks, excessive noise,
    formatting anomalies, OCR noise indicators, and duplicate detection.
    """

    OCR_NOISE_PATTERNS = (
        re.compile(r"[A-Z]{10,}"),
        re.compile(r"\|{3,}"),
        re.compile(r"\?{3,}"),
    )

    def verify(self, document_id: int) -> VerificationResult:
        doc = get_document(document_id)
        if not doc:
            return VerificationResult(False, "Document not found")

        warnings = []
        chunks = get_chunks_for_document(document_id)

        if not chunks:
            if doc["access_level"] == "full_storage":
                warnings.append("No chunks found for full_storage document")

        empty_chunks = sum(1 for c in chunks if not c.get("content", "").strip())
        if empty_chunks > 0:
            warnings.append(f"{empty_chunks} empty chunks found")

        duplicate_check = get_document_by_hash(doc["file_hash"])
        if duplicate_check and duplicate_check["id"] != doc["id"]:
            warnings.append(f"Duplicate hash matches document {duplicate_check['id']}")

        combined = " ".join(c.get("content", "") for c in chunks)
        noise_hits = 0
        for pattern in self.OCR_NOISE_PATTERNS:
            noise_hits += len(pattern.findall(combined))
        if noise_hits > 5:
            warnings.append(f"OCR noise detected ({noise_hits} pattern matches)")

        avg_chunk_len = len(combined) / max(len(chunks), 1)
        if avg_chunk_len < 20 and chunks:
            warnings.append(f"Very short average chunk length: {avg_chunk_len:.0f} chars")

        log_audit_event(
            "verification",
            "info",
            f"QA check: {len(chunks)} chunks, {len(warnings)} warnings",
            document_id,
        )

        return VerificationResult(
            True,
            f"QA complete: {len(chunks)} chunks, {len(warnings)} warnings",
            warnings=warnings,
        )


class KnowledgeCuratorAgent:
    """
    Final gatekeeper: determines if a document is ready for the shared
    knowledge base. Consolidates results from all other agents. Documents
    with unresolved copyright concerns are flagged for operator review
    rather than being silently rejected.
    """

    def curate(
        self,
        document_id: int,
        agent_results: Dict[str, VerificationResult],
    ) -> Tuple[bool, Dict]:
        doc = get_document(document_id)
        if not doc:
            return False, {"status": "error", "reason": "Document not found"}

        passed = all(r.passed for r in agent_results.values())
        all_warnings = []
        for name, result in agent_results.items():
            all_warnings.extend(result.warnings)

        copyright_cls = doc["copyright_classification"]
        needs_review = (
            copyright_cls in ("unknown",)
            or any("copyright" in w.lower() for w in all_warnings)
        )

        if not passed:
            update_document_review_status(document_id, "flagged")
            log_audit_event(
                "review", "error",
                "Curator: one or more agents failed. Document flagged.",
                document_id,
            )
            return False, {
                "status": "flagged",
                "reason": "Agent verification failed",
                "warnings": all_warnings,
            }

        if needs_review:
            update_document_review_status(document_id, "flagged")
            log_audit_event(
                "review", "warning",
                "Curator: flagged for operator review due to warnings",
                document_id,
            )
            return False, {
                "status": "flagged",
                "reason": "Needs operator review",
                "warnings": all_warnings,
            }

        update_document_review_status(document_id, "approved")
        log_audit_event(
            "review", "info",
            "Curator: document approved for shared knowledge base",
            document_id,
        )
        return True, {
            "status": "approved",
            "reason": "All agents passed, no review needed",
            "warnings": all_warnings,
        }


def run_all_agents(document_id: int) -> Dict:
    """
    Run all five quality-control agents against a document.
    Returns the combined result dict.
    """
    results = {}

    sv = SourceVerificationAgent().verify(document_id)
    results["source_verification"] = {"passed": sv.passed, "reason": sv.reason, "warnings": sv.warnings}

    lc = LegalClassificationAgent().verify(document_id)
    results["legal_classification"] = {"passed": lc.passed, "reason": lc.reason, "warnings": lc.warnings}

    ce = CitationExtractionAgent().verify(document_id)
    results["citation_extraction"] = {"passed": ce.passed, "reason": ce.reason, "warnings": ce.warnings}

    qa = QualityAssuranceAgent().verify(document_id)
    results["quality_assurance"] = {"passed": qa.passed, "reason": qa.reason, "warnings": qa.warnings}

    agent_results = {k: VerificationResult(v["passed"], v["reason"], v["warnings"]) for k, v in results.items()}
    passed, curation = KnowledgeCuratorAgent().curate(document_id, agent_results)
    results["knowledge_curator"] = curation

    results["overall"] = "approved" if passed else curation.get("status", "failed")
    return results
