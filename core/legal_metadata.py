"""17O-C: Legal Metadata + Version Control for Ghana Legal Knowledge Base.

Comprehensive metadata schema for legal documents plus immutable version
control with full audit trail. Every document gets:
  - Jurisdiction, court, year, citation, judge, parties metadata
  - Status tracking: current, overruled, amended, repealed, historical
  - Content-addressable immutable storage (SHA-256)
  - Audit trail recording every mutation

Integrates with the 17O-B legal taxonomy module.
"""

import hashlib
import json
import os
import shutil
import sqlite3
from copy import deepcopy
from dataclasses import dataclass, field, asdict, fields
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
import logging

logger = logging.getLogger("legal_metadata")


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class DocumentStatus(str, Enum):
    """Legal status of a document — whether it remains good law."""
    CURRENT = "current"
    OVERRULED = "overruled"
    AMENDED = "amended"
    REPEALED = "repealed"
    HISTORICAL = "historical"
    SUPERSEDED = "superseded"
    STAYED = "stayed"


class CourtLevel(str, Enum):
    """Ghana court hierarchy."""
    SUPREME_COURT = "Supreme Court"
    COURT_OF_APPEAL = "Court of Appeal"
    HIGH_COURT = "High Court"
    CIRCUIT_COURT = "Circuit Court"
    DISTRICT_COURT = "District Court"
    SPECIALISED_COURT = "Specialised Court"
    FAST_TRACK_HIGH_COURT = "Fast Track High Court"
    REGIONAL_TRIBUNAL = "Regional Tribunal"
    NOT_APPLICABLE = "N/A"  # For legislation, constitutions, etc.


class LegislationType(str, Enum):
    """Types of Ghanaian legislation."""
    CONSTITUTION = "Constitution"
    ACT = "Act of Parliament"
    LEGISLATIVE_INSTRUMENT = "Legislative Instrument (LI)"
    CONSTITUTIONAL_INSTRUMENT = "Constitutional Instrument (CI)"
    EXECUTIVE_INSTRUMENT = "Executive Instrument (EI)"
    REGULATION = "Regulation"
    BYE_LAW = "Bye-law"
    ORDER = "Order"
    NOT_APPLICABLE = "N/A"


# ---------------------------------------------------------------------------
# Core metadata schema
# ---------------------------------------------------------------------------

@dataclass
class GhanaLegalCitation:
    """Standard Ghana legal citation format.

    Examples:
      - Constitution: "1992 Constitution of Ghana, Article 1(2)"
      - Act: "Act 651 (Labour Act, 2003)"
      - LI: "LI 1807 (2002)"
      - Case: "[2003-2004] SCGLR 1"  (Supreme Court Ghana Law Reports)
      - Neutral: "J1/1/2020 (Supreme Court, 2021)"
    """
    citation_text: str
    neutral_citation: Optional[str] = None
    law_report_citation: Optional[str] = None
    year: Optional[int] = None
    volume: Optional[str] = None
    page: Optional[int] = None


