"""Tests for the Provider Health Monitor.

Part of KAI Phase 1 - Autonomous Provider Failover Implementation.

Verifies:
- Monitor lifecycle (start/stop)
- Provider health checking
- Health storage and retrieval
- Telegram alert generation
- API endpoint integration
"""

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(autouse=True)
def reset_state():
    """Reset monitor and health state before each test."""
    import core.ai.circuit_breaker as cb
    import core.ai.provider_health as ph
    from core.provider_health_monitor import stop_monitor
    from core.memory import save

    # Stop any running monitor
    stop_monitor()

    # Reset state
    cb.reset_all_breakers()

    # Clear health snapshots
    save("provider_quota.json", {})
    save("provider_health_monitor_state.json", {})

    yield

    # Cleanup
    stop_monitor()
    cb.reset_all_breakers()


class TestMonitorLifecycle:
    """Test monitor start/stop and lifecycle."""

    def test_monitor_can_be_started(self):
        """Monitor starts successfully and runs in background."""
        from core.provider_health_monitor import start_monitor, get_monitor

        monitor = start_monitor()

        assert monitor is not None
        assert monitor.is_running
        assert get_monitor() is monitor

        monitor.stop()

    def test_monitor_can_be_stopped(self):
        """Monitor stops gracefully."""
        from core.provider_health_monitor import start_monitor

        monitor = start_monitor()
        assert monitor.is_running

        monitor.stop()
        assert not monitor.is_running

    def test_monitor_double_start_is_noop(self):
        """Starting monitor twice returns same instance."""
        from core.provider_health_monitor import start_monitor, get_monitor

        monitor1 = start_monitor()
        monitor2 = start_monitor()

        assert monitor1 is monitor2
        assert get_monitor() is monitor1

        monitor1.stop()

    def test_monitor_increments_check_count(self):
        """Monitor increments check_count on each cycle."""
        from core.provider_health_monitor import ProviderHealthMonitor

        monitor = ProviderHealthMonitor(check_interval=0.1)  # Fast interval for testing
        monitor.start()

        initial_count = monitor.check_count
        time.sleep(0.3)  # Wait for ~2-3 checks

        assert monitor.check_count > initial_count

        monitor.stop()


class TestProviderHealthChecking:
    """Test provider health checking logic."""

    def test_check_unavailable_provider(self):
        """Unavailable provider (no credentials) reports 'unavailable'."""
        from core.provider_health_monitor import ProviderHealthMonitor

        monitor = ProviderHealthMonitor()

        # Mock provider with no credentials
        provider_info = {
            'available': False,
            'enabled': True,
        }

        status = monitor._check_provider("test_provider", provider_info)

        assert status['health'] == 'unavailable'
        assert 'credentials' in status['reason'].lower()

    def test_check_disabled_provider(self):
        """Disabled provider reports 'disabled'."""
        from core.provider_health_monitor import ProviderHealthMonitor

        monitor = ProviderHealthMonitor()

        provider_info = {
            'available': True,
            'enabled': False,
        }

        status = monitor._check_provider("test_provider", provider_info)

        assert status['health'] == 'disabled'
        assert 'disabled' in status['reason'].lower()

    def test_check_circuit_open_provider(self):
        """Provider with open circuit breaker reports 'circuit_open'."""
        import core.ai.circuit_breaker as cb
        from core.provider_health_monitor import ProviderHealthMonitor

        # Trip circuit breaker
        cb.record_failure("test_provider")
        cb.record_failure("test_provider")
        cb.record_failure("test_provider")

        assert cb.is_open("test_provider")

        monitor = ProviderHealthMonitor()
        provider_info = {
            'available': True,
            'enabled': True,
        }

        status = monitor._check_provider("test_provider", provider_info)

        assert status['health'] == 'circuit_open'
        assert status['consecutive_failures'] == 3

    def test_check_quota_exceeded_provider(self):
        """Provider with quota exceeded reports 'quota_exceeded'."""
        import core.ai.provider_health as ph
        from core.provider_health_monitor import ProviderHealthMonitor

        # Set quota exceeded
        ph.capture_quota_exceeded("test_provider", "Test quota limit")

        monitor = ProviderHealthMonitor()
        provider_info = {
            'available': True,
            'enabled': True,
        }

        status = monitor._check_provider("test_provider", provider_info)

        assert status['health'] == 'quota_exceeded'
        assert status['percent_remaining'] == 0

    def test_check_healthy_provider(self):
        """Healthy provider reports 'ok'."""
        import core.ai.provider_health as ph
        from core.provider_health_monitor import ProviderHealthMonitor

        # Set healthy status
        ph.record_quota_snapshot("test_provider", status="ok", percent_remaining=75.0)

        monitor = ProviderHealthMonitor()
        provider_info = {
            'available': True,
            'enabled': True,
        }

        status = monitor._check_provider("test_provider", provider_info)

        assert status['health'] == 'ok'
        assert status['percent_remaining'] == 75.0


