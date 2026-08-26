"""Tests for cost_tracker estimation from recorded provider token usage.

2026-08-26: before this change every usage-history entry carried cost=null and
no usage block, so get_cost_summary() returned $0 total with all calls counted
as unknown -- despite real provider spend (OpenRouter exhaustion proved it).
These tests pin the fix: entries carrying a provider-reported usage dict get
estimated via provider_pricing, and providers that were previously missing
from PRICING are now known.
"""

import pytest

import core.ai.cost_tracker as cost_tracker
import core.ai.provider_pricing as provider_pricing


def _record(provider, description="test call", **kwargs):
    from core.ai.ai_router import record_usage
    defaults = dict(task_type="classification", success=True, duration_ms=100)
    defaults.update(kwargs)
    return record_usage(provider, description=description, **defaults)


def test_summary_estimates_from_recorded_usage_block(isolated_memory):
    _record(
        "openrouter",
        usage={"prompt_tokens": 1_000_000, "completion_tokens": 500_000},
    )
    summary = cost_tracker.get_cost_summary(days=30)

    assert summary["calls_estimated"] == 1
    assert summary["calls_unknown"] == 0
    assert abs(summary["by_provider"]["openrouter"] - 0.45) < 1e-6  # $0.15 + $0.30


def test_local_providers_estimate_to_zero_but_are_known(isolated_memory):
    _record("local", usage={"prompt_tokens": 1234, "completion_tokens": 567})
    summary = cost_tracker.get_cost_summary(days=30)

    # Free self-hosted model: estimated (not unknown) at $0.00.
    assert summary["calls_estimated"] == 1
    assert summary["calls_unknown"] == 0
    assert summary["total_cost"] == 0.0


def test_every_registered_router_provider_has_pricing():
    """Any provider name in ROLE_PROVIDERS must be estimable by the tracker."""
    from core.ai.ai_router import ROLE_PROVIDERS

    unknown = sorted({
        p for providers in ROLE_PROVIDERS.values() for p in providers
        if provider_pricing.get_pricing(p) is None
    })
    assert unknown == [], f"providers missing from PRICING: {unknown}"
