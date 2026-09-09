"""Tests for Provider Failover functionality.

Part of KAI Phase 1 - Autonomous Provider Failover Implementation.

Verifies:
- Provider selection from fallback chains
- Health checking integration
- Circuit breaker integration
- Success/failure recording
- Failover notification logic (in ai_router)
"""

import os
import sys
from pathlib import Path
from unittest.mock import Mock, patch
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(autouse=True)
def reset_state():
    """Reset circuit breaker and health state before each test."""
    import core.ai.circuit_breaker as cb
    import core.ai.provider_health as ph
    from core.memory import save

    cb.reset_all_breakers()
    save("provider_quota.json", {})

    yield

    cb.reset_all_breakers()


class TestProviderFailover:
    """Test ProviderFailover class functionality."""

    def test_failover_can_be_created(self):
        """ProviderFailover instance can be created."""
        from core.ai.provider_failover import ProviderFailover

        failover = ProviderFailover()
        assert failover is not None
        assert failover.fallback_chains is not None

    def test_default_fallback_chains(self):
        """Default fallback chains are configured for common task types."""
        from core.ai.provider_failover import ProviderFailover

        failover = ProviderFailover()

        # Should have chains for key task types
        assert 'coding' in failover.fallback_chains
        assert 'planning' in failover.fallback_chains
        assert 'review' in failover.fallback_chains
        assert 'default' in failover.fallback_chains

        # Coding chain should prefer local models
        coding_chain = failover.fallback_chains['coding']
        assert len(coding_chain) > 0
        assert any('local' in p for p in coding_chain)  # Has local providers

    def test_get_available_provider_when_all_healthy(self):
        """get_available_provider returns first healthy provider."""
        from core.ai.provider_failover import ProviderFailover

        # Create failover with simple test chain
        failover = ProviderFailover(fallback_chains={
            'test': ['claude', 'gemini', 'local']
        })

        # Mock provider check - claude is available
        with patch('core.ai_provider.get_provider') as mock_get:
            mock_get.return_value = {
                'available': True,
                'enabled': True,
            }

            provider = failover.get_available_provider(task_type='test')

            # Should return first provider (claude)
            assert provider == 'claude'

    def test_get_available_provider_skips_unavailable(self):
        """get_available_provider skips providers without credentials."""
        from core.ai.provider_failover import ProviderFailover

        failover = ProviderFailover(fallback_chains={
            'test': ['provider1', 'provider2']
        })

        def mock_get_provider(name):
            if name == 'provider1':
                return {'available': False, 'enabled': True}  # No credentials
            return {'available': True, 'enabled': True}

        with patch('core.ai_provider.get_provider', side_effect=mock_get_provider):
            provider = failover.get_available_provider(task_type='test')

            # Should skip provider1, return provider2
            assert provider == 'provider2'

    def test_get_available_provider_skips_disabled(self):
        """get_available_provider skips disabled providers."""
        from core.ai.provider_failover import ProviderFailover

        failover = ProviderFailover(fallback_chains={
            'test': ['provider1', 'provider2']
        })

        def mock_get_provider(name):
            if name == 'provider1':
                return {'available': True, 'enabled': False}  # Disabled
            return {'available': True, 'enabled': True}

        with patch('core.ai_provider.get_provider', side_effect=mock_get_provider):
            provider = failover.get_available_provider(task_type='test')

            assert provider == 'provider2'

    def test_get_available_provider_skips_circuit_open(self):
        """get_available_provider skips providers with open circuit breakers."""
        import core.ai.circuit_breaker as cb
        from core.ai.provider_failover import ProviderFailover

        # Trip circuit breaker for provider1
        cb.record_failure('provider1')
        cb.record_failure('provider1')
        cb.record_failure('provider1')
        assert cb.is_open('provider1')

        failover = ProviderFailover(fallback_chains={
            'test': ['provider1', 'provider2']
        })

        with patch('core.ai_provider.get_provider') as mock_get:
            mock_get.return_value = {'available': True, 'enabled': True}

            provider = failover.get_available_provider(task_type='test')

            # Should skip provider1 (circuit open), return provider2
            assert provider == 'provider2'

    def test_get_available_provider_skips_quota_exceeded(self):
        """get_available_provider skips providers with exceeded quota."""
        import core.ai.provider_health as ph
        from core.ai.provider_failover import ProviderFailover

        # Set quota exceeded for provider1
        ph.capture_quota_exceeded('provider1', 'Test quota limit')

        failover = ProviderFailover(fallback_chains={
            'test': ['provider1', 'provider2']
        })

        with patch('core.ai_provider.get_provider') as mock_get:
            mock_get.return_value = {'available': True, 'enabled': True}

            provider = failover.get_available_provider(task_type='test')

            assert provider == 'provider2'

    def test_get_available_provider_returns_none_when_all_fail(self):
        """get_available_provider returns None when no healthy providers."""
        from core.ai.provider_failover import ProviderFailover

        failover = ProviderFailover(fallback_chains={
            'test': ['provider1', 'provider2']
        })

        # All providers unavailable
        with patch('core.ai_provider.get_provider') as mock_get:
            mock_get.return_value = {'available': False, 'enabled': True}

            provider = failover.get_available_provider(task_type='test')

            assert provider is None

    def test_record_success_clears_circuit_breaker(self):
        """record_success clears circuit breaker for provider."""
        import core.ai.circuit_breaker as cb
        from core.ai.provider_failover import ProviderFailover

        # Trip circuit breaker
        cb.record_failure('test_provider')
        cb.record_failure('test_provider')
        cb.record_failure('test_provider')
        assert cb.is_open('test_provider')

        failover = ProviderFailover()
        failover.record_success('test_provider')

        # Circuit should be cleared
        assert not cb.is_open('test_provider')

    def test_record_failure_trips_circuit_breaker(self):
        """record_failure increments circuit breaker."""
        import core.ai.circuit_breaker as cb
        from core.ai.provider_failover import ProviderFailover

        failover = ProviderFailover()

        # Record failures
        failover.record_failure('test_provider', 'error 1')
        failover.record_failure('test_provider', 'error 2')
        failover.record_failure('test_provider', 'error 3')

        # Circuit should be open
        assert cb.is_open('test_provider')

    def test_check_provider_health(self):
        """check_provider_health returns detailed health status."""
        from core.ai.provider_failover import ProviderFailover

        failover = ProviderFailover()

        with patch('core.ai_provider.get_provider') as mock_get:
            mock_get.return_value = {
                'available': True,
                'enabled': True,
            }

            health = failover.check_provider_health('test_provider')

            assert health['available'] == True
            assert health['enabled'] == True
            assert health['circuit_open'] == False
            assert health['quota_exceeded'] == False
            assert health['can_use'] == True

    def test_get_failover_sequence(self):
        """get_failover_sequence returns ordered provider list."""
        from core.ai.provider_failover import ProviderFailover

        failover = ProviderFailover(fallback_chains={
            'test': ['p1', 'p2', 'p3']
        })

        sequence = failover.get_failover_sequence(task_type='test')

        assert sequence == ['p1', 'p2', 'p3']

    def test_get_failover_sequence_with_exclusions(self):
        """get_failover_sequence respects exclude list."""
        from core.ai.provider_failover import ProviderFailover

        failover = ProviderFailover(fallback_chains={
            'test': ['p1', 'p2', 'p3']
        })

        sequence = failover.get_failover_sequence(task_type='test', exclude=['p2'])

        assert sequence == ['p1', 'p3']


class TestModuleLevelHelpers:
    """Test module-level convenience functions."""

    def test_get_failover_creates_default_instance(self):
        """get_failover creates and returns default instance."""
        from core.ai.provider_failover import get_failover

        failover1 = get_failover()
        failover2 = get_failover()

        # Should return same instance
        assert failover1 is failover2

    def test_convenience_functions(self):
        """Module-level convenience functions work."""
        from core.ai.provider_failover import record_success, record_failure

        # Should not raise
        record_success('test_provider')
        record_failure('test_provider', 'test error')
