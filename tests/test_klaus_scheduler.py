"""
Tests for KLAUS scheduler.

Tests the four-tier scheduling jobs (daily, weekly, monthly, quarterly),
seed initialization, and manual trigger functionality.
"""

from unittest.mock import patch, MagicMock, call
import pytest

from core.klaus.scheduler import (
    TIER_1_SEEDS,
    _ensure_seeds,
    daily_legislation_check,
    weekly_judgments_scan,
    monthly_full_refresh,
    quarterly_accuracy_verification,
    verify_existing_documents,
    trigger_job_now,
    start_scheduler,
    stop_scheduler,
)


class TestTier1Seeds:
    def test_seeds_are_valid(self):
        assert len(TIER_1_SEEDS) == 2
        for seed in TIER_1_SEEDS:
            assert "url" in seed
            assert "domain" in seed
            assert "tier" in seed
            assert "jurisdiction" in seed
            assert seed["jurisdiction"] == "Ghana"

    def test_seeds_match_approved_plan(self):
        domains = {s["domain"] for s in TIER_1_SEEDS}
        assert "parliament.gh" in domains
        assert "ghalii.org" in domains


class TestEnsureSeeds:
    def test_adds_all_seeds(self):
        with patch("core.klaus.db_manager.add_source", return_value=1):
            _ensure_seeds()

    def test_handles_add_failure_gracefully(self):
        with patch("core.klaus.db_manager.add_source", side_effect=[1, RuntimeError("duplicate")]):
            _ensure_seeds()


class TestDailyLegislationCheck:
    def test_scans_active_tier1_sources(self):
        import core.klaus.scheduler as sch
        with patch.object(sch, "list_sources", return_value=[
            {"domain": "parliament.gh", "tier": 1},
            {"domain": "ghalii.org", "tier": 2},
        ]), patch.object(sch, "get_failed_sources", return_value=[]), \
           patch.object(sch, "log_audit_event"):
            daily_legislation_check()

    def test_logs_broken_sources(self):
        import core.klaus.scheduler as sch
        with patch.object(sch, "list_sources", return_value=[{"domain": "parliament.gh", "tier": 1}]), \
             patch.object(sch, "get_failed_sources", return_value=[{"domain": "broken.gh"}]), \
             patch.object(sch, "log_audit_event") as mock_log:
            daily_legislation_check()
            mock_log.assert_any_call("failure", "warning", "1 broken sources found in daily scan")


class TestWeeklyJudgmentsScan:
    def test_scans_tier1_and_tier2(self):
        import core.klaus.scheduler as sch
        with patch.object(sch, "list_sources", side_effect=[
            [{"domain": "tier1.gh"}],
            [{"domain": "tier2.gh"}],
        ]), patch.object(sch, "get_failed_sources", return_value=[]), \
           patch.object(sch, "log_audit_event"):
            weekly_judgments_scan()

    def test_logs_failed_sources_with_domains(self):
        import core.klaus.scheduler as sch
        with patch.object(sch, "list_sources", side_effect=[[], []]), \
             patch.object(sch, "get_failed_sources", return_value=[
                 {"domain": "broken1.gh"},
                 {"domain": "broken2.gh"},
             ]), \
             patch.object(sch, "log_audit_event") as mock_log:
            weekly_judgments_scan()
            failure_calls = [c for c in mock_log.call_args_list if c[0][0] == "failure"]
            assert len(failure_calls) > 0


class TestMonthlyFullRefresh:
    def test_scans_all_active_sources(self):
        import core.klaus.scheduler as sch
        with patch.object(sch, "list_sources", return_value=[
            {"domain": "a.gh"}, {"domain": "b.gh"}, {"domain": "c.gh"},
        ]), patch.object(sch, "get_failed_sources", return_value=[]), \
           patch.object(sch, "log_audit_event"), \
           patch.object(sch, "_run_government_source_discovery"):
            monthly_full_refresh()


class TestQuarterlyAccuracyVerification:
    def test_logs_broken_sources(self):
        import core.klaus.scheduler as sch
        from contextlib import contextmanager

        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = {"ct": 0}

        @contextmanager
        def mock_get_cursor():
            yield mock_cur

        with patch("core.klaus.db_manager.get_cursor", side_effect=mock_get_cursor), \
             patch.object(sch, "get_failed_sources", return_value=[
                 {"domain": "broken1.gh"},
             ]), \
             patch.object(sch, "log_audit_event") as mock_log:
            quarterly_accuracy_verification()
            failure_calls = [c for c in mock_log.call_args_list
                             if c[0][0] == "failure"]
            assert len(failure_calls) > 0

    def test_logs_no_broken_when_clean(self):
        import core.klaus.scheduler as sch
        from contextlib import contextmanager

        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = {"ct": 0}

        @contextmanager
        def mock_get_cursor():
            yield mock_cur

        with patch("core.klaus.db_manager.get_cursor", side_effect=mock_get_cursor), \
             patch.object(sch, "get_failed_sources", return_value=[]), \
             patch.object(sch, "log_audit_event") as mock_log:
            quarterly_accuracy_verification()
            info_calls = [c for c in mock_log.call_args_list
                          if c[0][0] == "verification"]
            assert len(info_calls) > 0


class TestVerifyExistingDocuments:
    def test_verifies_approved_documents(self):
        import core.klaus.scheduler as sch
        from contextlib import contextmanager

        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [
            {"id": 1, "title": "Doc A"},
            {"id": 2, "title": "Doc B"},
        ]

        @contextmanager
        def mock_get_cursor():
            yield mock_cur

        with patch("core.klaus.db_manager.get_cursor", side_effect=mock_get_cursor), \
             patch.object(sch, "log_audit_event") as mock_log:
            verify_existing_documents()
            assert mock_log.call_count >= 2

    def test_handles_query_failure(self):
        import core.klaus.scheduler as sch
        with patch("core.klaus.db_manager.get_cursor", side_effect=RuntimeError("DB error")), \
             patch.object(sch, "log_audit_event"):
            verify_existing_documents()


class TestTriggerJobNow:
    def test_trigger_known_jobs(self):
        import core.klaus.scheduler as sch
        with patch.object(sch, "daily_legislation_check") as mock_fn:
            assert trigger_job_now("klaus_daily") is True
            mock_fn.assert_called_once()

        with patch.object(sch, "weekly_judgments_scan") as mock_fn:
            assert trigger_job_now("klaus_weekly") is True
            mock_fn.assert_called_once()

        with patch.object(sch, "monthly_full_refresh") as mock_fn:
            assert trigger_job_now("klaus_monthly") is True
            mock_fn.assert_called_once()

        with patch.object(sch, "quarterly_accuracy_verification") as mock_fn:
            assert trigger_job_now("klaus_quarterly") is True
            mock_fn.assert_called_once()

    def test_trigger_unknown_job(self):
        assert trigger_job_now("klaus_unknown") is False
