"""Tests for the AI-3 Circuit Breaker module.

Verifies: failure tracking, threshold tripping, cooldown, half-open probe,
success reset, configurable thresholds/cooldowns, bulk operations."""

import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(autouse=True)
def reset_state():
    """Reset breaker state before each test."""
    import core.ai.circuit_breaker as cb
    cb.reset_all_breakers()
    yield
    cb.reset_all_breakers()


class TestFailureTracking:
    """Consecutive failure tracking and threshold tripping."""

    def test_initial_state_is_closed(self):
        """A provider with no recorded failures is not open."""
        import core.ai.circuit_breaker as cb
        assert cb.is_open("test_provider") is False
        assert cb.get_breaker_snapshot("test_provider") is None

    def test_single_failure_does_not_trip(self):
        """One failure keeps the breaker closed."""
        import core.ai.circuit_breaker as cb
        entry = cb.record_failure("test_provider")
        assert entry["state"] == "closed"
        assert entry["consecutive_failures"] == 1
        assert cb.is_open("test_provider") is False

    def test_two_failures_do_not_trip(self):
        """Two consecutive failures still don't trip (threshold=3)."""
        import core.ai.circuit_breaker as cb
        cb.record_failure("test_provider")
        entry = cb.record_failure("test_provider")
        assert entry["state"] == "closed"
        assert entry["consecutive_failures"] == 2
        assert cb.is_open("test_provider") is False

    def test_third_failure_trips_breaker(self):
        """Three consecutive failures trip the breaker to OPEN."""
        import core.ai.circuit_breaker as cb
        cb.record_failure("test_provider")
        cb.record_failure("test_provider")
        entry = cb.record_failure("test_provider")
        assert entry["state"] == "open"
        assert entry["consecutive_failures"] == 3
        assert cb.is_open("test_provider") is True

    def test_success_resets_breaker(self):
        """A success clears the breaker entirely."""
        import core.ai.circuit_breaker as cb
        cb.record_failure("test_provider")
        cb.record_failure("test_provider")
        cb.record_failure("test_provider")
        assert cb.is_open("test_provider") is True

        cb.record_success("test_provider")
        assert cb.is_open("test_provider") is False
        assert cb.get_breaker_snapshot("test_provider") is None

    def test_success_after_two_failures_also_resets(self):
        """Success resets even before threshold is reached."""
        import core.ai.circuit_breaker as cb
        cb.record_failure("test_provider")
        cb.record_failure("test_provider")
        assert cb.get_breaker_snapshot("test_provider")["consecutive_failures"] == 2

        cb.record_success("test_provider")
        assert cb.get_breaker_snapshot("test_provider") is None

    def test_failure_after_reset_starts_fresh(self):
        """After a success reset, failures start counting from 1 again."""
        import core.ai.circuit_breaker as cb
        cb.record_failure("test_provider")
        cb.record_failure("test_provider")
        cb.record_failure("test_provider")
        cb.record_success("test_provider")

        cb.record_failure("test_provider")
        snap = cb.get_breaker_snapshot("test_provider")
        assert snap["consecutive_failures"] == 1
        assert snap["state"] == "closed"


class TestCooldownAndHalfOpen:
    """Cooldown transitions and half-open probe behavior."""

    SHORT_COOLDOWN = 1  # 1 second — fast for tests but non-zero

    def test_open_breaker_stays_open_within_cooldown(self):
        """Within the cooldown window, is_open() keeps returning True."""
        import core.ai.circuit_breaker as cb
        cb.record_failure("test_provider")
        cb.record_failure("test_provider")
        cb.record_failure("test_provider")

        # Immediately after trip — still open
        assert cb.is_open("test_provider") is True

    def test_open_breaker_transitions_to_half_open_after_cooldown(self):
        """After cooldown elapses, is_open transitions to half_open and returns False."""
        import core.ai.circuit_breaker as cb
        import time
        cb.set_cooldown("test_provider", self.SHORT_COOLDOWN)

        cb.record_failure("test_provider")
        cb.record_failure("test_provider")
        cb.record_failure("test_provider")

        # Immediately — still open
        assert cb.is_open("test_provider") is True

        time.sleep(self.SHORT_COOLDOWN + 0.1)

        # After cooldown — transitions to half_open
        assert cb.is_open("test_provider") is False
        assert cb.is_half_open("test_provider") is True

    def test_half_open_probe_failure_returns_to_open(self):
        """A failure in half-open state returns the breaker to OPEN."""
        import core.ai.circuit_breaker as cb
        import time
        cb.set_cooldown("test_provider", self.SHORT_COOLDOWN)

        cb.record_failure("test_provider")
        cb.record_failure("test_provider")
        cb.record_failure("test_provider")

        # Wait for cooldown and trigger half-open
        time.sleep(self.SHORT_COOLDOWN + 0.1)
        assert cb.is_open("test_provider") is False
        assert cb.is_half_open("test_provider") is True

        # Probe fails — back to OPEN (with new tripped_at)
        entry = cb.record_failure("test_provider")
        assert entry["state"] == "open"
        assert cb.is_open("test_provider") is True

    def test_half_open_probe_success_closes_breaker(self):
        """A success in half-open state fully resets the breaker."""
        import core.ai.circuit_breaker as cb
        import time
        cb.set_cooldown("test_provider", self.SHORT_COOLDOWN)

        cb.record_failure("test_provider")
        cb.record_failure("test_provider")
        cb.record_failure("test_provider")

        time.sleep(self.SHORT_COOLDOWN + 0.1)
        assert cb.is_open("test_provider") is False
        assert cb.is_half_open("test_provider") is True

        cb.record_success("test_provider")
        assert cb.is_open("test_provider") is False
        assert cb.get_breaker_snapshot("test_provider") is None

    def test_default_cooldown_is_300_seconds(self):
        """AI-3: Default cooldown is 300s (5 min) per roadmap spec."""
        import core.ai.circuit_breaker as cb
        cb.record_failure("test_provider")
        cb.record_failure("test_provider")
        cb.record_failure("test_provider")

        snap = cb.get_breaker_snapshot("test_provider")
        # tripped within last second, cooldown is 300s → should still be open
        assert snap["state"] == "open"


