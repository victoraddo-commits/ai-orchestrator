"""Speed tiers — route to the fastest capable worker, with tier-aware timeouts.

T0 accelerated (P40, ~65 tok/s) → T1 CPU (VM112, ~8 tok/s) → T2 cloud.
Tiers are the unit the scheduler reasons about: rank candidates T0-first and
derive each call's timeout from its tier so a slow worker can never inherit a
GPU-scale budget (or vice versa).
"""
from __future__ import annotations

# Accelerated local workers (P40 via VM104).
ACCELERATED = frozenset({"kai_coder", "kai_brain", "kai_deep", "local"})
# CPU-only workers — correct but ~8x slower; overflow/fallback only.
CPU_ONLY = frozenset({
    "llama_coder_cpu", "koboldcpp_cpu", "koboldcpp_cpu_a", "koboldcpp_cpu_b",
})

_TIER_RANK = {"T0": 0, "T1": 1, "T2": 2}
# Measured/expected generation throughput per tier (tokens/second).
TIER_TPS = {"T0": 60.0, "T1": 8.0, "T2": 20.0}
_OVERHEAD_S = 15.0
_SAFETY = 1.5


def tier_of(provider: str) -> str:
    if provider in ACCELERATED:
        return "T0"
    if provider in CPU_ONLY:
        return "T1"
    return "T2"


def tier_rank(tier: str) -> int:
    return _TIER_RANK.get(tier, 9)


def rank_candidates(candidates) -> list:
    """Stable sort: fastest tier first, preserving fabric order within a tier."""
    order = {name: i for i, name in enumerate(candidates)}
    return sorted(candidates, key=lambda p: (tier_rank(tier_of(p)), order[p]))


def estimate_timeout(provider: str, tokens: int, safety: float = _SAFETY) -> int:
    """Seconds to allow for a call producing ~``tokens`` on ``provider``."""
    tps = TIER_TPS.get(tier_of(provider), TIER_TPS["T2"])
    try:
        tokens = max(0, int(tokens))
    except (ValueError, TypeError):
        tokens = 0
    return int(_OVERHEAD_S + (tokens / max(tps, 1e-6)) * safety)
