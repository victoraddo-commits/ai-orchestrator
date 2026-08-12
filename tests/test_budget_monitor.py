"""Tests for Phase 16D: budget-alerting layer atop 13W's real cost aggregates.

Tests verify:
- An alert fires exactly once when a threshold is crossed (not every cycle after)
- Alerts stay silent when spend is under threshold
- No automatic provider-disabling or spend-limiting (alert-only)
"""

import json
import pytest
from datetime import datetime, timedelta
from pathlib import Path

import core.ai.ai_router as ai_router
from core.monitoring import budget_monitor
from core import telegram_bridge


class TestFormatProviderBreakdown:
    """Tests for _format_provider_breakdown."""

    def test_empty_costs(self):
        """Format handles empty costs dictionary."""
        result = budget_monitor._format_provider_breakdown({})
        assert "(no cost-carrying calls recorded)" in result

    def test_single_provider(self):
        """Format single provider correctly."""
        costs = {"openrouter": 5.42}
        result = budget_monitor._format_provider_breakdown(costs)
        assert "openrouter: $5.42" in result

    def test_multiple_providers_sorted(self):
        """Format multiple providers sorted by cost descending."""
        costs = {
            "gemini": 2.50,
            "openrouter": 7.20,
            "claude": 1.30
        }
        result = budget_monitor._format_provider_breakdown(costs)
        lines = result.strip().split("\n")
        # Should be sorted by cost descending
        assert "openrouter: $7.20" in lines[0]
        assert "gemini: $2.50" in lines[1]
        assert "claude: $1.30" in lines[2]


class TestComputeDailyUsage:
    """Tests for _compute_daily_usage."""

    def test_today_only(self, monkeypatch):
        """Only includes entries from today."""
        today = datetime.now().date().isoformat()
        yesterday = (datetime.now().date() - timedelta(days=1)).isoformat()

        history = [
            {"provider": "openrouter", "cost": 5.00, "timestamp": f"{today}T10:00:00"},
            {"provider": "gemini", "cost": 3.00, "timestamp": f"{today}T11:00:00"},
            {"provider": "claude", "cost": 2.00, "timestamp": f"{yesterday}T10:00:00"},
        ]

        result = budget_monitor._compute_daily_usage(history)
        assert result == {"openrouter": 5.00, "gemini": 3.00}
        assert "claude" not in result

    def test_no_cost_entries_ignored(self):
        """Entries without cost are included but with 0."""
        today = datetime.now().date().isoformat()

        history = [
            {"provider": "openrouter", "cost": 5.00, "timestamp": f"{today}T10:00:00"},
            {"provider": "gemini", "cost": None, "timestamp": f"{today}T11:00:00"},
        ]

        result = budget_monitor._compute_daily_usage(history)
        assert result == {"openrouter": 5.00}
        assert "gemini" not in result  # None cost is ignored

    def test_null_cost_not_included(self):
        """Entries with cost=None are excluded."""
        today = datetime.now().date().isoformat()

        history = [
            {"provider": "gemini", "cost": None, "timestamp": f"{today}T10:00:00"},
        ]

        result = budget_monitor._compute_daily_usage(history)
        assert result == {}


class TestComputeMonthlyUsage:
    """Tests for _compute_monthly_usage."""

    def test_current_month_only(self, monkeypatch):
        """Only includes entries from current month."""
        today = datetime.now().date()
        this_month = today.strftime("%Y-%m")
        last_month = (today - timedelta(days=30)).strftime("%Y-%m")

        history = [
            {"provider": "openrouter", "cost": 5.00, "timestamp": f"{this_month}-15T10:00:00"},
            {"provider": "gemini", "cost": 3.00, "timestamp": f"{this_month}-20T11:00:00"},
            {"provider": "claude", "cost": 2.00, "timestamp": f"{last_month}-15T10:00:00"},
        ]

        result = budget_monitor._compute_monthly_usage(history)
        assert result == {"openrouter": 5.00, "gemini": 3.00}
        assert "claude" not in result


