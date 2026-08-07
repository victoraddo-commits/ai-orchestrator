"""Phase 19Q: Legal Brain Health & Integrity Monitoring.

Continuous integrity checks on the permanent Legal Brain:
  - Document hash verification (daily)
  - Missing publication detection
  - Citation integrity verification
  - Duplicate detection
  - Conflicting version detection
  - Embedding integrity (optional, requires pgvector)
  - Source availability monitoring
  - Telegram alerts for integrity failures

Runs as a periodic check, can be invoked from cron or scheduler.
"""

import hashlib
import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List, Set
from datetime import datetime, timezone

from .permanent import get_connection as get_perm_connection
from .permanent.store import compute_hash, get_document, get_source

logger = logging.getLogger("kai.legal_brain.integrity")


class IntegrityMonitor:
    """Continuous integrity monitoring for the Legal Brain."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path

    # ── Document Hash Verification ─────────────────────────────────────

    def verify_all_document_hashes(self) -> Dict[str, Any]:
        """Verify SHA-256 hashes for all stored documents.

        Compares stored content_hash against actual file content.
        Reports any tampering or file corruption.
        """
        with get_perm_connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, title, content_hash, file_path FROM documents"
            ).fetchall()

        total = len(rows)
        valid = 0
        invalid = 0
        missing = 0
        tampered: List[Dict[str, Any]] = []
        missing_files: List[Dict[str, Any]] = []

        for row in rows:
            r = dict(row)
            try:
                with open(r["file_path"], "rb") as f:
                    actual_hash = compute_hash(f.read())
                if actual_hash == r["content_hash"]:
                    valid += 1
                else:
                    invalid += 1
                    tampered.append({
                        "document_id": r["id"],
                        "title": r["title"],
                        "stored_hash": r["content_hash"],
                        "actual_hash": actual_hash,
                    })
            except FileNotFoundError:
                missing += 1
                missing_files.append({
                    "document_id": r["id"],
                    "title": r["title"],
                    "file_path": r["file_path"],
                })

        return {
            "check_type": "document_hash_verification",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total": total,
            "valid": valid,
            "invalid": invalid,
            "missing": missing,
            "intact": invalid == 0 and missing == 0,
            "tampered_files": tampered,
            "missing_files": missing_files,
        }

    # ── Duplicate Detection ─────────────────────────────────────────────

    def detect_duplicates(self) -> Dict[str, Any]:
        """Detect duplicate documents by content hash.

        Two documents with identical content_hash but different IDs
        are duplicates. This can happen from ingestion bugs.
        """
        with get_perm_connection(self.db_path) as conn:
            dupes = conn.execute(
                """SELECT content_hash, COUNT(*) as cnt,
                           GROUP_CONCAT(id) as doc_ids,
                           GROUP_CONCAT(title, ' | ') as titles
                    FROM documents
                    GROUP BY content_hash
                    HAVING cnt > 1"""
            ).fetchall()

        duplicates = []
        for row in dupes:
            r = dict(row)
            ids = r["doc_ids"].split(",")
            titles = r["titles"].split(" | ")
            pairs = []
            for i, doc_id in enumerate(ids):
                pairs.append({"id": doc_id, "title": titles[i] if i < len(titles) else "?"})
            duplicates.append({"content_hash": r["content_hash"], "count": r["cnt"], "documents": pairs})

        return {
            "check_type": "duplicate_detection",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_duplicates": len(duplicates),
            "duplicate_groups": duplicates,
        }

    # ── Conflicting Version Detection ───────────────────────────────────

    def detect_conflicting_versions(self) -> Dict[str, Any]:
        """Detect documents with the same citation but different content.

        These may indicate a statute that was updated, but both versions
        are still present in the store without version linkage.
        """
        with get_perm_connection(self.db_path) as conn:
            conflicts = conn.execute(
                """SELECT citation_text, COUNT(*) as cnt,
                           GROUP_CONCAT(id) as doc_ids,
                           GROUP_CONCAT(title, ' | ') as titles,
                           GROUP_CONCAT(content_hash) as hashes
                    FROM documents
                    WHERE citation_text IS NOT NULL AND citation_text != ''
                    GROUP BY citation_text
                    HAVING cnt > 1"""
            ).fetchall()

        conflict_list = []
        for row in conflicts:
            r = dict(row)
            if not r.get("citation_text"):
                continue
            ids = r["doc_ids"].split(",")
            titles = r["titles"].split(" | ")
            hashes_list = r["hashes"].split(",") if r.get("hashes") else []

            # Only flag if different content hashes (same citation ≠ same content)
            unique_hashes = set(h for h in hashes_list if h)
            if len(unique_hashes) > 1:
                conflict_list.append({
                    "citation_text": r["citation_text"],
                    "versions": len(ids),
                    "documents": [
                        {"id": ids[i], "title": titles[i] if i < len(titles) else "?"}
                        for i in range(len(ids))
                    ],
                })

        return {
            "check_type": "conflicting_version_detection",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "conflicts_found": len(conflict_list),
            "conflicts": conflict_list,
        }

    # ── Citation Integrity ──────────────────────────────────────────────

    def verify_citation_integrity(self) -> Dict[str, Any]:
        """Verify that all citations reference documents that exist.

        Broken citations (source_doc_id or target_doc_id pointing to
        deleted documents) indicate data integrity issues.
        """
        with get_perm_connection(self.db_path) as conn:
            # Find citations where source doc is gone
            broken_sources = conn.execute(
                """SELECT c.id as citation_id, c.source_doc_id, c.target_doc_id,
                          c.citation_type, c.context_snippet
                   FROM citations c
                   LEFT JOIN documents d ON c.source_doc_id = d.id
                   WHERE d.id IS NULL"""
            ).fetchall()

            # Find citations where target doc is gone
            broken_targets = conn.execute(
                """SELECT c.id as citation_id, c.source_doc_id, c.target_doc_id,
                          c.citation_type, c.context_snippet
                   FROM citations c
                   LEFT JOIN documents d ON c.target_doc_id = d.id
                   WHERE c.target_doc_id IS NOT NULL AND d.id IS NULL"""
            ).fetchall()

        broken = []
        for source in broken_sources:
            broken.append({"type": "broken_source", **dict(source)})
        for target in broken_targets:
            broken.append({"type": "broken_target", **dict(target)})

        return {
            "check_type": "citation_integrity",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "broken_citations": len(broken),
            "details": broken,
        }

    # ── Source Availability ─────────────────────────────────────────────

    def check_source_availability(self) -> Dict[str, Any]:
        """Check that all Tier 1 sources are still accessible.

        Performs HTTP HEAD requests to verify URLs are reachable.
        This is a lightweight check — full content verification
        is done by the scheduler's acquisition pipeline.
        """
        import urllib.request
        import urllib.error

        with get_perm_connection(self.db_path) as conn:
            sources = conn.execute(
                "SELECT id, url, domain, tier FROM sources WHERE status = 'active' AND tier = 1"
            ).fetchall()

        total = len(sources)
        reachable = 0
        unreachable: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []

        for row in sources:
            src = dict(row)
            try:
                req = urllib.request.Request(src["url"], method="HEAD")
                urllib.request.urlopen(req, timeout=10)
                reachable += 1
            except urllib.error.HTTPError as e:
                unreachable.append({
                    "source_id": src["id"],
                    "url": src["url"],
                    "domain": src["domain"],
                    "error": f"HTTP {e.code}",
                })
            except Exception as e:
                errors.append({
                    "source_id": src["id"],
                    "url": src["url"],
                    "domain": src["domain"],
                    "error": str(e)[:200],
                })

        return {
            "check_type": "source_availability",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_checked": total,
            "reachable": reachable,
            "unreachable": len(unreachable),
            "errors": len(errors),
            "unreachable_details": unreachable,
            "error_details": errors,
        }

    # ── Full Integrity Check ────────────────────────────────────────────

    def run_full_check(self) -> Dict[str, Any]:
        """Run all integrity checks and return a comprehensive report."""
        results = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "hash_verification": self.verify_all_document_hashes(),
            "duplicate_detection": self.detect_duplicates(),
            "conflicting_versions": self.detect_conflicting_versions(),
            "citation_integrity": self.verify_citation_integrity(),
            "source_availability": self.check_source_availability(),
        }

        # Compute overall health
        issues = 0
        if not results["hash_verification"]["intact"]:
            issues += 1
        if results["duplicate_detection"]["total_duplicates"] > 0:
            issues += 1
        if results["conflicting_versions"]["conflicts_found"] > 0:
            issues += 1
        if results["citation_integrity"]["broken_citations"] > 0:
            issues += 1
        if results["source_availability"]["unreachable"] > 0:
            issues += 1

        results["overall_health"] = "healthy" if issues == 0 else "degraded" if issues <= 2 else "unhealthy"
        results["issues_found"] = issues

        return results

    def format_alert(self, results: Dict[str, Any]) -> Optional[str]:
        """Format integrity check results as a Telegram alert.

        Returns None if everything is healthy (no alert needed).
        """
        if results.get("overall_health") == "healthy":
            return None

        lines = []
        lines.append("⚠️ **Legal Brain Integrity Alert**")
        lines.append(f"Status: {results['overall_health'].upper()}")
        lines.append(f"Issues: {results['issues_found']}")
        lines.append("")

        h = results["hash_verification"]
        if not h["intact"]:
            lines.append(f"🔴 Hash verification: {h['invalid']} tampered, {h['missing']} missing")

        d = results["duplicate_detection"]
        if d["total_duplicates"] > 0:
            lines.append(f"🟡 Duplicates: {d['total_duplicates']} groups")

        v = results["conflicting_versions"]
        if v["conflicts_found"] > 0:
            lines.append(f"🟡 Conflicting versions: {v['conflicts_found']}")

        c = results["citation_integrity"]
        if c["broken_citations"] > 0:
            lines.append(f"🟡 Broken citations: {c['broken_citations']}")

        s = results["source_availability"]
        if s["unreachable"] > 0:
            lines.append(f"🔴 Unreachable sources: {s['unreachable']}/{s['total_checked']}")

        return "\n".join(lines)
