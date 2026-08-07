"""Tests for Phase 18C: Zero-Trust Legal Brain Architecture.

Covers:
  - Permanent WORM store (insert-only, hash-chain)
  - Document lifecycle (pending → approved/rejected)
  - Audit chain integrity
  - Workspace isolation (sessions, documents, analyses, expiry)
  - Sandbox PDF/text extraction
  - Malware scanning (heuristic)
  - Service boundary (no cross-imports between permanent/workspace)
  - Migration from Klaus
"""

import os
import sys
import tempfile
import uuid
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Permanent Store Tests
# ---------------------------------------------------------------------------

class TestPermanentStore:
    """WORM document store with hash-chain integrity."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Use temp directory for test."""
        import core.legal_brain.config as cfg
        import core.legal_brain.permanent as perm

        self.test_dir = Path(tempfile.mkdtemp())
        self.db_path = self.test_dir / "test_legal_brain.db"
        self.storage_dir = self.test_dir / "documents"
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        # Override paths
        cfg.PERMANENT_DB_PATH = self.db_path
        cfg.PERMANENT_STORAGE_ROOT = self.storage_dir

        # Override get_db_path
        self._orig_get_db_path = perm.get_db_path
        perm.get_db_path = lambda: self.db_path

        perm.init_permanent_store(self.db_path)

        yield

        perm.get_db_path = self._orig_get_db_path

        # Cleanup
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_init_permanent_store(self):
        """Store initializes and creates tables."""
        import sqlite3
        conn = sqlite3.connect(str(self.db_path))
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = [t[0] for t in tables]
        conn.close()

        assert "sources" in table_names
        assert "documents" in table_names
        assert "chunks" in table_names
        assert "citations" in table_names
        assert "audit_chain" in table_names

    def test_add_and_get_source(self):
        """Source CRUD works."""
        from core.legal_brain.permanent.store import add_source, get_source, list_sources

        sid = add_source("https://parliament.gh", "parliament.gh", 1)
        assert sid

        src = get_source(sid)
        assert src["url"] == "https://parliament.gh"
        assert src["tier"] == 1
        assert src["jurisdiction"] == "Ghana"

        all_sources = list_sources()
        assert len(all_sources) == 1

    def test_source_deduplication(self):
        """Duplicate sources update last_checked instead of creating new."""
        from core.legal_brain.permanent.store import add_source, list_sources

        sid1 = add_source("https://ghalii.org", "ghalii.org", 2)
        sid2 = add_source("https://ghalii.org", "ghalii.org", 2)
        assert sid1 == sid2  # Same source

        sources = list_sources()
        assert len(sources) == 1

    def test_insert_document_worm(self):
        """Documents are inserted with WORM semantics."""
        from core.legal_brain.permanent.store import (
            add_source, insert_document, get_document, compute_hash,
        )

        sid = add_source("https://parliament.gh", "parliament.gh", 1)

        content = b"Constitution of the Republic of Ghana, 1992"
        file_hash = compute_hash(content)

        # Write a test file
        test_file = self.storage_dir / "constitution.txt"
        test_file.write_bytes(content)

        doc_id = insert_document(
            source_id=sid,
            title="Constitution of Ghana",
            content_hash=file_hash,
            file_path=str(test_file),
            category="Constitutional Law",
            copyright_classification="official_public_access",
            jurisdiction="Ghana",
            court="Supreme Court",
            year=1992,
            page_count=1,
            file_size_bytes=len(content),
        )

        assert doc_id
        doc = get_document(doc_id)
        assert doc["title"] == "Constitution of Ghana"
        assert doc["content_hash"] == file_hash
        assert doc["review_status"] == "pending"
        assert doc["jurisdiction"] == "Ghana"
        assert doc["category"] == "Constitutional Law"

    def test_hash_chain_integrity(self):
        """Each document links to the previous via prev_hash."""
        from core.legal_brain.permanent.store import (
            add_source, insert_document, get_document, compute_hash,
        )

        sid = add_source("https://parliament.gh", "parliament.gh", 1)

        # Insert two documents
        doc1_id = insert_document(
            source_id=sid, title="Doc 1",
            content_hash=compute_hash(b"content 1"),
            file_path=str(self.storage_dir / "doc1.txt"),
            category="Legislation",
            copyright_classification="official_public_access",
        )

        doc2_id = insert_document(
            source_id=sid, title="Doc 2",
            content_hash=compute_hash(b"content 2"),
            file_path=str(self.storage_dir / "doc2.txt"),
            category="Legislation",
            copyright_classification="official_public_access",
        )

        doc1 = get_document(doc1_id)
        doc2 = get_document(doc2_id)

        # First doc has no prev_hash (NULL in DB → None in Python)
        assert doc1["prev_hash"] is None
        # Second doc links to first
        assert doc2["prev_hash"] == doc1["content_hash"]

    def test_document_approval_workflow(self):
        """Documents follow pending → approved workflow."""
        from core.legal_brain.permanent.store import (
            add_source, insert_document, approve_document, get_document, compute_hash,
        )

        sid = add_source("https://parliament.gh", "parliament.gh", 1)
        doc_id = insert_document(
            source_id=sid, title="Test Law",
            content_hash=compute_hash(b"test"),
            file_path=str(self.storage_dir / "test.txt"),
            category="Legislation",
            copyright_classification="official_public_access",
        )

        assert approve_document(doc_id, "operator1") is True
        doc = get_document(doc_id)
        assert doc["review_status"] == "approved"
        assert doc["approved_by"] == "operator1"

        # Cannot approve twice
        assert approve_document(doc_id, "operator2") is False

    def test_document_rejection(self):
        """Documents can be rejected."""
        from core.legal_brain.permanent.store import (
            add_source, insert_document, reject_document, get_document, compute_hash,
        )

        sid = add_source("https://parliament.gh", "parliament.gh", 1)
        doc_id = insert_document(
            source_id=sid, title="Test Law",
            content_hash=compute_hash(b"test"),
            file_path=str(self.storage_dir / "test.txt"),
            category="Legislation",
            copyright_classification="official_public_access",
        )

        assert reject_document(doc_id, "operator1") is True
        doc = get_document(doc_id)
        assert doc["review_status"] == "rejected"

    def test_approve_nonexistent_document(self):
        """Approving a nonexistent doc returns False."""
        from core.legal_brain.permanent.store import approve_document
        assert approve_document("nonexistent-id", "op") is False

    def test_search_documents(self):
        """Full-text search finds documents by title."""
        from core.legal_brain.permanent.store import (
            add_source, insert_document, approve_document, search_documents, compute_hash,
        )

        sid = add_source("https://parliament.gh", "parliament.gh", 1)

        doc_id = insert_document(
            source_id=sid, title="Ghana Criminal Code 1960",
            content_hash=compute_hash(b"criminal code"),
            file_path=str(self.storage_dir / "criminal.txt"),
            category="Legislation",
            copyright_classification="official_public_access",
        )
        approve_document(doc_id, "op")

        results = search_documents("Criminal")
        assert len(results) == 1
        assert "Criminal" in results[0]["title"]

        # Unapproved docs shouldn't appear in search
        insert_document(
            source_id=sid, title="Secret Law",
            content_hash=compute_hash(b"secret"),
            file_path=str(self.storage_dir / "secret.txt"),
            category="Legislation",
            copyright_classification="official_public_access",
        )

        results2 = search_documents("Secret")
        assert len(results2) == 0  # Not approved

    def test_audit_chain_logged(self):
        """Every document operation is logged in the audit chain."""
        from core.legal_brain.permanent.store import (
            add_source, insert_document, get_audit_chain, compute_hash,
        )

        sid = add_source("https://parliament.gh", "parliament.gh", 1)
        insert_document(
            source_id=sid, title="Audit Test",
            content_hash=compute_hash(b"audit test"),
            file_path=str(self.storage_dir / "audit.txt"),
            category="Legislation",
            copyright_classification="official_public_access",
        )

        chain = get_audit_chain()
        assert len(chain) >= 1
        assert chain[0]["event_type"] == "document.inserted"

    def test_audit_chain_integrity(self):
        """Audit chain verification catches tampering."""
        from core.legal_brain.permanent.store import (
            add_source, insert_document, verify_audit_chain, compute_hash,
        )

        sid = add_source("https://parliament.gh", "parliament.gh", 1)
        insert_document(
            source_id=sid, title="Integrity Test",
            content_hash=compute_hash(b"integrity test"),
            file_path=str(self.storage_dir / "integrity.txt"),
            category="Legislation",
            copyright_classification="official_public_access",
        )

        result = verify_audit_chain()
        assert result["intact"] is True
        assert result["invalid"] == 0

    def test_document_hash_verification(self):
        """verify_all_document_hashes validates stored content."""
        from core.legal_brain.permanent.store import (
            add_source, insert_document, verify_all_document_hashes, compute_hash,
        )

        sid = add_source("https://parliament.gh", "parliament.gh", 1)

        content = b"Verify me!"
        test_file = self.storage_dir / "verify.txt"
        test_file.write_bytes(content)

        insert_document(
            source_id=sid, title="Verification Test",
            content_hash=compute_hash(content),
            file_path=str(test_file),
            category="Legislation",
            copyright_classification="official_public_access",
        )

        result = verify_all_document_hashes()
        assert result["intact"] is True
        assert result["valid"] == 1

    def test_document_version_chain(self):
        """version chain follows parent_doc_id links."""
        from core.legal_brain.permanent.store import (
            add_source, insert_document, get_document_version_chain, compute_hash,
        )

        sid = add_source("https://parliament.gh", "parliament.gh", 1)

        v1_id = insert_document(
            source_id=sid, title="Law v1",
            content_hash=compute_hash(b"v1"),
            file_path=str(self.storage_dir / "v1.txt"),
            category="Legislation",
            copyright_classification="official_public_access",
        )

        v2_id = insert_document(
            source_id=sid, title="Law v2",
            content_hash=compute_hash(b"v2"),
            file_path=str(self.storage_dir / "v2.txt"),
            category="Legislation",
            copyright_classification="official_public_access",
            parent_doc_id=v1_id,
        )

        chain = get_document_version_chain(v2_id)
        assert len(chain) == 2
        assert chain[0]["title"] == "Law v2"
        assert chain[1]["title"] == "Law v1"

    def test_citation_insert_and_query(self):
        """Citations link documents."""
        from core.legal_brain.permanent.store import (
            add_source, insert_document, insert_citation,
            get_citations_for_document, get_documents_citing, compute_hash,
        )

        sid = add_source("https://parliament.gh", "parliament.gh", 1)

        doc1_id = insert_document(
            source_id=sid, title="Constitution",
            content_hash=compute_hash(b"constitution"),
            file_path=str(self.storage_dir / "const.txt"),
            category="Constitutional Law",
            copyright_classification="official_public_access",
        )

        doc2_id = insert_document(
            source_id=sid, title="Case Law",
            content_hash=compute_hash(b"caselaw"),
            file_path=str(self.storage_dir / "case.txt"),
            category="Judiciary",
            copyright_classification="official_public_access",
        )

        insert_citation(doc2_id, doc1_id, citation_type="references",
                        context_snippet="Per Article 12")

        # Citations from doc2
        citations = get_citations_for_document(doc2_id)
        assert len(citations) == 1
        assert citations[0]["citation_type"] == "references"

        # Who cites doc1
        citing = get_documents_citing(doc1_id)
        assert len(citing) == 1
        assert citing[0]["source_title"] == "Case Law"

    def test_store_stats(self):
        """Statistics aggregate correctly."""
        from core.legal_brain.permanent.store import (
            add_source, insert_document, get_store_stats, compute_hash,
        )

        sid = add_source("https://parliament.gh", "parliament.gh", 1)
        insert_document(
            source_id=sid, title="Stat Test",
            content_hash=compute_hash(b"stats"),
            file_path=str(self.storage_dir / "stats.txt"),
            category="Legislation",
            copyright_classification="official_public_access",
        )

        stats = get_store_stats()
        assert stats["documents_total"] == 1
        assert stats["sources"] == 1
        assert stats["documents_pending"] == 1
        assert stats["documents_approved"] == 0

    def test_chunks_insert_and_retrieve(self):
        """Document chunking works."""
        from core.legal_brain.permanent.store import (
            add_source, insert_document, insert_chunk, get_chunks, compute_hash,
        )

        sid = add_source("https://parliament.gh", "parliament.gh", 1)
        doc_id = insert_document(
            source_id=sid, title="Chunked Doc",
            content_hash=compute_hash(b"chunk me"),
            file_path=str(self.storage_dir / "chunk.txt"),
            category="Legislation",
            copyright_classification="official_public_access",
        )

        insert_chunk(doc_id, 0, "First chunk of text")
        insert_chunk(doc_id, 1, "Second chunk of text")

        chunks = get_chunks(doc_id)
        assert len(chunks) == 2
        assert chunks[0]["chunk_index"] == 0
        assert chunks[1]["chunk_index"] == 1

    def test_list_documents_with_filters(self):
        """Documents can be filtered by category and status."""
        from core.legal_brain.permanent.store import (
            add_source, insert_document, approve_document, list_documents, compute_hash,
        )

        sid = add_source("https://parliament.gh", "parliament.gh", 1)

        d1 = insert_document(
            source_id=sid, title="Legislation A",
            content_hash=compute_hash(b"A"),
            file_path=str(self.storage_dir / "A.txt"),
            category="Legislation",
            copyright_classification="official_public_access",
        )
        d2 = insert_document(
            source_id=sid, title="Judiciary B",
            content_hash=compute_hash(b"B"),
            file_path=str(self.storage_dir / "B.txt"),
            category="Judiciary",
            copyright_classification="official_public_access",
        )

        approve_document(d1, "op")

        # Filter by category
        leg = list_documents(category="Legislation")
        assert len(leg) == 1

        # Filter by status
        approved = list_documents(review_status="approved")
        assert len(approved) == 1