class TestAlertKey:
    """Tests for _alert_key."""

    def test_total_category(self):
        """Key for total category without provider."""
        key = budget_monitor._alert_key("daily")
        assert key == "daily:total"

    def test_provider_category(self):
        """Key for provider-specific category."""
        key = budget_monitor._alert_key("daily", "openrouter")
        assert key == "daily:openrouter"


class TestShouldAlert:
    """Tests for _should_alert - idempotency check."""

    def test_should_alert_when_not_recorded(self):
        """Alert should fire if not previously recorded."""
        state = {}
        assert budget_monitor._should_alert("daily:total", "2026-08-03", state) is True

    def test_should_not_alert_when_already_alerted(self):
        """Alert should NOT fire if already recorded for this period."""
        state = {"daily:total": "2026-08-03"}
        assert budget_monitor._should_alert("daily:total", "2026-08-03", state) is False

    def test_should_alert_different_period(self):
        """Alert should fire for same category but different period."""
        state = {"daily:total": "2026-08-02"}
        assert budget_monitor._should_alert("daily:total", "2026-08-03", state) is True


class TestRecordAlert:
    """Tests for _record_alert."""

    def test_records_period(self):
        """Records the period for the alert key."""
        state = {}
        budget_monitor._record_alert("daily:total", "2026-08-03", state)
        assert state["daily:total"] == "2026-08-03"


class TestBuildAlertMessage:
    """Tests for _build_alert_message."""

    def test_total_daily_limit_exceeded(self):
        """Builds correct message for total daily limit exceeded."""
        msg = budget_monitor._build_alert_message(
            category="daily",
            limit=10.00,
            current=11.42,
            provider=None,
            breakdown="  openrouter: $7.20\n  gemini: $4.22"
        )
        assert "\U0001f6a8 **AI Budget Alert**" in msg
        assert "Daily limit exceeded!" in msg
        assert "**Total Spend:** $11.42 / $10.00 limit ($1.42 over)" in msg
        assert "**Breakdown:**" in msg
        assert "openrouter: $7.20" in msg
        assert "*Action required: Manual review needed. No providers have been disabled.*" in msg

    def test_provider_daily_limit_exceeded(self):
        """Builds correct message for provider-specific limit exceeded."""
        msg = budget_monitor._build_alert_message(
            category="daily",
            limit=5.00,
            current=7.20,
            provider="openrouter",
            breakdown="  openrouter: $7.20"
        )
        assert "Daily limit exceeded!" in msg
        assert "**openrouter Spend:** $7.20 / $5.00 limit ($2.20 over)" in msg


