"""
KLAUS Legal Knowledge Acquisition System - Quality Control Agents

Six agents form the quality-control layer before any document
enters the shared knowledge base:

1. SourceVerificationAgent - validates source authenticity
2. LegalClassificationAgent - categorizes documents and copyright
3. TierClassificationAgent - maps documents to 16-tier priority system
4. CitationExtractionAgent - parses internal citations/cross-references
5. QualityAssuranceAgent - completeness, duplication, formatting
6. KnowledgeCuratorAgent - final integration + operator review flags

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
    ACQUISITION_TIERS,
    AUTHORITY_TYPES,
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


class TierClassificationAgent:
    """
    Maps documents to the 16-tier Ghana Legal Corpus Acquisition priority system.

    T1  Constitutional Law          T9  Property & Land Law
    T2  Primary Legislation (Acts)  T10 Family & Succession Law
    T3  Subsidiary Legislation      T11 Intellectual Property Law
    T4  Judicial Precedents         T12 Technology & Data Law
    T5  Criminal Law                T13 Banking & Finance Law
    T6  Commercial & Contract Law   T14 Government & Administrative
    T7  Employment & Labour Law     T15 Case Law & Digests
    T8  Tax & Revenue Law           T16 Official Publications
    """

    # ── Tier classification signals (keyword → tier_number) ─────────
    # Order matters: more specific patterns checked first.
    TIER_SIGNALS: List[Tuple[str, int]] = [
        # T1: Constitutional
        (r"\bconstitution\b", 1),
        (r"\bconstitutional\b", 1),
        # T3: Subsidiary Legislation (check before T2 since LI/CI are specific)
        (r"\bL\.?\s*I\.?\s+\d+", 3),
        (r"\bC\.?\s*I\.?\s+\d+", 3),
        (r"\bE\.?\s*I\.?\s+\d+", 3),
        (r"\blegislative\s+instrument\b", 3),
        (r"\bconstitutional\s+instrument\b", 3),
        # T2: Primary Legislation
        (r"\bAct\s+\d{3,4}\b", 2),
        (r"\bAct,\s+\d{4}\b", 2),
        (r"\bACT\s+\d{3}\b", 2),
        # T5: Criminal Law
        (r"\bcriminal\b", 5),
        (r"\boffence", 5),
        (r"\bnarcotic\b", 5),
        (r"\bpenal\b", 5),
        (r"\bpenalty\b", 5),
        (r"\bcommunity\s+service\b", 5),
        # T8: Tax & Revenue (before T2 so specific tax acts go here)
        (r"\btax\b", 8),
        (r"\brevenue\b", 8),
        (r"\bexcise\b", 8),
        (r"\bcustoms\b", 8),
        (r"\blevy\b", 8),
        (r"\bVAT\b", 8),
        (r"\bvalue\s+added\s+tax\b", 8),
        # T13: Banking & Finance (before T2 so banking acts go here)
        (r"\bbank\b", 13),
        (r"\bbanking\b", 13),
        (r"\bfinance\b", 13),
        (r"\bsecurit(?:y|ies)\b", 13),
        (r"\binvestment\b", 13),
        (r"\binsurance\b", 13),
        (r"\bvirtual\s+asset\b", 13),
        # T12: Technology & Data
        (r"\belectronic\b", 12),
        (r"\bcyber\b", 12),
        (r"\bdata\s+protection\b", 12),
        (r"\bvirtual\b", 12),
        # T7: Employment
        (r"\blabour\b", 7),
        (r"\bemployment\b", 7),
        (r"\bfactory\b", 7),
        (r"\bwork(?:er|men)", 7),
        # T6: Commercial
        (r"\bcommercial\b", 6),
        (r"\bcontract\b", 6),
        (r"\bsale\s+of\s+goods\b", 6),
        (r"\bhire\s+purchase\b", 6),
        (r"\bcompanies?\b", 6),
        (r"\bbusiness\s+name", 6),
        (r"\bpartnership", 6),
        (r"\bchartered\s+accountant", 6),
        # T9: Property
        (r"\bland\b", 9),
        (r"\bproperty\b", 9),
        (r"\bmortgage\b", 9),
        (r"\bconveyanc", 9),
        # T10: Family
        (r"\bfamily\b", 10),
        (r"\bmarriage\b", 10),
        (r"\bdivorce\b", 10),
        (r"\bsuccession\b", 10),
        (r"\bintestate\b", 10),
        (r"\bdomestic\s+violence\b", 10),
        (r"\bhuman\s+sexual\b", 10),
        # T11: Intellectual Property
        (r"\bcopyright\b", 11),
        (r"\bpatent\b", 11),
        (r"\btrademark\b", 11),
        (r"\bintellectual\s+property\b", 11),
        # T14: Government
        (r"\bgovernment\b", 14),
        (r"\bpublic\s+offic", 14),
        (r"\bparliament", 14),
        (r"\bcivil\s+service\b", 14),
        (r"\bgovernance\b", 14),
        (r"\bconduct\s+of\s+public\b", 14),
        # T4: Judicial (catch broader judicial terms after specifics)
        (r"\bcourt", 4),
        (r"\bjudicial\b", 4),
        (r"\btribunal\b", 4),
        (r"\bjudge\b", 4),
        (r"\blegal\s+profession\b", 4),
        (r"\blegal\s+education\b", 4),
        (r"\bextradition\b", 4),
    ]

    # Title-based supplementary signals (checked against title only)
    TITLE_SIGNALS: List[Tuple[str, int]] = [
        (r"\bConstitution\b", 1),
        (r"\bBill,?\s*\d{4}\b", 16),  # Bills → T16 Publications
        (r"\bBill\b", 16),
        (r"\bAmendment\b", 2),  # Amendments → T2 (primary legislation)
    ]

    # Category-to-tier fallback mapping
    CATEGORY_TIER_MAP = {
        "Constitutional Law": 1,
        "Legislation": 2,
        "Judiciary": 4,
        "Legal Procedure": 5,
        "International Law": 14,
        "Legal Scholarship": 16,
    }

    @classmethod
    def classify(cls, document_id: int) -> Tuple[int, str, str]:
        """Classify a document into a 16-tier priority.

        Returns (tier_number, authority_type, confidence_level).
        """
        doc = get_document(document_id)
        if not doc:
            return (16, "report", "low")  # Default: T16, report type

        title = doc.get("title", "")
        category = doc.get("category", "")
        source = get_source(doc.get("source_id")) if doc.get("source_id") else None

        # Step 1: Check chunks content for keyword signals
        chunks = get_chunks_for_document(document_id)
        combined = " ".join(c.get("content", "") for c in chunks)[:5000]  # First 5K chars
        combined_lower = combined.lower()

        tier_scores: Dict[int, float] = {}
        for pattern, tier_num in cls.TIER_SIGNALS:
            matches = re.findall(pattern, combined_lower, re.IGNORECASE)
            if matches:
                tier_scores[tier_num] = tier_scores.get(tier_num, 0) + len(matches)

        # Step 2: Check title signals
        for pattern, tier_num in cls.TITLE_SIGNALS:
            if re.search(pattern, title, re.IGNORECASE):
                tier_scores[tier_num] = tier_scores.get(tier_num, 0) + 2  # Title match = stronger

        # Step 3: If "Act" pattern matched but no other specific tier, it's T2
        if not tier_scores and re.search(r"\bAct\b", title, re.IGNORECASE):
            tier_scores[2] = 1

        # Step 4: Category fallback
        if not tier_scores and category in cls.CATEGORY_TIER_MAP:
            tier_scores[cls.CATEGORY_TIER_MAP[category]] = 0.5

        # Step 5: Pick highest-scoring tier
        if tier_scores:
            best_tier = max(tier_scores, key=tier_scores.get)
            best_score = tier_scores[best_tier]
            confidence = "high" if best_score >= 3 else "medium" if best_score >= 1 else "low"
        else:
            best_tier = 16  # Default to T16 Official Publications
            confidence = "low"

        # Determine authority_type
        authority_type = cls._determine_authority_type(best_tier, title, combined_lower)

        log_audit_event(
            "classification",
            "info",
            f"Tier classification: T{best_tier} ({ACQUISITION_TIERS[best_tier]['name']}) "
            f"authority_type={authority_type} confidence={confidence} "
            f"scores={dict(sorted(tier_scores.items(), key=lambda x: -x[1])[:5])}",
            document_id,
        )

        return (best_tier, authority_type, confidence)

    @classmethod
    def _determine_authority_type(cls, tier: int, title: str, content_lower: str) -> str:
        """Determine the authority_type for a Legal Authority Record."""
        combined = f"{title} {content_lower}".lower()

        if tier == 1:
            return "constitution"
        if tier == 16 and re.search(r"\bbill\b", combined):
            return "bill"
        if tier == 4 or tier == 15:
            return "case"
        if re.search(r"\bL\.?\s*I\.?\s+\d+|\bC\.?\s*I\.?\s+\d+|\bE\.?\s*I\.?\s+\d+", combined):
            return "regulation"
        if re.search(r"\bgazette\b", combined):
            return "gazette"
        if re.search(r"\breport\b", combined):
            return "report"
        if tier in (2, 3):
            return "statute"
        return "statute"  # Default for legislative documents

    def verify(self, document_id: int) -> VerificationResult:
        """Run tier classification and return VerificationResult."""
        tier_num, authority_type, confidence = self.classify(document_id)

        # Store tier on document via db_manager
        from core.klaus.db_manager import set_document_tier
        set_document_tier(document_id, tier_num)

        return VerificationResult(
            True,
            f"Classified as T{tier_num}: {ACQUISITION_TIERS[tier_num]['name']} "
            f"(authority_type={authority_type}, confidence={confidence})"
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
    Run all six quality-control agents against a document.
    Returns the combined result dict.
    """
    results = {}

    sv = SourceVerificationAgent().verify(document_id)
    results["source_verification"] = {"passed": sv.passed, "reason": sv.reason, "warnings": sv.warnings}

    lc = LegalClassificationAgent().verify(document_id)
    results["legal_classification"] = {"passed": lc.passed, "reason": lc.reason, "warnings": lc.warnings}

    tc = TierClassificationAgent().verify(document_id)
    results["tier_classification"] = {"passed": tc.passed, "reason": tc.reason, "warnings": tc.warnings}

    ce = CitationExtractionAgent().verify(document_id)
    results["citation_extraction"] = {"passed": ce.passed, "reason": ce.reason, "warnings": ce.warnings}

    qa = QualityAssuranceAgent().verify(document_id)
    results["quality_assurance"] = {"passed": qa.passed, "reason": qa.reason, "warnings": qa.warnings}

    agent_results = {k: VerificationResult(v["passed"], v["reason"], v["warnings"]) for k, v in results.items()}
    passed, curation = KnowledgeCuratorAgent().curate(document_id, agent_results)
    results["knowledge_curator"] = curation

    results["overall"] = "approved" if passed else curation.get("status", "failed")
    return results