class TestConfigurableThreshold:
    """Per-provider threshold and cooldown configuration."""

    def test_custom_threshold_trips_earlier(self):
        """A provider with threshold=1 trips after a single failure."""
        import core.ai.circuit_breaker as cb
        cb.set_threshold("test_provider", 1)

        entry = cb.record_failure("test_provider")
        assert entry["state"] == "open"
        assert entry["consecutive_failures"] == 1

    def test_custom_threshold_trips_later(self):
        """A provider with threshold=10 takes many failures to trip."""
        import core.ai.circuit_breaker as cb
        cb.set_threshold("test_provider", 10)

        for _ in range(9):
            entry = cb.record_failure("test_provider")
            assert entry["state"] == "closed"

        entry = cb.record_failure("test_provider")
        assert entry["state"] == "open"

    def test_threshold_below_one_raises(self):
        """Threshold must be >= 1."""
        import core.ai.circuit_breaker as cb
        with pytest.raises(ValueError):
            cb.set_threshold("test_provider", 0)

    def test_cooldown_below_zero_raises(self):
        """Cooldown must be >= 0."""
        import core.ai.circuit_breaker as cb
        with pytest.raises(ValueError):
            cb.set_cooldown("test_provider", -1)

    def test_custom_cooldown_is_stored(self):
        """Setting a custom cooldown persists in breaker state."""
        import core.ai.circuit_breaker as cb
        cb.set_cooldown("test_provider", 120)

        cb.record_failure("test_provider")
        snap = cb.get_breaker_snapshot("test_provider")
        assert snap["cooldown_seconds"] == 120

    def test_get_effective_config_returns_defaults(self):
        """Unconfigured provider gets default threshold and cooldown."""
        import core.ai.circuit_breaker as cb
        config = cb.get_effective_config("unknown_provider")
        assert config["threshold"] == 3
        assert config["cooldown_seconds"] == 300


class TestBulkOperations:
    """List all, reset all, manual trip."""

    def test_list_all_breakers(self):
        """list_all_breakers returns all providers with breaker state."""
        import core.ai.circuit_breaker as cb
        cb.record_failure("provider_a")
        cb.record_failure("provider_a")
        cb.record_failure("provider_a")
        cb.record_failure("provider_b")

        all_breakers = cb.list_all_breakers()
        assert len(all_breakers) == 2

        a = next(b for b in all_breakers if b["provider"] == "provider_a")
        b = next(b for b in all_breakers if b["provider"] == "provider_b")

        assert a["state"] == "open"
        assert a["consecutive_failures"] == 3
        assert b["state"] == "closed"
        assert b["consecutive_failures"] == 1

    def test_reset_all_breakers(self):
        """reset_all_breakers clears everything."""
        import core.ai.circuit_breaker as cb
        cb.record_failure("p1")
        cb.record_failure("p1")
        cb.record_failure("p1")
        cb.record_failure("p2")

        count = cb.reset_all_breakers()
        assert count == 2
        assert cb.list_all_breakers() == []

    def test_trip_breaker_manually(self):
        """Manual trip forces a breaker OPEN immediately."""
        import core.ai.circuit_breaker as cb
        entry = cb.trip_breaker_manually("test_provider", detail="admin override")
        assert entry["state"] == "open"
        assert entry["detail"] == "admin override"
        assert cb.is_open("test_provider") is True

    def test_clear_breaker(self):
        """clear_breaker removes a single provider's breaker."""
        import core.ai.circuit_breaker as cb
        cb.record_failure("p1")
        cb.record_failure("p1")
        cb.record_failure("p1")
        cb.record_failure("p2")

        cb.clear_breaker("p1")
        assert cb.get_breaker_snapshot("p1") is None
        assert cb.get_breaker_snapshot("p2") is not None

    def test_is_half_open_returns_false_for_closed(self):
        """is_half_open is False when breaker is closed or absent."""
        import core.ai.circuit_breaker as cb
        assert cb.is_half_open("nonexistent") is False
        cb.record_failure("p1")
        assert cb.is_half_open("p1") is False

    def test_unique_providers_tracked_independently(self):
        """Each provider has its own independent breaker."""
        import core.ai.circuit_breaker as cb
        # Provider A trips
        cb.record_failure("p_a")
        cb.record_failure("p_a")
        cb.record_failure("p_a")
        assert cb.is_open("p_a") is True

        # Provider B hasn't failed at all
        assert cb.is_open("p_b") is False

        # Provider A's state doesn't affect Provider C with 1 failure
        cb.record_failure("p_c")
        assert cb.is_open("p_c") is False