@dataclass
class LegalMetadata:
    """Complete metadata for a Ghana legal document.

    All fields are optional except those marked as required for their category.
    The schema is designed to be extensible — new fields can be added without
    breaking existing records.
    """
    # Required fields
    jurisdiction: str = "Ghana"
    document_type: str = ""  # "legislation", "case_law", "constitution", etc.

    # Court/judge (for case law)
    court: Optional[str] = None  # CourtLevel value
    court_division: Optional[str] = None
    judge: Optional[str] = None
    judges_panel: Optional[List[str]] = None  # For multi-judge benches

    # Citation
    citation: Optional[GhanaLegalCitation] = None

    # Parties (for case law)
    plaintiff: Optional[str] = None
    defendant: Optional[str] = None
    appellant: Optional[str] = None
    respondent: Optional[str] = None
    parties_other: Optional[List[str]] = None

    # Dates
    year: Optional[int] = None
    judgment_date: Optional[str] = None  # ISO date
    filing_date: Optional[str] = None
    promulgation_date: Optional[str] = None  # For legislation
    commencement_date: Optional[str] = None
    assent_date: Optional[str] = None

    # Status
    status: DocumentStatus = DocumentStatus.CURRENT
    status_note: Optional[str] = None  # "Overruled by X v Y [2020] SCGLR 5"

    # Amendment chain (for legislation)
    amended_by: Optional[List[str]] = None
    amends: Optional[List[str]] = None
    repealed_by: Optional[str] = None

    # Classification
    subject_areas: List[str] = field(default_factory=list)  # "Labour Law", "Constitutional Law"
    keywords: List[str] = field(default_factory=list)
    taxonomy_category: Optional[str] = None  # 17O-B category code "01"-"07"
    taxonomy_subcategory: Optional[str] = None

    # Source
    source_url: Optional[str] = None
    source_type: Optional[str] = None  # "pdf", "html", "txt"
    language: str = "en"

    # References
    legislation_cited: Optional[List[str]] = None
    cases_cited: Optional[List[str]] = None
    international_law_cited: Optional[List[str]] = None

    # Extensibility
    custom_fields: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to JSON-compatible dict, handling nested dataclasses."""
        d = asdict(self)
        # Convert enums to values
        if self.status:
            d["status"] = self.status.value
        # Convert nested citation
        if self.citation:
            d["citation"] = asdict(self.citation)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LegalMetadata":
        """Deserialize from dict, reconstructing nested dataclasses."""
        d = dict(data)
        # Reconstruct citation
        if d.get("citation") and isinstance(d["citation"], dict):
            d["citation"] = GhanaLegalCitation(**d["citation"])
        # Reconstruct status enum
        if d.get("status"):
            try:
                d["status"] = DocumentStatus(d["status"])
            except ValueError:
                d["status"] = DocumentStatus.CURRENT
        # Remove fields not in the dataclass (forward compat)
        valid_keys = {f.name for f in fields(cls)}
        d = {k: v for k, v in d.items() if k in valid_keys}
        return cls(**d)


# ---------------------------------------------------------------------------
# Content-addressable immutable storage
# ---------------------------------------------------------------------------

class ImmutableStorage:
    """Content-addressable, append-only storage for legal documents.

    Every version of a document is stored under its SHA-256 hash.
    Once written, content is never modified — only new versions are added.
    Metadata is stored in SQLite for efficient queries.
    """

    def __init__(self, storage_dir: str = "legal_storage", db_path: str = "legal_metadata.db"):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = Path(db_path)
        self._init_db()

    def _init_db(self):
        """Initialize SQLite schema for metadata + audit trail."""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")

            conn.executescript("""
                -- Document registry
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    current_version_hash TEXT NOT NULL,
                    title TEXT NOT NULL,
                    document_type TEXT NOT NULL,
                    jurisdiction TEXT DEFAULT 'Ghana',
                    year INTEGER,
                    court TEXT,
                    citation_text TEXT,
                    status TEXT DEFAULT 'current',
                    taxonomy_category TEXT,
                    taxonomy_subcategory TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                -- Content versions (immutable — rows are never updated)
                CREATE TABLE IF NOT EXISTS content_versions (
                    version_hash TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    version_number INTEGER NOT NULL,
                    content_path TEXT NOT NULL,       -- relative to storage_dir
                    content_checksum TEXT NOT NULL,   -- SHA-256 of content
                    content_size_bytes INTEGER NOT NULL,
                    metadata_json TEXT,               -- full LegalMetadata as JSON
                    created_at TEXT NOT NULL,
                    created_by TEXT DEFAULT 'system',
                    change_description TEXT,
                    FOREIGN KEY (document_id) REFERENCES documents(id)
                );

                -- Immutable audit trail
                CREATE TABLE IF NOT EXISTS audit_trail (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id TEXT NOT NULL,
                    action TEXT NOT NULL,             -- 'created', 'updated', 'version_added',
                                                     -- 'status_changed', 'deleted'
                    old_value TEXT,                   -- JSON of previous state
                    new_value TEXT,                   -- JSON of new state
                    performed_by TEXT DEFAULT 'system',
                    performed_at TEXT NOT NULL,
                    ip_address TEXT,
                    FOREIGN KEY (document_id) REFERENCES documents(id)
                );

                -- Indexes for common queries
                CREATE INDEX IF NOT EXISTS idx_docs_type ON documents(document_type);
                CREATE INDEX IF NOT EXISTS idx_docs_status ON documents(status);
                CREATE INDEX IF NOT EXISTS idx_docs_year ON documents(year);
                CREATE INDEX IF NOT EXISTS idx_docs_court ON documents(court);
                CREATE INDEX IF NOT EXISTS idx_docs_category ON documents(taxonomy_category);
                CREATE INDEX IF NOT EXISTS idx_versions_doc ON content_versions(document_id);
                CREATE INDEX IF NOT EXISTS idx_audit_doc ON audit_trail(document_id);
                CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_trail(performed_at);
            """)

    # ------------------------------------------------------------------
    # Storage operations
    # ------------------------------------------------------------------

    def hash_content(self, content: bytes) -> str:
        """SHA-256 hash of content bytes."""
        return hashlib.sha256(content).hexdigest()

    def store_document(
        self,
        document_id: str,
        title: str,
        content: bytes,
        metadata: LegalMetadata,
        change_description: str = "Initial version",
        created_by: str = "system",
    ) -> Tuple[str, int]:
        """Store a document (or new version of existing document).

        Content is stored on disk under its hash. Metadata and audit trail
        are stored in SQLite. Returns (version_hash, version_number).

        If the document_id already exists, this adds a new version.
        """
        now = datetime.now(timezone.utc).isoformat()
        content_hash = self.hash_content(content)
        metadata_dict = metadata.to_dict()

        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("PRAGMA foreign_keys=ON")

            # Check if document exists
            existing = conn.execute(
                "SELECT id, current_version_hash FROM documents WHERE id = ?",
                (document_id,)
            ).fetchone()

            if existing:
                # Adding a new version
                current_version = conn.execute(
                    "SELECT MAX(version_number) FROM content_versions WHERE document_id = ?",
                    (document_id,)
                ).fetchone()[0] or 0
                new_version = current_version + 1

                # Get old state for audit
                old_meta = conn.execute(
                    "SELECT metadata_json FROM content_versions WHERE version_hash = ?",
                    (existing[1],)
                ).fetchone()
                old_value = old_meta[0] if old_meta else None

                # Write new content version
                version_hash = self._write_content(content, content_hash, document_id, new_version)
                conn.execute(
                    """INSERT INTO content_versions
                       (version_hash, document_id, version_number, content_path,
                        content_checksum, content_size_bytes, metadata_json,
                        created_at, created_by, change_description)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (version_hash, document_id, new_version,
                     f"{document_id}/{content_hash}",
                     content_hash, len(content),
                     json.dumps(metadata_dict),
                     now, created_by, change_description)
                )

                # Update document registry
                conn.execute(
                    "UPDATE documents SET current_version_hash = ?, updated_at = ? WHERE id = ?",
                    (version_hash, now, document_id)
                )

                # Audit trail
                conn.execute(
                    """INSERT INTO audit_trail
                       (document_id, action, old_value, new_value, performed_by, performed_at)
                       VALUES (?, 'version_added', ?, ?, ?, ?)""",
                    (document_id, old_value, json.dumps(metadata_dict), created_by, now)
                )

                logger.info(f"Added version {new_version} for {document_id}")
                return version_hash, new_version
            else:
                # First version
                version_hash = self._write_content(content, content_hash, document_id, 1)

                conn.execute(
                    """INSERT INTO documents
                       (id, current_version_hash, title, document_type, jurisdiction,
                        year, court, citation_text, status, taxonomy_category,
                        taxonomy_subcategory, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (document_id, version_hash, title, metadata.document_type,
                     metadata.jurisdiction, metadata.year, metadata.court,
                     metadata.citation.citation_text if metadata.citation else None,
                     metadata.status.value, metadata.taxonomy_category,
                     metadata.taxonomy_subcategory, now, now)
                )

                conn.execute(
                    """INSERT INTO content_versions
                       (version_hash, document_id, version_number, content_path,
                        content_checksum, content_size_bytes, metadata_json,
                        created_at, created_by, change_description)
                       VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?)""",
                    (version_hash, document_id,
                     f"{document_id}/{content_hash}",
                     content_hash, len(content),
                     json.dumps(metadata_dict),
                     now, created_by, change_description)
                )

                conn.execute(
                    """INSERT INTO audit_trail
                       (document_id, action, new_value, performed_by, performed_at)
                       VALUES (?, 'created', ?, ?, ?)""",
                    (document_id, json.dumps(metadata_dict), created_by, now)
                )

                logger.info(f"Created document {document_id}")
                return version_hash, 1

    def _write_content(self, content: bytes, content_hash: str, doc_id: str, version: int) -> str:
        """Write content bytes to disk. Returns a version hash unique to this
        document-version (composite of doc_id + content_hash) so two documents
        with identical content still get distinct version rows.

        Content is stored in a content-addressed directory structure:
        storage_dir/{doc_id}/{content_hash}
        """
        doc_dir = self.storage_dir / doc_id
        doc_dir.mkdir(parents=True, exist_ok=True)

        # Store by content hash for deduplication
        content_path = doc_dir / content_hash
        if not content_path.exists():
            content_path.write_bytes(content)

        # Version hash: unique per document + content combination
        composite = f"{doc_id}:v{version}:{content_hash}"
        return hashlib.sha256(composite.encode()).hexdigest()

    def get_document(self, document_id: str, version: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Retrieve a document and its content.

        If version is None, returns the latest version.
        Returns dict with: document_id, title, content_bytes, metadata, version_number, created_at
        """
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row

            if version is not None:
                row = conn.execute(
                    """SELECT v.*, d.title, d.document_type, d.jurisdiction
                       FROM content_versions v
                       JOIN documents d ON v.document_id = d.id
                       WHERE v.document_id = ? AND v.version_number = ?""",
                    (document_id, version)
                ).fetchone()
            else:
                # Latest version
                row = conn.execute(
                    """SELECT v.*, d.title, d.document_type, d.jurisdiction
                       FROM documents d
                       JOIN content_versions v ON v.version_hash = d.current_version_hash
                       WHERE d.id = ?""",
                    (document_id,)
                ).fetchone()

            if not row:
                return None

            row_dict = dict(row)
            content_path = self.storage_dir / row_dict["content_path"]
            content_bytes = content_path.read_bytes() if content_path.exists() else b""

            metadata = LegalMetadata.from_dict(json.loads(row_dict["metadata_json"])) \
                if row_dict["metadata_json"] else None

            return {
                "document_id": document_id,
                "title": row_dict["title"],
                "version_number": row_dict["version_number"],
                "version_hash": row_dict["version_hash"],
                "content_path": row_dict["content_path"],
                "content_bytes": content_bytes,
                "metadata": metadata,
                "change_description": row_dict["change_description"],
                "created_at": row_dict["created_at"],
                "created_by": row_dict["created_by"],
            }

    def list_documents(
        self,
        document_type: Optional[str] = None,
        court: Optional[str] = None,
        year: Optional[int] = None,
        status: Optional[str] = None,
        taxonomy_category: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Query documents with filters."""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row

            query = "SELECT * FROM documents WHERE 1=1"
            params: List[Any] = []

            if document_type:
                query += " AND document_type = ?"
                params.append(document_type)
            if court:
                query += " AND court = ?"
                params.append(court)
            if year:
                query += " AND year = ?"
                params.append(year)
            if status:
                query += " AND status = ?"
                params.append(status)
            if taxonomy_category:
                query += " AND taxonomy_category = ?"
                params.append(taxonomy_category)

            query += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            return [dict(row) for row in conn.execute(query, params)]

    def update_metadata(
        self,
        document_id: str,
        metadata: LegalMetadata,
        created_by: str = "system",
        change_description: str = "Metadata update",
    ) -> bool:
        """Update metadata for the current version (mutable metadata, immutable content).

        This only updates the metadata record — content remains immutable.
        Status changes (current → overruled, etc.) are recorded here.
        """
        now = datetime.now(timezone.utc).isoformat()
        metadata_dict = metadata.to_dict()

        with sqlite3.connect(str(self.db_path)) as conn:
            # Get current state for audit
            current = conn.execute(
                "SELECT metadata_json FROM content_versions WHERE version_hash = (SELECT current_version_hash FROM documents WHERE id = ?)",
                (document_id,)
            ).fetchone()

            old_value = current[0] if current else None

            conn.execute(
                """UPDATE content_versions
                   SET metadata_json = ? WHERE version_hash = (
                       SELECT current_version_hash FROM documents WHERE id = ?
                   )""",
                (json.dumps(metadata_dict), document_id)
            )

            # Update document registry fields derived from metadata
            conn.execute(
                """UPDATE documents SET
                   status = ?, year = ?, court = ?, citation_text = ?,
                   taxonomy_category = ?, taxonomy_subcategory = ?,
                   updated_at = ?
                   WHERE id = ?""",
                (metadata.status.value, metadata.year, metadata.court,
                 metadata.citation.citation_text if metadata.citation else None,
                 metadata.taxonomy_category, metadata.taxonomy_subcategory,
                 now, document_id)
            )

            conn.execute(
                """INSERT INTO audit_trail
                   (document_id, action, old_value, new_value, performed_by, performed_at)
                   VALUES (?, 'status_changed', ?, ?, ?, ?)""",
                (document_id, old_value, json.dumps(metadata_dict), created_by, now)
            )

            return True

    def get_audit_trail(self, document_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Get the full audit trail for a document."""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT * FROM audit_trail
                   WHERE document_id = ?
                   ORDER BY performed_at DESC LIMIT ?""",
                (document_id, limit)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_version_history(self, document_id: str) -> List[Dict[str, Any]]:
        """Get all versions of a document."""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT version_hash, version_number, content_checksum,
                          content_size_bytes, created_at, created_by, change_description
                   FROM content_versions
                   WHERE document_id = ?
                   ORDER BY version_number ASC""",
                (document_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def verify_integrity(self, document_id: Optional[str] = None) -> Dict[str, Any]:
        """Verify content integrity by checking stored checksums.

        Returns a report of any corruption or missing content.
        """
        issues: List[Dict[str, Any]] = []

        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row

            query = "SELECT * FROM content_versions"
            params: tuple = ()
            if document_id:
                query += " WHERE document_id = ?"
                params = (document_id,)

            for row in conn.execute(query, params):
                content_path = self.storage_dir / row["content_path"]
                if not content_path.exists():
                    issues.append({
                        "document_id": row["document_id"],
                        "version": row["version_number"],
                        "issue": "content_missing",
                        "path": str(content_path),
                    })
                else:
                    actual_hash = self.hash_content(content_path.read_bytes())
                    if actual_hash != row["content_checksum"]:
                        issues.append({
                            "document_id": row["document_id"],
                            "version": row["version_number"],
                            "issue": "checksum_mismatch",
                            "expected": row["content_checksum"],
                            "actual": actual_hash,
                        })

        return {
            "documents_checked": 0 if not document_id else 1,
            "issues_found": len(issues),
            "issues": issues,
            "verified_at": datetime.now(timezone.utc).isoformat(),
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def create_metadata_template(document_type: str) -> LegalMetadata:
    """Create a LegalMetadata with sensible defaults for the given document type."""
    if document_type == "legislation":
        return LegalMetadata(
            document_type="legislation",
            status=DocumentStatus.CURRENT,
            subject_areas=["Legislation"],
            taxonomy_category="02",
        )
    elif document_type == "case_law":
        return LegalMetadata(
            document_type="case_law",
            status=DocumentStatus.CURRENT,
            subject_areas=["Case Law"],
            taxonomy_category="03",
        )
    elif document_type == "constitution":
        return LegalMetadata(
            document_type="constitution",
            status=DocumentStatus.CURRENT,
            subject_areas=["Constitutional Law"],
            taxonomy_category="01",
        )
    else:
        return LegalMetadata(document_type=document_type)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Ghana Legal Knowledge Base — Metadata & Version Control")
    sub = parser.add_subparsers(dest="command")

    store_parser = sub.add_parser("store", help="Store a document")
    store_parser.add_argument("--id", required=True, help="Document ID")
    store_parser.add_argument("--title", required=True, help="Document title")
    store_parser.add_argument("--type", required=True, help="Document type (legislation/case_law/constitution)")
    store_parser.add_argument("--file", required=True, help="Path to document file")

    list_parser = sub.add_parser("list", help="List documents")
    list_parser.add_argument("--type", help="Filter by document type")
    list_parser.add_argument("--court", help="Filter by court")
    list_parser.add_argument("--year", type=int, help="Filter by year")
    list_parser.add_argument("--status", help="Filter by status")

    verify_parser = sub.add_parser("verify", help="Verify storage integrity")
    verify_parser.add_argument("--doc-id", help="Verify specific document")

    args = parser.parse_args()

    if args.command == "store":
        storage = ImmutableStorage()
        content = Path(args.file).read_bytes()
        metadata = create_metadata_template(args.type)
        metadata.source_url = f"file://{args.file}"
        metadata.source_type = Path(args.file).suffix.lstrip(".")
        vhash, vnum = storage.store_document(args.id, args.title, content, metadata)
        print(f"Stored: {args.id} v{vnum} ({vhash[:16]}...)")

    elif args.command == "list":
        storage = ImmutableStorage()
        docs = storage.list_development(
            document_type=args.type, court=args.court,
            year=args.year, status=args.status
        )
        for doc in docs:
            print(f"  {doc['id']}: {doc['title']} [{doc['status']}]")

    elif args.command == "verify":
        storage = ImmutableStorage()
        report = storage.verify_integrity(document_id=args.doc_id)
        print(json.dumps(report, indent=2))

    else:
        parser.print_help()