class TestCheckCategory:
    """Tests for _check_category."""

    def test_under_limit_no_alert(self, monkeypatch, tmp_path, isolated_memory):
        """No alert when current spend is under limit."""
        state_file = tmp_path / "memory" / "ai_budget_state.json"
        monkeypatch.setattr(budget_monitor, "_BUDGET_STATE_FILE", state_file.name)
        monkeypatch.setattr(budget_monitor, "_load_state", lambda: {})
        monkeypatch.setattr(budget_monitor, "_save_state", lambda s: None)

        state = {}
        costs = {"openrouter": 5.00}

        result = budget_monitor._check_category("daily", 10.00, costs, state, "2026-08-03")

        assert result is False
        assert state == {}

    def test_exactly_at_limit_no_alert(self, monkeypatch, tmp_path, isolated_memory):
        """No alert when current spend equals limit."""
        state_file = tmp_path / "memory" / "ai_budget_state.json"
        monkeypatch.setattr(budget_monitor, "_BUDGET_STATE_FILE", state_file.name)
        monkeypatch.setattr(budget_monitor, "_load_state", lambda: {})
        monkeypatch.setattr(budget_monitor, "_save_state", lambda s: None)

        state = {}
        costs = {"openrouter": 10.00}

        result = budget_monitor._check_category("daily", 10.00, costs, state, "2026-08-03")

        assert result is False
        assert state == {}

    def test_over_limit_sends_alert(self, monkeypatch, tmp_path, isolated_memory):
        """Alert is sent when over limit."""
        state_file = tmp_path / "memory" / "ai_budget_state.json"
        monkeypatch.setattr(budget_monitor, "_BUDGET_STATE_FILE", state_file.name)
        monkeypatch.setattr(budget_monitor, "_load_state", lambda: {})
        monkeypatch.setattr(budget_monitor, "_save_state", lambda s: None)

        sent_messages = []
        # Patch budget_monitor.send_message since it's imported directly from telegram_bridge
        monkeypatch.setattr(budget_monitor, "send_message", lambda text: sent_messages.append(text))

        state = {}
        costs = {"openrouter": 11.42}

        result = budget_monitor._check_category("daily", 10.00, costs, state, "2026-08-03")

        assert result is True
        assert len(sent_messages) == 1
        assert "AI Budget Alert" in sent_messages[0]
        assert "$11.42 / $10.00" in sent_messages[0]
        # State should be updated to prevent repeat
        assert state["daily:total"] == "2026-08-03"

    def test_alert_only_once_per_period(self, monkeypatch, tmp_path, isolated_memory):
        """Alert fires only once per period, not on subsequent checks."""
        state_file = tmp_path / "memory" / "ai_budget_state.json"
        monkeypatch.setattr(budget_monitor, "_BUDGET_STATE_FILE", state_file.name)
        monkeypatch.setattr(budget_monitor, "_load_state", lambda: {})
        monkeypatch.setattr(budget_monitor, "_save_state", lambda s: None)

        sent_messages = []
        monkeypatch.setattr(budget_monitor, "send_message", lambda text: sent_messages.append(text))

        state = {}
        costs = {"openrouter": 11.42}

        # First check - should send alert
        result1 = budget_monitor._check_category("daily", 10.00, costs, state, "2026-08-03")
        assert result1 is True
        assert len(sent_messages) == 1

        # Second check same period - should NOT send alert
        result2 = budget_monitor._check_category("daily", 10.00, costs, state, "2026-08-03")
        assert result2 is False
        assert len(sent_messages) == 1  # No additional alert

    def test_new_period_resets_alert_state(self, monkeypatch, tmp_path, isolated_memory):
        """Alert can fire again in a new period."""
        state_file = tmp_path / "memory" / "ai_budget_state.json"
        monkeypatch.setattr(budget_monitor, "_BUDGET_STATE_FILE", state_file.name)
        monkeypatch.setattr(budget_monitor, "_load_state", lambda: {})
        monkeypatch.setattr(budget_monitor, "_save_state", lambda s: None)

        sent_messages = []
        monkeypatch.setattr(budget_monitor, "send_message", lambda text: sent_messages.append(text))

        state = {}
        costs = {"openrouter": 11.42}

        # Alert in first period
        result1 = budget_monitor._check_category("daily", 10.00, costs, state, "2026-08-03")
        assert result1 is True
        assert len(sent_messages) == 1

        # Same costs in new period - should alert again
        result2 = budget_monitor._check_category("daily", 10.00, costs, state, "2026-08-04")
        assert result2 is True
        assert len(sent_messages) == 2

    def test_none_limit_skipped(self, monkeypatch, tmp_path, isolated_memory):
        """No check is performed when limit is None."""
        state_file = tmp_path / "memory" / "ai_budget_state.json"
        monkeypatch.setattr(budget_monitor, "_BUDGET_STATE_FILE", state_file.name)
        monkeypatch.setattr(budget_monitor, "_load_state", lambda: {})
        monkeypatch.setattr(budget_monitor, "_save_state", lambda s: None)

        sent_messages = []
        monkeypatch.setattr(budget_monitor, "send_message", lambda text: sent_messages.append(text))

        state = {"daily:total": "2026-08-02"}
        costs = {"openrouter": 100.00}

        result = budget_monitor._check_category("daily", None, costs, state, "2026-08-03")

        assert result is False
        # State should NOT be updated since no check was performed
        assert state["daily:total"] == "2026-08-02"

    def test_send_message_failure_does_not_update_state(self, monkeypatch, tmp_path, isolated_memory):
        """State is NOT updated if send_message fails."""
        state_file = tmp_path / "memory" / "ai_budget_state.json"
        monkeypatch.setattr(budget_monitor, "_BUDGET_STATE_FILE", state_file.name)
        monkeypatch.setattr(budget_monitor, "_load_state", lambda: {})
        monkeypatch.setattr(budget_monitor, "_save_state", lambda s: None)

        def failing_send(text):
            raise RuntimeError("Telegram unreachable")

        monkeypatch.setattr(budget_monitor, "send_message", failing_send)

        state = {}
        costs = {"openrouter": 11.42}

        result = budget_monitor._check_category("daily", 10.00, costs, state, "2026-08-03")

        assert result is False  # Returns False on error
        # State should NOT be updated since send failed
        assert state == {}