# ---------------------------------------------------------------------------
# Workspace Tests
# ---------------------------------------------------------------------------

class TestWorkspace:
    """Temporary user workspace — isolation and lifecycle."""

    @pytest.fixture(autouse=True)
    def setup(self):
        import core.legal_brain.config as cfg

        self.test_dir = Path(tempfile.mkdtemp())
        cfg.WORKSPACE_ROOT = self.test_dir

        yield

        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_create_and_get_session(self):
        """Session creation and retrieval."""
        from core.legal_brain.workspace import create_session, get_session

        session = create_session("user123")
        assert session["user_id"] == "user123"
        assert session["status"] == "active"
        assert "session_id" in session

        retrieved = get_session(session["session_id"])
        assert retrieved["user_id"] == "user123"
        assert retrieved["status"] == "active"

    def test_add_document_to_workspace(self):
        """Documents can be added to workspace sessions."""
        from core.legal_brain.workspace import create_session, add_document, list_documents

        session = create_session("user123")
        sid = session["session_id"]

        doc_id = add_document(
            session_id=sid,
            original_filename="test.pdf",
            file_hash="abc123",
            file_path="/tmp/test.pdf",
            file_size_bytes=1024,
            page_count=5,
        )

        docs = list_documents(sid)
        assert len(docs) == 1
        assert docs[0]["original_filename"] == "test.pdf"
        assert docs[0]["page_count"] == 5

    def test_add_document_increments_count(self):
        """Document count in session updates."""
        from core.legal_brain.workspace import create_session, add_document, get_session

        session = create_session("user123")
        sid = session["session_id"]

        add_document(sid, "doc1.pdf", "hash1", "/tmp/doc1.pdf")
        add_document(sid, "doc2.pdf", "hash2", "/tmp/doc2.pdf")

        updated = get_session(sid)
        assert updated["document_count"] == 2

    def test_save_and_get_analyses(self):
        """Analyses are saved per-session."""
        from core.legal_brain.workspace import create_session, add_document, save_analysis, get_analyses

        session = create_session("user123")
        sid = session["session_id"]

        doc_id = add_document(sid, "test.pdf", "hash", "/tmp/test.pdf")

        save_analysis(sid, doc_id, "summary", "This is a summary of the document.",
                      model_used="qwen3", confidence=0.85)
        save_analysis(sid, None, "general_query", "Answer to query",
                      model_used="qwen3", confidence=0.90)

        analyses = get_analyses(sid)
        assert len(analyses) == 2

    def test_destroy_session(self):
        """Session destruction removes all data."""
        from core.legal_brain.workspace import create_session, add_document, destroy_session, get_session

        session = create_session("user123")
        sid = session["session_id"]
        add_document(sid, "test.pdf", "hash", "/tmp/test.pdf")

        assert destroy_session(sid) is True
        assert get_session(sid) is None

    def test_nonexistent_session(self):
        """Accessing nonexistent session returns None."""
        from core.legal_brain.workspace import get_session, list_documents

        assert get_session("nonexistent") is None
        assert list_documents("nonexistent") == []

    def test_add_document_to_nonexistent_session(self):
        """Adding to nonexistent session raises ValueError."""
        import pytest
        from core.legal_brain.workspace import add_document

        with pytest.raises(ValueError, match="not found"):
            add_document("nonexistent", "test.pdf", "hash", "/tmp/test.pdf")

    def test_session_isolation(self):
        """Documents from one session do not leak to another."""
        from core.legal_brain.workspace import create_session, add_document, list_documents

        s1 = create_session("user1")
        s2 = create_session("user2")

        add_document(s1["session_id"], "user1_doc.pdf", "hash1", "/tmp/doc1.pdf")
        add_document(s2["session_id"], "user2_doc.pdf", "hash2", "/tmp/doc2.pdf")

        # User 1 only sees their doc
        docs1 = list_documents(s1["session_id"])
        assert len(docs1) == 1
        assert docs1[0]["original_filename"] == "user1_doc.pdf"

        # User 2 only sees their doc
        docs2 = list_documents(s2["session_id"])
        assert len(docs2) == 1
        assert docs2[0]["original_filename"] == "user2_doc.pdf"


