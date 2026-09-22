"""Tests for cost_tracker estimation from recorded provider token usage.

2026-08-26: before this change every usage-history entry carried cost=null and
no usage block, so get_cost_summary() returned $0 total with all calls counted
as unknown -- despite real provider spend (OpenRouter exhaustion proved it).
These tests pin the fix: entries carrying a provider-reported usage dict get
estimated via provider_pricing, and providers that were previously missing
from PRICING are now known.
"""

from datetime import datetime, timezone

import pytest

import core.ai.cost_tracker as cost_tracker
import core.ai.provider_pricing as provider_pricing


def _null_usage_entry(provider="openrouter", task_type="coding"):
    # Mirrors the real records: usage and cost are both nullable.
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "provider": provider,
        "task_type": task_type,
        "cost": None,
        "usage": None,
        "description": "legacy record without token accounting",
    }


def _record(provider, description="test call", **kwargs):
    from core.ai.ai_router import record_usage
    defaults = dict(task_type="classification", success=True, duration_ms=100)
    defaults.update(kwargs)
    return record_usage(provider, description=description, **defaults)


def test_summary_estimates_from_recorded_usage_block(isolated_memory, monkeypatch):
    # The fabric is local-only, so every real provider is $0/token. Inject a
    # synthetic priced provider to pin the estimation mechanism itself.
    monkeypatch.setitem(provider_pricing.PRICING, "test-priced", {
        "test-model": {"input_per_million": 0.15, "output_per_million": 0.60},
    })
    _record(
        "test-priced",
        usage={"prompt_tokens": 1_000_000, "completion_tokens": 500_000},
    )
    summary = cost_tracker.get_cost_summary(days=30)

    assert summary["calls_estimated"] == 1
    assert summary["calls_unknown"] == 0
    assert abs(summary["by_provider"]["test-priced"] - 0.45) < 1e-6  # $0.15 + $0.30


def test_local_providers_estimate_to_zero_but_are_known(isolated_memory):
    _record("local", usage={"prompt_tokens": 1234, "completion_tokens": 567})
    summary = cost_tracker.get_cost_summary(days=30)

    # Free self-hosted model: estimated (not unknown) at $0.00.
    assert summary["calls_estimated"] == 1
    assert summary["calls_unknown"] == 0
    assert summary["total_cost"] == 0.0


def test_summary_survives_null_usage_records(monkeypatch, isolated_memory):
    """P0: usage=null used to make .get() raise AttributeError -> HTTP 500."""
    monkeypatch.setattr(
        cost_tracker, "_load_history",
        lambda: [_null_usage_entry("openrouter"), _null_usage_entry("local")],
    )

    summary = cost_tracker.get_cost_summary(days=30)

    assert summary["calls_with_cost"] == 0
    assert summary["calls_estimated"] + summary["calls_unknown"] == 2
    assert summary["total_cost"] >= 0.0


def test_monthly_trend_and_export_survive_null_usage(monkeypatch, isolated_memory):
    monkeypatch.setattr(
        cost_tracker, "_load_history",
        lambda: [_null_usage_entry("openrouter")],
    )

    monthly = cost_tracker.get_monthly_summary()
    trend = cost_tracker.get_daily_trend(days=30)
    export = cost_tracker.get_cost_export(days=30)

    assert monthly["total_cost"] >= 0.0
    assert len(trend) == 1
    assert len(export) == 1
    assert export[0]["cost_source"] in {"estimated", "unknown"}


def test_provider_detail_survives_null_usage(monkeypatch, isolated_memory):
    monkeypatch.setattr(
        cost_tracker, "_load_history",
        lambda: [_null_usage_entry("openrouter")],
    )

    detail = cost_tracker.get_provider_cost_detail("openrouter", days=30)

    assert detail["provider"] == "openrouter"
    assert detail["total_cost"] >= 0.0
    assert len(detail["recent_calls"]) == 1


def test_every_registered_router_provider_has_pricing():
    """Any provider name in ROLE_PROVIDERS must be estimable by the tracker."""
    from core.ai.ai_router import ROLE_PROVIDERS

    unknown = sorted({
        p for providers in ROLE_PROVIDERS.values() for p in providers
        if provider_pricing.get_pricing(p) is None
    })
    assert unknown == [], f"providers missing from PRICING: {unknown}"