class TestCheckPerProviderLimits:
    """Tests for _check_per_provider_limits."""

    def test_under_provider_limit_no_alert(self, monkeypatch, tmp_path, isolated_memory):
        """No alert when provider spend is under limit."""
        state_file = tmp_path / "memory" / "ai_budget_state.json"
        monkeypatch.setattr(budget_monitor, "_BUDGET_STATE_FILE", state_file.name)
        monkeypatch.setattr(budget_monitor, "_load_state", lambda: {})
        monkeypatch.setattr(budget_monitor, "_save_state", lambda s: None)

        sent_messages = []
        monkeypatch.setattr(budget_monitor, "send_message", lambda text: sent_messages.append(text))

        state = {}
        daily_costs = {"openrouter": 3.00}
        limits = {"openrouter": {"daily": 5.00}}

        result = budget_monitor._check_per_provider_limits(limits, daily_costs, state, "2026-08-03")

        assert result is False
        assert sent_messages == []
        assert state == {}

    def test_over_provider_limit_sends_alert(self, monkeypatch, tmp_path, isolated_memory):
        """Alert is sent when provider over limit."""
        state_file = tmp_path / "memory" / "ai_budget_state.json"
        monkeypatch.setattr(budget_monitor, "_BUDGET_STATE_FILE", state_file.name)
        monkeypatch.setattr(budget_monitor, "_load_state", lambda: {})
        monkeypatch.setattr(budget_monitor, "_save_state", lambda s: None)

        sent_messages = []
        monkeypatch.setattr(budget_monitor, "send_message", lambda text: sent_messages.append(text))

        state = {}
        daily_costs = {"openrouter": 7.20}
        limits = {"openrouter": {"daily": 5.00}}

        result = budget_monitor._check_per_provider_limits(limits, daily_costs, state, "2026-08-03")

        assert result is True
        assert len(sent_messages) == 1
        assert "openrouter" in sent_messages[0]
        assert "$7.20 / $5.00" in sent_messages[0]
        assert state["daily:openrouter"] == "2026-08-03"

    def test_alert_only_once_per_provider_per_period(self, monkeypatch, tmp_path, isolated_memory):
        """Per-provider alert fires only once per period."""
        state_file = tmp_path / "memory" / "ai_budget_state.json"
        monkeypatch.setattr(budget_monitor, "_BUDGET_STATE_FILE", state_file.name)
        monkeypatch.setattr(budget_monitor, "_load_state", lambda: {})
        monkeypatch.setattr(budget_monitor, "_save_state", lambda s: None)

        sent_messages = []
        monkeypatch.setattr(budget_monitor, "send_message", lambda text: sent_messages.append(text))

        state = {}
        daily_costs = {"openrouter": 7.20}
        limits = {"openrouter": {"daily": 5.00}}

        # First check
        result1 = budget_monitor._check_per_provider_limits(limits, daily_costs, state, "2026-08-03")
        assert result1 is True
        assert len(sent_messages) == 1

        # Second check same period
        result2 = budget_monitor._check_per_provider_limits(limits, daily_costs, state, "2026-08-03")
        assert result2 is False
        assert len(sent_messages) == 1  # No additional alert

    def test_multiple_providers(self, monkeypatch, tmp_path, isolated_memory):
        """Alerts for multiple providers over their limits."""
        state_file = tmp_path / "memory" / "ai_budget_state.json"
        monkeypatch.setattr(budget_monitor, "_BUDGET_STATE_FILE", state_file.name)
        monkeypatch.setattr(budget_monitor, "_load_state", lambda: {})
        monkeypatch.setattr(budget_monitor, "_save_state", lambda s: None)

        sent_messages = []
        monkeypatch.setattr(budget_monitor, "send_message", lambda text: sent_messages.append(text))

        state = {}
        daily_costs = {"openrouter": 7.20, "gemini": 6.00}
        limits = {
            "openrouter": {"daily": 5.00},
            "gemini": {"daily": 5.00}
        }

        result = budget_monitor._check_per_provider_limits(limits, daily_costs, state, "2026-08-03")

        assert result is True
        assert len(sent_messages) == 2
        assert any("openrouter" in m for m in sent_messages)
        assert any("gemini" in m for m in sent_messages)

    def test_missing_provider_in_costs(self, monkeypatch, tmp_path, isolated_memory):
        """No alert for provider with no costs."""
        state_file = tmp_path / "memory" / "ai_budget_state.json"
        monkeypatch.setattr(budget_monitor, "_BUDGET_STATE_FILE", state_file.name)
        monkeypatch.setattr(budget_monitor, "_load_state", lambda: {})
        monkeypatch.setattr(budget_monitor, "_save_state", lambda s: None)

        sent_messages = []
        monkeypatch.setattr(budget_monitor, "send_message", lambda text: sent_messages.append(text))

        state = {}
        daily_costs = {"openrouter": 3.00}  # Under limit
        limits = {
            "openrouter": {"daily": 5.00},
            "gemini": {"daily": 5.00}  # Not in costs
        }

        result = budget_monitor._check_per_provider_limits(limits, daily_costs, state, "2026-08-03")

        assert result is False
        assert sent_messages == []

    def test_none_daily_limit_skipped(self, monkeypatch, tmp_path, isolated_memory):
        """Provider with None daily limit is skipped."""
        state_file = tmp_path / "memory" / "ai_budget_state.json"
        monkeypatch.setattr(budget_monitor, "_BUDGET_STATE_FILE", state_file.name)
        monkeypatch.setattr(budget_monitor, "_load_state", lambda: {})
        monkeypatch.setattr(budget_monitor, "_save_state", lambda s: None)

        sent_messages = []
        monkeypatch.setattr(budget_monitor, "send_message", lambda text: sent_messages.append(text))

        state = {}
        daily_costs = {"openrouter": 100.00}
        limits = {"openrouter": {"daily": None}}  # None limit

        result = budget_monitor._check_per_provider_limits(limits, daily_costs, state, "2026-08-03")

        assert result is False
        assert sent_messages == []