# ---------------------------------------------------------------------------
# Sandbox Tests
# ---------------------------------------------------------------------------

class TestSandbox:
    """PDF/text extraction and malware scanning."""

    @pytest.fixture(autouse=True)
    def setup(self):
        import core.legal_brain.config as cfg

        self.orig_timeout = cfg.SANDBOX_TIMEOUT_SECONDS
        self.orig_max_size = cfg.SANDBOX_MAX_FILE_SIZE_MB
        cfg.SANDBOX_TIMEOUT_SECONDS = 10
        cfg.SANDBOX_MAX_FILE_SIZE_MB = 50

        yield
        cfg.SANDBOX_TIMEOUT_SECONDS = self.orig_timeout
        cfg.SANDBOX_MAX_FILE_SIZE_MB = self.orig_max_size

    def test_extract_text_from_txt(self):
        """Plain text extraction works."""
        from core.legal_brain.workspace.sandbox import extract_text

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Hello, this is a test document.\nIt has multiple lines.")
            filepath = f.name

        try:
            result = extract_text(filepath)
            assert result["success"] is True
            assert "Hello" in result["text"]
            assert result["pages"] >= 1
        finally:
            os.unlink(filepath)

    def test_extract_text_file_not_found(self):
        """Missing file returns error."""
        from core.legal_brain.workspace.sandbox import extract_text

        result = extract_text("/nonexistent/file.pdf")
        assert result["success"] is False
        assert "not found" in result["error"]

    def test_extract_text_unsupported_type(self):
        """Unsupported file types are rejected."""
        from core.legal_brain.workspace.sandbox import extract_text

        with tempfile.NamedTemporaryFile(mode="w", suffix=".xyz", delete=False) as f:
            f.write("test")
            filepath = f.name

        try:
            result = extract_text(filepath)
            assert result["success"] is False
            assert "Unsupported" in result["error"]
        finally:
            os.unlink(filepath)

    def test_scan_clean_file(self):
        """Heuristic scanner passes clean files."""
        from core.legal_brain.workspace.sandbox import scan_file

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("This is a normal legal document.")
            filepath = f.name

        try:
            result = scan_file(filepath)
            assert result["clean"] is True
            assert result["scanner"] == "heuristic"
        finally:
            os.unlink(filepath)

    def test_scan_executable_detected(self):
        """Heuristic scanner detects executable headers."""
        from core.legal_brain.workspace.sandbox import scan_file

        with tempfile.NamedTemporaryFile(mode="wb", suffix=".bin", delete=False) as f:
            f.write(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 100)
            filepath = f.name

        try:
            result = scan_file(filepath)
            assert result["clean"] is False
            assert "Executable" in result["details"]
        finally:
            os.unlink(filepath)

    def test_file_too_large(self):
        """Oversized files are rejected."""
        from core.legal_brain.workspace.sandbox import SandboxFileTooLarge, _check_file_size
        import core.legal_brain.config as cfg

        cfg.SANDBOX_MAX_FILE_SIZE_MB = 0  # Effectively reject everything
        try:
            with tempfile.NamedTemporaryFile(mode="wb", suffix=".txt", delete=False) as f:
                f.write(b"x" * 100)
                filepath = f.name

            try:
                with pytest.raises(SandboxFileTooLarge):
                    _check_file_size(filepath)
            finally:
                os.unlink(filepath)
        finally:
            cfg.SANDBOX_MAX_FILE_SIZE_MB = 50

    def test_extract_text_markdown(self):
        """Markdown extraction works."""
        from core.legal_brain.workspace.sandbox import extract_text

        content = "# Legal Brief\n\nThis is **important** text."
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(content)
            filepath = f.name

        try:
            result = extract_text(filepath)
            assert result["success"] is True
            assert "Legal Brief" in result["text"]
            assert "important" in result["text"]
        finally:
            os.unlink(filepath)


