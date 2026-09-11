"""Provider Health Monitor — continuous background monitoring of all AI providers.

Part of: KAI Phase 1 — Autonomous Provider Failover Implementation.

The ProviderHealthMonitor runs as a daemon thread within the scheduler process,
checking all AI provider health every 30 seconds independently of the orchestrator
cycle. Stores health snapshots in memory and sends Telegram alerts on failures.

Uses existing infrastructure:
- core.ai.provider_health for quota/health tracking
- core.ai.circuit_breaker for failure detection
- core.ai.ai_router for provider routing
- core.memory for state persistence
- core.telegram_bridge for notifications
"""

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any

import core.ai_provider as ai_provider
import core.ai.provider_health as provider_health
import core.ai.circuit_breaker as circuit_breaker
from core.memory import save, load
from core.logger import info
from core.telegram_bridge import send_telegram_alert

logger = logging.getLogger(__name__)

# Monitoring interval — check all providers every 30 seconds
CHECK_INTERVAL = 30  # seconds

# Memory file for health monitor state
HEALTH_MONITOR_STATE_FILE = "provider_health_monitor_state.json"

# How often to send summary stats to the logger (every N checks)
SUMMARY_EVERY_N = 20  # every 10 minutes at 30s intervals


class ProviderHealthMonitor:
    """Continuous background monitoring of all AI providers.

    Checks all registered providers every 30 seconds, updates health snapshots,
    detects failures, and sends Telegram alerts when providers become unavailable.

    Usage:
        monitor = ProviderHealthMonitor()
        monitor.start()
        # ... process lifetime ...
        monitor.stop()
    """

    def __init__(self, check_interval: int = CHECK_INTERVAL):
        self.interval = check_interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._check_count = 0
        self._last_alert_sent = {}  # Track last alert time per provider to avoid spam
        self._alert_cooldown = 300  # Don't re-alert same provider within 5 minutes

    # -------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------

    def start(self):
        """Launch the provider health monitor daemon thread."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("ProviderHealthMonitor: already running, not starting again")
            return

        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="kai-provider-health-monitor",
            daemon=True,
        )
        self._thread.start()
        info(f"ProviderHealthMonitor: started (interval={self.interval}s)")

    def stop(self, timeout: float = 5.0):
        """Signal the monitor to stop and wait for graceful shutdown."""
        if self._thread is None or not self._thread.is_alive():
            return

        info("ProviderHealthMonitor: stopping...")
        self._stop.set()
        self._thread.join(timeout=timeout)

        if self._thread.is_alive():
            logger.warning("ProviderHealthMonitor: did not stop within %.1fs", timeout)
        else:
            info(f"ProviderHealthMonitor: stopped (completed {self._check_count} checks)")

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def check_count(self) -> int:
        return self._check_count

    def _write_state_file(self):
        """Write state file so the API process can see monitor liveness."""
        state = {
            "running": self.is_running,
            "check_count": self._check_count,
            "last_check": datetime.now(timezone.utc).isoformat(),
        }
        save(HEALTH_MONITOR_STATE_FILE, state)

    # -------------------------------------------------------------------
    # Main loop
    # -------------------------------------------------------------------

    def _run(self):
        """Main monitoring loop — runs until _stop is set."""
        while not self._stop.is_set():
            started = time.monotonic()

            try:
                self.check_all_providers()
            except Exception as exc:
                logger.error("ProviderHealthMonitor: check failed: %s", exc)

            # Sleep for the remainder of the interval
            elapsed = time.monotonic() - started
            remaining = max(0, self.interval - elapsed)
            self._stop.wait(remaining)

    # -------------------------------------------------------------------
    # Provider checking
    # -------------------------------------------------------------------

    def check_all_providers(self):
        """Check all providers and update health snapshots."""
        providers = ai_provider.list_providers()

        for name, provider_info in providers.items():
            try:
                status = self._check_provider(name, provider_info)
                self._store_health(name, status)

                # Detect failures and notify. 'disabled' is an intentional
                # state (deprecated provider being phased out, or operator-
                # disabled), not a failure — never alert on it.
                if status['health'] not in ('ok', 'disabled'):
                    self._notify_failure(name, status)
            except Exception as exc:
                logger.error(f"ProviderHealthMonitor: failed to check {name}: {exc}")
                self._store_health(name, {
                    'health': 'error',
                    'error': str(exc),
                    'checked_at': datetime.now().isoformat()
                })

        self._check_count += 1
        self._write_state_file()

        if self._check_count % SUMMARY_EVERY_N == 0:
            self._log_summary()

    def _check_provider(self, name: str, provider_info: Dict[str, Any]) -> Dict[str, Any]:
        """Check health of a single provider.

        Returns health status dict with keys:
        - health: 'ok', 'unavailable', 'quota_exceeded', 'circuit_open', 'degraded', 'error'
        - checked_at: ISO timestamp
        - Additional fields depending on provider status
        """
        status = {
            'health': 'ok',
            'checked_at': datetime.now().isoformat()
        }

        # Check if provider is available (credentials configured)
        # Skip credential check for local providers (Ollama on localhost:11434 —
        # they don't need API keys; available_fn() already reports reachability).
        # The registry stores this as `kind` ("local"/"cloud"), NOT `type`.
        # Also skip deprecated providers that are being phased out.
        kind = provider_info.get('kind', '')
        is_local = kind == 'local' or name.startswith('local')
        is_deprecated = name in ['llama3', 'local_brain_fast', 'local_coder']

        if is_deprecated:
            # Deprecated provider - mark as disabled, don't alert
            status['health'] = 'disabled'
            status['reason'] = 'Deprecated provider (being phased out)'
            return status

        if not is_local and not provider_info.get('available', False):
            status['health'] = 'unavailable'
            status['reason'] = 'No credentials configured'
            return status

        # Check if provider is enabled
        if not provider_info.get('enabled', True):
            status['health'] = 'disabled'
            status['reason'] = 'Operator disabled this provider'
            return status

        # Check circuit breaker state
        if circuit_breaker.is_open(name):
            breaker = circuit_breaker.get_breaker_snapshot(name) or {}
            status['health'] = 'circuit_open'
            status['consecutive_failures'] = breaker.get('consecutive_failures', 0)
            status['tripped_at'] = breaker.get('tripped_at')
            return status

        # Check quota/health snapshot
        quota = provider_health.get_quota_snapshot(name)
        if quota:
            if quota.get('status') == 'quota_exceeded':
                status['health'] = 'quota_exceeded'
                status['percent_remaining'] = 0
                status['detail'] = quota.get('detail', 'Quota exceeded')
                return status

            if quota.get('status') == 'error':
                status['health'] = 'degraded'
                status['detail'] = quota.get('detail', 'Provider error')
                return status

            # Provider is OK, add quota info if available
            if quota.get('percent_remaining') is not None:
                status['percent_remaining'] = quota['percent_remaining']

        return status

    def _store_health(self, provider: str, status: Dict[str, Any]):
        """Store health status using provider_health module."""
        # The provider_health module already has its own storage
        # We just update the official snapshot
        provider_health.record_quota_snapshot(
            provider,
            status=status.get('health', 'ok'),
            percent_remaining=status.get('percent_remaining'),
            detail=status.get('reason') or status.get('detail'),
            **{k: v for k, v in status.items() if k not in ['health', 'percent_remaining', 'reason', 'detail']}
        )

    def _notify_failure(self, provider: str, status: Dict[str, Any]):
        """Send Telegram notification on provider failure."""
        # Check cooldown to avoid spam
        now = time.time()
        last_alert = self._last_alert_sent.get(provider, 0)
        if now - last_alert < self._alert_cooldown:
            return  # Skip alert, too soon since last one

        try:
            message = f"⚠️ PROVIDER HEALTH ALERT\n\n"
            message += f"Provider: {provider}\n"
            message += f"Status: {status['health']}\n"
            message += f"Time: {status['checked_at']}\n"

            if status.get('percent_remaining') is not None:
                message += f"Quota: {status['percent_remaining']}%\n"

            if status.get('reason'):
                message += f"Reason: {status['reason']}\n"

            if status.get('detail'):
                message += f"Detail: {status['detail']}\n"

            if status.get('consecutive_failures'):
                message += f"Consecutive Failures: {status['consecutive_failures']}\n"

            send_telegram_alert(message)
            self._last_alert_sent[provider] = now
            info(f"ProviderHealthMonitor: sent failure alert for {provider} ({status['health']})")

        except Exception as exc:
            logger.error(f"ProviderHealthMonitor: failed to send alert for {provider}: {exc}")

    def _log_summary(self):
        """Log summary of current provider health."""
        providers = ai_provider.list_providers()
        health_counts = {}

        for name in providers.keys():
            quota = provider_health.get_quota_snapshot(name)
            health = 'unknown'
            if quota:
                health = quota.get('status', 'ok')
            health_counts[health] = health_counts.get(health, 0) + 1

        info(
            f"ProviderHealthMonitor: {self._check_count} checks completed | "
            f"{len(providers)} providers: " +
            ", ".join(f"{status}={count}" for status, count in sorted(health_counts.items()))
        )


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

# Default instance — created by scheduler on startup
_default_monitor: Optional[ProviderHealthMonitor] = None


def get_monitor() -> Optional[ProviderHealthMonitor]:
    """Return the default ProviderHealthMonitor instance, or None if not started."""
    return _default_monitor


def start_monitor(interval: int = CHECK_INTERVAL) -> ProviderHealthMonitor:
    """Start the default provider health monitor. Returns the instance."""
    global _default_monitor
    if _default_monitor is None:
        _default_monitor = ProviderHealthMonitor(check_interval=interval)
    if not _default_monitor.is_running:
        _default_monitor.start()
    return _default_monitor


def stop_monitor():
    """Stop the default provider health monitor."""
    global _default_monitor
    if _default_monitor is not None:
        _default_monitor.stop()
        _default_monitor = None
