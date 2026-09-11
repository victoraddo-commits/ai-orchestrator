"""Tests for /api/money/summary endpoint and TaskProgressMonitor."""

import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from core.task_progress_monitor import TaskProgressMonitor


# ── TaskProgressMonitor tests ─────────────────────────────────────────────


class TestTaskProgressMonitor:
    def test_add_and_count(self):
        m = TaskProgressMonitor("test")
        m.add_task("a", 5)
        m.add_task("b", 10)
        assert m.total_count == 2
        assert m.completed_count == 0

    def test_start_and_complete(self):
        m = TaskProgressMonitor("test")
        m.add_task("a", 5)
        m.start_task("a")
        assert m.tasks[0]["status"] == "in_progress"
        assert m.tasks[0]["started_at"] is not None
        m.complete_task("a")
        assert m.tasks[0]["status"] == "completed"
        assert m.completed_count == 1

    def test_fail_task(self):
        m = TaskProgressMonitor("test")
        m.add_task("a", 5)
        m.fail_task("a", "broke")
        assert m.tasks[0]["status"] == "failed"
        assert m.tasks[0]["failure_reason"] == "broke"

    def test_progress_pct(self):
        m = TaskProgressMonitor("test")
        m.add_task("a", 5)
        m.add_task("b", 5)
        m.complete_task("a")
        assert m.progress_pct == 50.0

    def test_progress_pct_empty(self):
        m = TaskProgressMonitor("test")
        assert m.progress_pct == 0

    def test_report_format(self):
        m = TaskProgressMonitor("Money Integration")
        m.add_task("UI card", 5)
        m.add_task("Backend API", 30)
        m.complete_task("UI card")
        m.start_task("Backend API")
        report = m.report()
        assert "Money Integration" in report
        assert "✅ UI card" in report
        assert "🔄 Backend API" in report
        assert "50%" in report

    def test_find_missing_raises(self):
        m = TaskProgressMonitor("test")
        with pytest.raises(KeyError, match="not found"):
            m._find("nonexistent")

    def test_save_writes_json(self, tmp_path):
        import core.task_progress_monitor as tpm
        original = tpm.PROGRESS_FILE
        tpm.PROGRESS_FILE = tmp_path / "progress.json"
        try:
            m = TaskProgressMonitor("test")
            m.add_task("a", 5)
            m.complete_task("a")
            data = json.loads(tpm.PROGRESS_FILE.read_text())
            assert data["task_name"] == "test"
            assert data["tasks"][0]["status"] == "completed"
        finally:
            tpm.PROGRESS_FILE = original

    def test_send_report_no_crash(self):
        m = TaskProgressMonitor("test")
        m.add_task("a", 5)
        with patch("core.task_progress_monitor.TaskProgressMonitor.send_report"):
            m.send_report()


# ── /api/money/summary endpoint tests ────────────────────────────────────


@pytest.fixture
def client():
    """Create a test client for the FastAPI app."""
    from fastapi.testclient import TestClient
    from core.api import app
    return TestClient(app)


class TestMoneySummaryEndpoint:
    @patch("core.api._get_proxy_client")
    def test_summary_live(self, mock_client_fn, client):
        mock_client = AsyncMock()

        pending_resp = MagicMock()
        pending_resp.status_code = 200
        pending_resp.json.return_value = [
            {"id": 1, "amount": 500, "status": "pending"},
            {"id": 2, "amount": 1000, "status": "pending"},
        ]

        treasury_resp = MagicMock()
        treasury_resp.status_code = 200
        treasury_resp.json.return_value = {"total": 125000}

        ops_resp = MagicMock()
        ops_resp.status_code = 200
        ops_resp.json.return_value = {"kai-legal-brain": {"balance": 45000}}

        mock_client.get = AsyncMock(side_effect=[pending_resp, treasury_resp, ops_resp])
        mock_client_fn.return_value = mock_client

        resp = client.get("/api/money/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["pending_requests"] == 2
        assert data["total_treasury"] == 125000
        assert data["source"] == "live"

    @patch("core.api._get_proxy_client")
    def test_summary_unavailable(self, mock_client_fn, client):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("connection refused"))
        mock_client_fn.return_value = mock_client

        # Clear cache
        import core.api
        core.api._money_summary_cache = {}

        resp = client.get("/api/money/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "unavailable"
        assert data["pending_requests"] == 0
