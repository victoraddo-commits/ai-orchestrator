"""Tests for Phase 18D: Research Session Logging."""

import os
import sys
import tempfile
import json
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestResearchSessions:
    """Research session logging and retrieval."""

    @pytest.fixture(autouse=True)
    def setup(self):
        import core.legal_brain.permanent as perm

        self.test_dir = Path(tempfile.mkdtemp())
        self.db_path = self.test_dir / "test_sessions.db"

        # Override get_db_path
        self._orig_get_db_path = perm.get_db_path
        perm.get_db_path = lambda: self.db_path

        # Init the permanent + sessions store
        from core.legal_brain.permanent.store import add_source
        perm.init_permanent_store(self.db_path)

        yield

        perm.get_db_path = self._orig_get_db_path
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_log_and_retrieve_session(self):
        """Session is logged and retrievable."""
        from core.legal_brain.sessions import log_research_session, get_session

        auths = [
            {"doc_id": "abc123", "title": "Constitution 1992", "citation": "Const. Art. 12", "similarity": 0.92},
            {"doc_id": "def456", "title": "Criminal Code", "citation": "Act 29, s.1", "similarity": 0.78},
        ]
        cited = ["abc123"]

        sid = log_research_session(
            user_id="user1",
            query_text="What are the fundamental human rights under Ghana law?",
            retrieved_authorities=auths,
            citations_used=cited,
            model_used="qwen3_coder",
            confidence=0.85,
            brain_version="0.1.0",
            search_strategy="semantic",
            session_duration_ms=1520,
            response_summary="The 1992 Constitution guarantees rights including...",
        )

        session = get_session(sid)
        assert session is not None
        assert session["user_id"] == "user1"
        assert "fundamental human rights" in session["query_text"]
        assert len(session["retrieved_authorities"]) == 2
        assert session["retrieved_authorities"][0]["title"] == "Constitution 1992"
        assert len(session["citations_used"]) == 1
        assert session["model_used"] == "qwen3_coder"
        assert session["confidence"] == 0.85

    def test_list_sessions_by_user(self):
        """Sessions can be filtered by user."""
        from core.legal_brain.sessions import log_research_session, list_sessions

        log_research_session("user_a", "Query A", [], [], model_used="model1")
        log_research_session("user_b", "Query B", [], [], model_used="model1")
        log_research_session("user_a", "Query C", [], [], model_used="model1")

        a_sessions = list_sessions(user_id="user_a")
        assert len(a_sessions) == 2

        b_sessions = list_sessions(user_id="user_b")
        assert len(b_sessions) == 1

    def test_list_sessions_by_date(self):
        """Sessions can be filtered by date range."""
        from core.legal_brain.sessions import log_research_session, list_sessions

        log_research_session("user1", "Old query", [], [])
        log_research_session("user1", "New query", [], [])

        # All sessions should be found with wide date range
        all_sessions = list_sessions(since="2020-01-01", until="2030-12-31")
        assert len(all_sessions) == 2

        # Future date should find none (using created_at which is auto-generated)
        no_sessions = list_sessions(since="2030-01-01")
        assert len(no_sessions) == 0

    def test_nonexistent_session(self):
        """Retrieving nonexistent session returns None."""
        from core.legal_brain.sessions import get_session
        assert get_session("nonexistent") is None

    def test_session_stats(self):
        """Stats aggregate correctly."""
        from core.legal_brain.sessions import log_research_session, get_session_stats

        log_research_session("user1", "Q1", [], [], model_used="model_a", confidence=0.9, session_duration_ms=500)
        log_research_session("user1", "Q2", [], [], model_used="model_a", confidence=0.7, session_duration_ms=1500)
        log_research_session("user2", "Q3", [], [], model_used="model_b", confidence=0.8, session_duration_ms=1000)

        stats = get_session_stats()
        assert stats["total_sessions"] == 3
        assert stats["avg_confidence"] == 0.8
        assert stats["avg_duration_ms"] == 1000

        user1_stats = get_session_stats(user_id="user1")
        assert user1_stats["total_sessions"] == 2

    def test_export_session_json(self):
        """JSON export produces valid report."""
        from core.legal_brain.sessions import log_research_session, export_session_json

        sid = log_research_session(
            "user1", "What is habeas corpus?",
            [{"doc_id": "x", "title": "Habeas Corpus Act", "citation": "Act 28", "similarity": 0.95}],
            ["x"],
            model_used="qwen3",
            confidence=0.92,
            response_summary="Habeas corpus is a writ requiring a person under arrest to be brought before a court.",
        )

        json_str = export_session_json(sid)
        assert json_str is not None

        report = json.loads(json_str)
        assert report["kai_legal_brain_research_report"]["query"]["text"] == "What is habeas corpus?"
        assert len(report["kai_legal_brain_research_report"]["authorities_retrieved"]) == 1

    def test_export_session_json_to_file(self):
        """JSON export writes to file."""
        from core.legal_brain.sessions import log_research_session, export_session_json

        sid = log_research_session("user1", "Test", [], [])
        out = self.test_dir / "report.json"

        result = export_session_json(sid, output_path=out)
        assert result is not None
        assert out.exists()

    def test_export_session_pdf(self):
        """PDF (text) export writes to file."""
        from core.legal_brain.sessions import log_research_session, export_session_pdf

        sid = log_research_session(
            "user1", "Test query",
            [{"doc_id": "1", "title": "Test Act", "citation": "Act 1"}],
            ["1"],
            response_summary="Test summary.",
        )
        out = self.test_dir / "report.txt"

        assert export_session_pdf(sid, out) is True
        content = out.read_text()
        assert "KAI LEGAL BRAIN" in content
        assert "Test query" in content
        assert "Test Act" in content

    def test_export_nonexistent_session(self):
        """Exporting nonexistent session returns None/False."""
        from core.legal_brain.sessions import export_session_json, export_session_pdf

        assert export_session_json("nonexistent") is None
        assert export_session_pdf("nonexistent", self.test_dir / "nope.txt") is False

    def test_session_appends_only(self):
        """Sessions are append-only — old sessions unchanged after adding new."""
        from core.legal_brain.sessions import log_research_session, get_session

        sid1 = log_research_session("user1", "First question", [], [])
        sid2 = log_research_session("user1", "Second question", [], [])

        s1 = get_session(sid1)
        assert s1["query_text"] == "First question"

        s2 = get_session(sid2)
        assert s2["query_text"] == "Second question"

    def test_jurisdiction_filtering(self):
        """Sessions are filterable by jurisdiction."""
        from core.legal_brain.sessions import log_research_session, list_sessions

        log_research_session("user1", "Ghana query", [], [], jurisdiction="Ghana")
        log_research_session("user1", "Also Ghana", [], [], jurisdiction="Ghana")

        sessions = list_sessions(jurisdiction="Ghana")
        assert len(sessions) == 2

        # Non-Ghana should return nothing (jurisdiction is enforced)
        sessions = list_sessions(jurisdiction="Nigeria")
        assert len(sessions) == 0
