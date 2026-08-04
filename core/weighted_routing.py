"""Phase 13L: Provider Performance-Weighted Routing.

Weights the provider rotation by success rate, timeout frequency, and
average duration — so proven providers get more attempts.  Complements
the existing rotation without replacing it.
"""

import json
from collections import defaultdict


def get_provider_weights():
    """Compute performance weights for every provider from usage history.
    Returns {provider: weight} where weight is 0.0-1.0 (higher = better)."""

    from core.ai.ai_router import get_usage_history

    history = get_usage_history()
    if not history:
        return {}

    # Group by provider
    groups: dict[str, list] = defaultdict(list)
    for entry in history[-200:]:  # Last 200 calls only
        provider = entry.get("provider", "unknown")
        groups[provider].append(entry)

    weights = {}
    for provider, attempts in groups.items():
        total = len(attempts)
        successes = sum(1 for a in attempts if a.get("success"))
        success_rate = successes / max(total, 1)

        # Timeout penalty
        timeouts = sum(1 for a in attempts if "timeout" in str(a.get("error", "") or "").lower())
        timeout_penalty = min(timeouts / max(total, 1), 0.5)

        # Duration factor (faster = better)
        durations = [a.get("duration_ms", 0) for a in attempts if a.get("duration_ms")]
        avg_ms = sum(durations) / max(len(durations), 1)
        # Normalize: assume 30s is "slow", 1s is "fast"
        duration_factor = max(0, 1 - (avg_ms / 30000)) if avg_ms > 0 else 1.0

        # Combined weight
        weight = round((success_rate * 0.6) + ((1 - timeout_penalty) * 0.25) + (duration_factor * 0.15), 3)
        weights[provider] = max(0.1, min(1.0, weight))  # Floor at 0.1 so every provider gets some chance

    return weights


def sort_candidates_by_weight(candidates):
    """Sort a list of candidate providers by performance weight.
    Higher weight = tried first.  Providers not in history get neutral 0.5."""
    weights = get_provider_weights()
    return sorted(
        candidates,
        key=lambda name: weights.get(name, 0.5),
        reverse=True,
    )


def get_weighted_routing_report():
    """Dashboard-friendly report of current weights."""
    weights = get_provider_weights()
    return {
        "weights": weights,
        "sorted": sorted(weights.items(), key=lambda x: x[1], reverse=True),
        "note": "Higher weight = better performance. Weight floor: 0.1. Computed from last 200 calls.",
    }