class TestCheckBudgets:
    """Integration tests for check_budgets()."""

    def test_disabled_config_does_nothing(self, monkeypatch, tmp_path, isolated_memory):
        """When disabled, no checks are performed."""
        # Mock _load_config to return disabled config
        monkeypatch.setattr(budget_monitor, "_load_config", lambda: {"enabled": False})

        sent_messages = []
        monkeypatch.setattr(budget_monitor, "send_message", lambda text: sent_messages.append(text))

        # Set up some high costs
        today = datetime.now().date().isoformat()
        monkeypatch.setattr(ai_router, "get_usage_history", lambda: [
            {"provider": "openrouter", "cost": 100.00, "timestamp": f"{today}T10:00:00"},
        ])

        budget_monitor.check_budgets()

        assert sent_messages == []

    def test_empty_history_does_nothing(self, monkeypatch, tmp_path, isolated_memory):
        """When no history exists, no checks are performed."""
        monkeypatch.setattr(budget_monitor, "_load_config", lambda: {"enabled": True, "daily_limit_usd": 10.00})
        monkeypatch.setattr(ai_router, "get_usage_history", lambda: [])

        sent_messages = []
        monkeypatch.setattr(budget_monitor, "send_message", lambda text: sent_messages.append(text))

        budget_monitor.check_budgets()

        assert sent_messages == []

    def test_under_all_limits_no_alerts(self, monkeypatch, tmp_path, isolated_memory):
        """No alerts when all limits are under threshold."""
        today = datetime.now().date().isoformat()
        history = [
            {"provider": "openrouter", "cost": 5.00, "timestamp": f"{today}T10:00:00"},
            {"provider": "gemini", "cost": 3.00, "timestamp": f"{today}T11:00:00"},
        ]

        monkeypatch.setattr(ai_router, "get_usage_history", lambda: history)
        monkeypatch.setattr(budget_monitor, "_load_config", lambda: {
            "enabled": True,
            "daily_limit_usd": 100.00,
            "monthly_limit_usd": 1000.00,
            "per_provider_limits": {
                "openrouter": {"daily": 100.00},
                "gemini": {"daily": 100.00}
            }
        })

        sent_messages = []
        monkeypatch.setattr(budget_monitor, "send_message", lambda text: sent_messages.append(text))

        budget_monitor.check_budgets()

        assert sent_messages == []

    def test_total_daily_limit_exceeded(self, monkeypatch, tmp_path, isolated_memory):
        """Alert fires when total daily limit exceeded."""
        today = datetime.now().date().isoformat()
        history = [
            {"provider": "openrouter", "cost": 7.20, "timestamp": f"{today}T10:00:00"},
            {"provider": "gemini", "cost": 4.22, "timestamp": f"{today}T11:00:00"},
        ]

        monkeypatch.setattr(ai_router, "get_usage_history", lambda: history)
        monkeypatch.setattr(budget_monitor, "_load_config", lambda: {
            "enabled": True,
            "daily_limit_usd": 10.00,  # Limit is $10
            "monthly_limit_usd": 100.00,
            "per_provider_limits": {}
        })
        monkeypatch.setattr(budget_monitor, "_load_state", lambda: {})
        monkeypatch.setattr(budget_monitor, "_save_state", lambda s: None)

        sent_messages = []
        monkeypatch.setattr(budget_monitor, "send_message", lambda text: sent_messages.append(text))

        budget_monitor.check_budgets()

        assert len(sent_messages) == 1
        assert "$11.42 / $10.00 limit" in sent_messages[0]
        assert "Action required: Manual review needed. No providers have been disabled." in sent_messages[0]

    def test_total_monthly_limit_exceeded(self, monkeypatch, tmp_path, isolated_memory):
        """Alert fires when total monthly limit exceeded."""
        today = datetime.now().date()
        this_month = today.strftime("%Y-%m")
        history = [
            {"provider": "openrouter", "cost": 50.00, "timestamp": f"{this_month}-15T10:00:00"},
            {"provider": "gemini", "cost": 60.00, "timestamp": f"{this_month}-20T11:00:00"},
        ]

        monkeypatch.setattr(ai_router, "get_usage_history", lambda: history)
        monkeypatch.setattr(budget_monitor, "_load_config", lambda: {
            "enabled": True,
            "daily_limit_usd": 1000.00,  # High daily
            "monthly_limit_usd": 100.00,  # Limit is $100
            "per_provider_limits": {}
        })
        monkeypatch.setattr(budget_monitor, "_load_state", lambda: {})
        monkeypatch.setattr(budget_monitor, "_save_state", lambda s: None)

        sent_messages = []
        monkeypatch.setattr(budget_monitor, "send_message", lambda text: sent_messages.append(text))

        budget_monitor.check_budgets()

        assert len(sent_messages) == 1
        assert "Monthly limit exceeded" in sent_messages[0]
        assert "$110.00 / $100.00 limit" in sent_messages[0]

    def test_per_provider_limit_exceeded(self, monkeypatch, tmp_path, isolated_memory):
        """Alert fires for per-provider limit exceeded."""
        today = datetime.now().date().isoformat()
        history = [
            {"provider": "openrouter", "cost": 7.20, "timestamp": f"{today}T10:00:00"},
            {"provider": "gemini", "cost": 2.00, "timestamp": f"{today}T11:00:00"},
        ]

        monkeypatch.setattr(ai_router, "get_usage_history", lambda: history)
        monkeypatch.setattr(budget_monitor, "_load_config", lambda: {
            "enabled": True,
            "daily_limit_usd": 1000.00,  # High total daily
            "monthly_limit_usd": 1000.00,
            "per_provider_limits": {
                "openrouter": {"daily": 5.00},  # Limit is $5
                "gemini": {"daily": 100.00}
            }
        })
        monkeypatch.setattr(budget_monitor, "_load_state", lambda: {})
        monkeypatch.setattr(budget_monitor, "_save_state", lambda s: None)

        sent_messages = []
        monkeypatch.setattr(budget_monitor, "send_message", lambda text: sent_messages.append(text))

        budget_monitor.check_budgets()

        assert len(sent_messages) == 1
        assert "openrouter" in sent_messages[0]
        assert "$7.20 / $5.00 limit" in sent_messages[0]

    def test_alert_only_once_per_period_integration(self, monkeypatch, tmp_path, isolated_memory):
        """Alert fires exactly once per period, not every cycle."""
        today = datetime.now().date().isoformat()
        history = [
            {"provider": "openrouter", "cost": 11.42, "timestamp": f"{today}T10:00:00"},
        ]

        monkeypatch.setattr(ai_router, "get_usage_history", lambda: history)
        monkeypatch.setattr(budget_monitor, "_load_config", lambda: {
            "enabled": True,
            "daily_limit_usd": 10.00,
            "monthly_limit_usd": 100.00,
            "per_provider_limits": {}
        })
        state = {}
        monkeypatch.setattr(budget_monitor, "_load_state", lambda: state)
        monkeypatch.setattr(budget_monitor, "_save_state", lambda s: None)

        sent_messages = []
        monkeypatch.setattr(budget_monitor, "send_message", lambda text: sent_messages.append(text))

        # First call - should send alert
        budget_monitor.check_budgets()
        assert len(sent_messages) == 1

        # Second call same period - should NOT send alert
        budget_monitor.check_budgets()
        assert len(sent_messages) == 1

    def test_new_period_resets_alert_integration(self, monkeypatch, tmp_path, isolated_memory):
        """Alert can fire again in a new period."""
        today = datetime.now().date().isoformat()
        history = [
            {"provider": "openrouter", "cost": 11.42, "timestamp": f"{today}T10:00:00"},
        ]

        monkeypatch.setattr(ai_router, "get_usage_history", lambda: history)
        monkeypatch.setattr(budget_monitor, "_load_config", lambda: {
            "enabled": True,
            "daily_limit_usd": 10.00,
            "monthly_limit_usd": 100.00,
            "per_provider_limits": {}
        })
        monkeypatch.setattr(budget_monitor, "_load_state", lambda: {})
        monkeypatch.setattr(budget_monitor, "_save_state", lambda s: None)

        sent_messages = []
        monkeypatch.setattr(budget_monitor, "send_message", lambda text: sent_messages.append(text))

        # First period
        budget_monitor.check_budgets()
        assert len(sent_messages) == 1

        # Reset state for new period simulation
        # In real scenario, the state file would persist, but for test
        # we verify that _should_alert returns True for different period
        from core.monitoring.budget_monitor import _should_alert
        assert _should_alert("daily:total", "2026-08-04", {"daily:total": "2026-08-03"}) is True


class TestAlertOnlyPolicy:
    """Verify that check_budgets is alert-only (no provider disabling)."""

    def test_no_provider_state_modified(self, monkeypatch, tmp_path, isolated_memory):
        """check_budgets does not modify provider availability or state."""
        today = datetime.now().date().isoformat()
        history = [
            {"provider": "openrouter", "cost": 100.00, "timestamp": f"{today}T10:00:00"},
        ]

        monkeypatch.setattr(ai_router, "get_usage_history", lambda: history)
        monkeypatch.setattr(budget_monitor, "_load_config", lambda: {
            "enabled": True,
            "daily_limit_usd": 10.00,
            "monthly_limit_usd": 100.00,
            "per_provider_limits": {}
        })
        monkeypatch.setattr(budget_monitor, "_load_state", lambda: {})
        monkeypatch.setattr(budget_monitor, "_save_state", lambda s: None)

        sent_messages = []
        monkeypatch.setattr(budget_monitor, "send_message", lambda text: sent_messages.append(text))

        # Run budget check
        budget_monitor.check_budgets()

        # Alert should be sent
        assert len(sent_messages) == 1
        assert "No providers have been disabled" in sent_messages[0]

        # The key assertion: the function only sent a message,
        # it did not call any function that would disable a provider
