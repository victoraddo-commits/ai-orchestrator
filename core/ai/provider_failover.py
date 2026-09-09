"""Provider Failover Manager — automatic provider selection and fallback.

Part of: KAI Phase 1 — Autonomous Provider Failover Implementation.

The ProviderFailover class provides a clean abstraction for selecting the best
available provider and handling automatic failover when providers fail. It integrates
with existing circuit breaker and health tracking systems.

Uses existing infrastructure:
- core.ai.circuit_breaker for failure tracking
- core.ai.provider_health for health snapshots
- core.ai.ai_router for provider execution
"""

import logging
from typing import Optional, List, Dict, Any

import core.ai.circuit_breaker as circuit_breaker
import core.ai.provider_health as provider_health
import core.ai_provider as ai_provider
from core.logger import info

logger = logging.getLogger(__name__)


class ProviderFailover:
    """Automatic failover when primary provider fails.

    Manages provider selection based on health, circuit breaker state, and
    configurable fallback chains. Records success/failure for circuit breaker
    integration.

    Usage:
        failover = ProviderFailover()
        provider = failover.get_available_provider(task_type='coding')
        if provider:
            # Execute with provider
            failover.record_success(provider)
        else:
            # No healthy provider available
            raise Exception("All providers unavailable")
    """

    def __init__(self, fallback_chains: Optional[Dict[str, List[str]]] = None):
        """Initialize provider failover manager.

        Args:
            fallback_chains: Optional custom fallback chains per task type.
                           If None, uses default chains.
        """
        self.fallback_chains = fallback_chains or self._default_fallback_chains()

    def _default_fallback_chains(self) -> Dict[str, List[str]]:
        """Get default fallback chains for each task type.

        Returns:
            Dict mapping task_type to ordered list of provider names.
            Providers are tried in order until one succeeds.
        """
        return {
            # Coding tasks: prefer local GPU, then cloud providers
            'coding': [
                'local_brain_fast',  # Local GPU (fastest, no cost)
                'local_coder',        # Local coding-specific model
                'local',              # Generic local model
                'free_coding',        # Free cloud models
                'claude',             # Paid cloud (reliable fallback)
                'omniroute',          # Multi-provider gateway
            ],
            # Text tasks: prefer local for speed, cloud for quality
            'planning': [
                'local_brain_fast',
                'local',
                'gemini',
                'claude',
            ],
            'review': [
                'local_brain_fast',
                'local',
                'gemini',
                'claude',
            ],
            # Default chain for unknown task types
            'default': [
                'local_brain_fast',
                'claude',
                'omniroute',
            ],
        }

    def get_available_provider(
        self,
        task_type: str = 'coding',
        exclude: Optional[List[str]] = None
    ) -> Optional[str]:
        """Get first available provider from fallback chain.

        Checks providers in order:
        1. Skip if circuit breaker is open
        2. Skip if provider is unavailable (no credentials)
        3. Skip if provider is disabled
        4. Skip if quota exceeded
        5. Return first healthy provider

        Args:
            task_type: Type of task ('coding', 'planning', 'review', etc.)
            exclude: Optional list of provider names to exclude

        Returns:
            Provider name if available, None if all providers exhausted.
        """
        exclude = exclude or []
        chain = self.fallback_chains.get(task_type) or self.fallback_chains['default']

        for provider_name in chain:
            # Skip excluded providers
            if provider_name in exclude:
                logger.debug(f"Skipping excluded provider: {provider_name}")
                continue

            # Check if provider is registered
            provider_info = ai_provider.get_provider(provider_name)
            if not provider_info:
                logger.debug(f"Provider {provider_name} not registered")
                continue

            # Check if provider is available (has credentials)
            if not provider_info.get('available', False):
                logger.debug(f"Provider {provider_name} not available (no credentials)")
                continue

            # Check if provider is enabled
            if not provider_info.get('enabled', True):
                logger.debug(f"Provider {provider_name} is disabled")
                continue

            # Check circuit breaker
            if circuit_breaker.is_open(provider_name):
                breaker = circuit_breaker.get_breaker_snapshot(provider_name) or {}
                logger.debug(
                    f"Provider {provider_name} circuit breaker open "
                    f"({breaker.get('consecutive_failures', '?')} failures)"
                )
                continue

            # Check health/quota
            health = provider_health.get_quota_snapshot(provider_name)
            if health:
                status = health.get('status')

                if status == 'quota_exceeded':
                    logger.debug(
                        f"Provider {provider_name} quota exceeded: "
                        f"{health.get('detail', 'unknown')}"
                    )
                    continue

                if status == 'error':
                    logger.debug(
                        f"Provider {provider_name} has errors: "
                        f"{health.get('detail', 'unknown')}"
                    )
                    # Error status is degraded but not a hard block
                    # Continue to try this provider

            # Provider passes all checks
            logger.info(f"Selected provider for {task_type}: {provider_name}")
            return provider_name

        # No healthy provider found
        logger.warning(f"No available provider for task_type={task_type}")
        return None

    def get_failover_sequence(
        self,
        task_type: str = 'coding',
        exclude: Optional[List[str]] = None
    ) -> List[str]:
        """Get ordered list of providers to try for a task.

        Unlike get_available_provider which returns only the first healthy one,
        this returns the full ordered sequence for explicit failover handling.

        Args:
            task_type: Type of task
            exclude: Optional list of provider names to exclude

        Returns:
            Ordered list of provider names to try.
        """
        exclude = exclude or []
        chain = self.fallback_chains.get(task_type) or self.fallback_chains['default']
        return [p for p in chain if p not in exclude]

    def record_success(self, provider: str):
        """Record successful provider use.

        Clears circuit breaker and quota errors for the provider.

        Args:
            provider: Provider name that succeeded
        """
        circuit_breaker.record_success(provider)
        provider_health.clear_quota_exceeded(provider)
        logger.debug(f"Recorded success for provider: {provider}")

    def record_failure(self, provider: str, error: str):
        """Record provider failure.

        Increments circuit breaker failure count. May trip circuit breaker
        if threshold is reached.

        Args:
            provider: Provider name that failed
            error: Error message or description
        """
        circuit_breaker.record_failure(provider)
        logger.debug(f"Recorded failure for provider {provider}: {error}")

    def check_provider_health(self, provider: str) -> Dict[str, Any]:
        """Check detailed health status of a provider.

        Args:
            provider: Provider name to check

        Returns:
            Dict with health details:
            - available: bool (has credentials)
            - enabled: bool (not disabled by operator)
            - circuit_open: bool (circuit breaker tripped)
            - quota_exceeded: bool (quota limit hit)
            - health_status: str (ok/error/quota_exceeded)
            - can_use: bool (overall usability)
        """
        provider_info = ai_provider.get_provider(provider)

        if not provider_info:
            return {
                'available': False,
                'enabled': False,
                'circuit_open': False,
                'quota_exceeded': False,
                'health_status': 'not_registered',
                'can_use': False,
            }

        available = provider_info.get('available', False)
        enabled = provider_info.get('enabled', True)
        circuit_open = circuit_breaker.is_open(provider)

        health = provider_health.get_quota_snapshot(provider)
        health_status = 'ok'
        quota_exceeded = False

        if health:
            health_status = health.get('status', 'ok')
            quota_exceeded = health_status == 'quota_exceeded'

        can_use = (
            available and
            enabled and
            not circuit_open and
            not quota_exceeded
        )

        return {
            'available': available,
            'enabled': enabled,
            'circuit_open': circuit_open,
            'quota_exceeded': quota_exceeded,
            'health_status': health_status,
            'can_use': can_use,
            'details': health,
        }


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

# Default instance for simple usage
_default_failover: Optional[ProviderFailover] = None


def get_failover() -> ProviderFailover:
    """Get or create the default ProviderFailover instance."""
    global _default_failover
    if _default_failover is None:
        _default_failover = ProviderFailover()
    return _default_failover


def get_available_provider(task_type: str = 'coding') -> Optional[str]:
    """Get first available provider for task_type.

    Convenience function using the default failover instance.

    Args:
        task_type: Type of task

    Returns:
        Provider name or None
    """
    return get_failover().get_available_provider(task_type)


def record_success(provider: str):
    """Record successful provider use.

    Convenience function using the default failover instance.
    """
    get_failover().record_success(provider)


def record_failure(provider: str, error: str):
    """Record provider failure.

    Convenience function using the default failover instance.
    """
    get_failover().record_failure(provider, error)