# ---------------------------------------------------------------------------
# Service Boundary Tests
# ---------------------------------------------------------------------------

class TestServiceBoundary:
    """Permanent and Temporary stores have NO shared state."""

    def test_separate_databases(self):
        """Permanent and workspace use separate database files."""
        import core.legal_brain.config as cfg

        perm_path = cfg.PERMANENT_DB_PATH
        ws_root = cfg.WORKSPACE_ROOT

        assert perm_path != ws_root
        assert "permanent" in str(perm_path).lower()
        assert "workspace" in str(ws_root).lower()

    def test_no_cross_import_from_workspace_to_permanent(self):
        """Workspace module does not import from permanent."""
        # This is verified by the module structure —
        # workspace/__init__.py has no import from ..permanent
        import ast
        import inspect
        from core.legal_brain import workspace

        ws_file = inspect.getfile(workspace)
        with open(ws_file) as f:
            tree = ast.parse(f.read())

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "permanent" in node.module:
                    pytest.fail(f"Workspace imports permanent: {node.module}")

    def test_no_cross_import_from_permanent_to_workspace(self):
        """Permanent module does not import from workspace."""
        import ast
        import inspect
        from core.legal_brain import permanent

        perm_file = inspect.getfile(permanent)
        with open(perm_file) as f:
            tree = ast.parse(f.read())

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "workspace" in node.module:
                    pytest.fail(f"Permanent imports workspace: {node.module}")


# ---------------------------------------------------------------------------
# Migration Tests
# ---------------------------------------------------------------------------

class TestMigration:
    """Migration from old Klaus to new Legal Brain."""

    def test_migration_status_no_klaus(self):
        """Migration status reports gracefully when Klaus is unavailable."""
        from core.legal_brain.migrate import get_migration_status

        status = get_migration_status()
        assert "klaus_documents" in status
        assert "legal_brain_documents" in status

    def test_migrate_sources_no_klaus(self):
        """Source migration handles missing Klaus gracefully."""
        from core.legal_brain.migrate import migrate_sources

        result = migrate_sources()
        assert "total" in result
        assert "migrated" in result
