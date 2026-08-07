"""Phase 19L: Legal Trust Engine.

Assigns trust scores to:
  1. Sources — per-source reliability (Tier 1 > Tier 3)
  2. Documents — per-document confidence from QC agents
  3. Citations — verification against knowledge graph
  4. AI Responses — overall confidence based on source quality + citation coverage

Used by the evidence-first AI pipeline to gate responses.
Auto-flags responses with confidence < 0.7 for operator review.
"""

import json
from pathlib import Path
from typing import Optional, Dict, Any, List

from .permanent import get_connection as get_perm_connection
from .permanent.store import get_source, get_document


class TrustEngine:
    """Legal trust scoring engine."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path

    # ── Source Trust ────────────────────────────────────────────────────

    def score_source(self, source_id: str) -> Dict[str, Any]:
        """Score the reliability of a legal source.

        Scoring factors:
          - Tier (1=official > 2=recognized > 3=secondary)
          - Reliability score from operator feedback
          - Status (active/broken)
        """
        source = get_source(source_id)
        if not source:
            return {"source_id": source_id, "score": 0.0, "reason": "Source not found"}

        tier = source.get("tier", 3)
        reliability = source.get("reliability_score", 1.0)
        status = source.get("status", "active")

        # Base score from tier
        tier_scores = {1: 0.90, 2: 0.60, 3: 0.35}
        base = tier_scores.get(tier, 0.30)

        # Adjust by operator feedback
        score = base * reliability

        # Penalty for broken sources
        if status != "active":
            score *= 0.5

        return {
            "source_id": source_id,
            "name": source.get("domain", "unknown"),
            "tier": tier,
            "reliability": reliability,
            "score": round(score, 3),
            "status": status,
        }

    def score_all_sources(self) -> List[Dict[str, Any]]:
        """Score all registered sources."""
        with get_perm_connection(self.db_path) as conn:
            rows = conn.execute("SELECT id FROM sources").fetchall()
        return [self.score_source(r["id"]) for r in rows]

    # ── Document Trust ──────────────────────────────────────────────────

    def score_document(self, document_id: str) -> Dict[str, Any]:
        """Score the confidence of a document based on source quality and metadata.

        Factors:
          - Source trust score
          - Review status (approved > pending > rejected)
          - Copyright classification
          - Jurisdiction match (Ghana = full score)
        """
        doc = get_document(document_id)
        if not doc:
            return {"document_id": document_id, "score": 0.0, "reason": "Document not found"}

        # Source trust
        source_score = 0.5
        if doc.get("source_id"):
            src_result = self.score_source(doc["source_id"])
            source_score = src_result["score"]

        # Review status bonus/penalty
        status_multiplier = {
            "approved": 1.0,
            "pending": 0.4,
            "rejected": 0.0,
        }
        status_mult = status_multiplier.get(doc.get("review_status", "pending"), 0.3)

        # Copyright classification
        copyright_scores = {
            "official_public_access": 1.0,
            "public_domain": 0.9,
            "open_license": 0.8,
            "unknown": 0.3,
            "copyright_protected": 0.2,
        }
        copyright_mult = copyright_scores.get(
            doc.get("copyright_classification", "unknown"), 0.3
        )

        # Jurisdiction match
        jurisdiction_mult = 1.0 if doc.get("jurisdiction") == "Ghana" else 0.5

        # Combined score
        score = source_score * status_mult * copyright_mult * jurisdiction_mult

        return {
            "document_id": document_id,
            "title": doc.get("title", "Unknown"),
            "source_score": round(source_score, 3),
            "review_status": doc.get("review_status"),
            "score": round(score, 3),
            "confidence_level": self._confidence_label(score),
        }

    def score_documents(
        self, document_ids: List[str]
    ) -> List[Dict[str, Any]]:
        """Score multiple documents."""
        return [self.score_document(did) for did in document_ids]

    # ── Citation Trust ──────────────────────────────────────────────────

    def verify_citation(
        self, citing_doc_id: str, cited_doc_id: str
    ) -> Dict[str, Any]:
        """Verify that a citation between two documents is valid.

        Checks:
          - Both documents exist in the permanent store
          - The citation relationship is recorded
          - Citation confidence from KG
        """
        citing_doc = get_document(citing_doc_id)
        cited_doc = get_document(cited_doc_id)

        if not citing_doc or not cited_doc:
            return {
                "citing_doc_id": citing_doc_id,
                "cited_doc_id": cited_doc_id,
                "valid": False,
                "reason": "One or both documents not found",
                "score": 0.0,
            }

        # Check if citation exists in permanent store
        with get_perm_connection(self.db_path) as conn:
            row = conn.execute(
                """SELECT * FROM citations
                   WHERE source_doc_id = ? AND target_doc_id = ?""",
                (citing_doc_id, cited_doc_id),
            ).fetchone()

        if not row:
            return {
                "citing_doc_id": citing_doc_id,
                "cited_doc_id": cited_doc_id,
                "valid": False,
                "reason": "Citation relationship not recorded",
                "citing_title": citing_doc.get("title"),
                "cited_title": cited_doc.get("title"),
                "score": 0.0,
            }

        row_dict = dict(row)
        confidence = row_dict.get("confidence", 1.0)

        # Also check KG relationships
        from .knowledge.engine import KnowledgeEngine
        kg = KnowledgeEngine(self.db_path)

        citing_entity = kg._get_entity_for_document(citing_doc_id)
        cited_entity = kg._get_entity_for_document(cited_doc_id)

        kg_verified = False
        if citing_entity and cited_entity:
            rels = kg.get_relationships(citing_entity["id"], direction="outgoing")
            kg_verified = any(
                r["target_entity_id"] == cited_entity["id"] for r in rels
            )

        score = confidence * (1.0 if kg_verified else 0.7)

        return {
            "citing_doc_id": citing_doc_id,
            "cited_doc_id": cited_doc_id,
            "valid": True,
            "confidence": confidence,
            "kg_verified": kg_verified,
            "citing_title": citing_doc.get("title"),
            "cited_title": cited_doc.get("title"),
            "score": round(score, 3),
        }

    def verify_citations(
        self, citation_pairs: List[tuple]
    ) -> List[Dict[str, Any]]:
        """Verify multiple citation pairs. Each pair is (citing_id, cited_id)."""
        return [self.verify_citation(citing, cited) for citing, cited in citation_pairs]

    # ── AI Response Trust ───────────────────────────────────────────────

    def score_ai_response(
        self,
        retrieved_authorities: List[Dict[str, Any]],
        citations_used: List[str],
        model_confidence: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Score the trustworthiness of an AI legal response.

        Factors:
          - Quality of retrieved authorities (source tier, document confidence)
          - Citation coverage: are claims actually backed by cited sources?
          - Model confidence (if provided by the AI model)

        Returns a score 0-1 and a flag if below threshold.
        """
        if not retrieved_authorities:
            return {
                "score": 0.0,
                "confidence": "none",
                "flag_for_review": True,
                "reason": "No authorities retrieved",
            }

        # Score each retrieved authority
        doc_scores = []
        for auth in retrieved_authorities:
            doc_id = auth.get("doc_id")
            if doc_id:
                doc_score = self.score_document(doc_id)
                doc_scores.append(doc_score["score"])

        authority_score = sum(doc_scores) / len(doc_scores) if doc_scores else 0.3

        # Citation coverage: how many retrieved authorities were actually cited?
        cited_ids = set(citations_used)
        retrieved_ids = {a.get("doc_id") for a in retrieved_authorities if a.get("doc_id")}
        coverage = len(cited_ids & retrieved_ids) / len(retrieved_ids) if retrieved_ids else 0.0

        # Combined score
        combined = authority_score * 0.6 + coverage * 0.4
        if model_confidence is not None:
            combined = combined * 0.7 + model_confidence * 0.3

        flag = combined < 0.7

        return {
            "score": round(combined, 3),
            "authority_score": round(authority_score, 3),
            "citation_coverage": round(coverage, 3),
            "model_confidence": model_confidence,
            "confidence": self._confidence_label(combined),
            "flag_for_review": flag,
            "authorities_count": len(retrieved_authorities),
            "citations_count": len(citations_used),
        }

    # ── Aggregate ───────────────────────────────────────────────────────

    def get_trust_summary(self) -> Dict[str, Any]:
        """Get a summary of trust scores across the entire Legal Brain."""
        source_scores = self.score_all_sources()
        avg_source = sum(s["score"] for s in source_scores) / len(source_scores) if source_scores else 0

        return {
            "sources_scored": len(source_scores),
            "average_source_score": round(avg_source, 3),
            "source_breakdown": {
                "tier_1": len([s for s in source_scores if s["tier"] == 1]),
                "tier_2": len([s for s in source_scores if s["tier"] == 2]),
                "tier_3": len([s for s in source_scores if s["tier"] == 3]),
            },
        }

    @staticmethod
    def _confidence_label(score: float) -> str:
        if score >= 0.9:
            return "high"
        elif score >= 0.7:
            return "medium"
        elif score >= 0.4:
            return "low"
        else:
            return "very_low"