class TestHealthStorage:
    """Test health snapshot storage."""

    def test_health_is_stored(self):
        """Health status is stored in provider_health module."""
        import core.ai.provider_health as ph
        from core.provider_health_monitor import ProviderHealthMonitor

        monitor = ProviderHealthMonitor()

        status = {
            'health': 'ok',
            'percent_remaining': 80.0,
            'checked_at': datetime.now().isoformat()
        }

        monitor._store_health("test_provider", status)

        snapshot = ph.get_quota_snapshot("test_provider")
        assert snapshot is not None
        assert snapshot['status'] == 'ok'
        assert snapshot['percent_remaining'] == 80.0

    def test_health_is_retrieved(self):
        """Stored health can be retrieved."""
        import core.ai.provider_health as ph
        from core.provider_health_monitor import ProviderHealthMonitor

        # Store initial health
        ph.record_quota_snapshot("test_provider", status="ok", percent_remaining=90.0)

        # Retrieve
        snapshot = ph.get_quota_snapshot("test_provider")
        assert snapshot['status'] == 'ok'
        assert snapshot['percent_remaining'] == 90.0


class TestTelegramAlerts:
    """Test Telegram alert generation."""

    @patch('core.provider_health_monitor.send_telegram_alert')
    def test_failure_sends_alert(self, mock_send):
        """Provider failure triggers Telegram alert."""
        from core.provider_health_monitor import ProviderHealthMonitor

        monitor = ProviderHealthMonitor()

        status = {
            'health': 'quota_exceeded',
            'percent_remaining': 0,
            'detail': 'Limit reached',
            'checked_at': datetime.now().isoformat()
        }

        monitor._notify_failure("test_provider", status)

        assert mock_send.called
        call_args = mock_send.call_args[0][0]
        assert "PROVIDER HEALTH ALERT" in call_args
        assert "test_provider" in call_args
        assert "quota_exceeded" in call_args

    @patch('core.provider_health_monitor.send_telegram_alert')
    def test_alert_cooldown_prevents_spam(self, mock_send):
        """Alert cooldown prevents repeated alerts for same provider."""
        from core.provider_health_monitor import ProviderHealthMonitor

        monitor = ProviderHealthMonitor()

        status = {
            'health': 'error',
            'detail': 'Test error',
            'checked_at': datetime.now().isoformat()
        }

        # First alert should go through
        monitor._notify_failure("test_provider", status)
        assert mock_send.call_count == 1

        # Immediate second alert should be blocked
        monitor._notify_failure("test_provider", status)
        assert mock_send.call_count == 1  # Still only 1

    @patch('core.provider_health_monitor.send_telegram_alert')
    def test_different_providers_can_alert_simultaneously(self, mock_send):
        """Cooldown is per-provider, not global."""
        from core.provider_health_monitor import ProviderHealthMonitor

        monitor = ProviderHealthMonitor()

        status = {
            'health': 'error',
            'detail': 'Test error',
            'checked_at': datetime.now().isoformat()
        }

        # Alert for provider1
        monitor._notify_failure("provider1", status)
        assert mock_send.call_count == 1

        # Alert for provider2 should also go through
        monitor._notify_failure("provider2", status)
        assert mock_send.call_count == 2


class TestAPIIntegration:
    """Test API endpoint integration."""

    def test_health_monitor_state_file_created(self):
        """Monitor writes state file for API visibility."""
        from core.provider_health_monitor import ProviderHealthMonitor
        from core.memory import load

        monitor = ProviderHealthMonitor()
        monitor.start()
        time.sleep(0.2)  # Let it run briefly

        state = load("provider_health_monitor_state.json")
        assert state is not None
        assert state['running'] == True
        assert 'check_count' in state
        assert 'last_check' in state

        monitor.stop()

    def test_check_all_providers_integration(self):
        """check_all_providers handles real provider list."""
        from core.provider_health_monitor import ProviderHealthMonitor
        import core.ai_provider as ai_provider

        # Ensure at least one provider is registered
        providers = ai_provider.list_providers()
        assert len(providers) > 0

        monitor = ProviderHealthMonitor()
        monitor.check_all_providers()

        # Should complete without error
        assert monitor.check_count == 1


class TestEndToEnd:
    """End-to-end integration tests."""

    @patch('core.provider_health_monitor.send_telegram_alert')
    def test_monitor_detects_and_alerts_on_failure(self, mock_send):
        """Full flow: provider fails → monitor detects → alert sent."""
        import core.ai.circuit_breaker as cb
        from core.provider_health_monitor import ProviderHealthMonitor

        # Trip circuit breaker to simulate failure
        cb.record_failure("test_provider")
        cb.record_failure("test_provider")
        cb.record_failure("test_provider")

        monitor = ProviderHealthMonitor(check_interval=0.1)
        monitor.start()

        # Wait for at least one check cycle
        time.sleep(0.3)

        # Should have sent an alert
        assert mock_send.called

        monitor.stop()

    def test_summary_logging(self):
        """Monitor logs summary every N checks."""
        from core.provider_health_monitor import ProviderHealthMonitor
        import logging

        # Capture log output
        with patch.object(ProviderHealthMonitor, '_log_summary') as mock_log:
            monitor = ProviderHealthMonitor(check_interval=0.05)  # Very fast
            monitor.start()

            # Wait for ~20+ checks (SUMMARY_EVERY_N = 20)
            time.sleep(1.2)

            monitor.stop()

            # Should have logged at least once
            assert mock_log.called
