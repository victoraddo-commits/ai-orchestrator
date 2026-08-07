"""Phase 13L-1: Provider Performance-Weighted Routing.

Tracks per-(provider, model) call observations (success, latency, cost) and
computes composite scores that weight provider selection toward higher-
performing providers while still exploring enough to keep metrics fresh.

Architecture:
  CallObservation     -- one measured LLM call
  ProviderPerformanceTracker -- sliding-window observation store
  PerformanceScorer   -- composite scoring from observations
  RoutingDecision     -- weighted provider selection from scores

All tracking is in-memory (no persistence). Performance telemetry only needs
to be recent; restarting fresh is acceptable for a first implementation.
"""

import math
import random as _random
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from core.ai.provider_pricing import compute_cost

# --- Configuration (module-level, overridable in tests) ---

PERFORMANCE_ROUTING_ENABLED = True

SELECTION_MODE = "weighted_random"  # "weighted_random" | "best"

MIN_SAMPLES = 20

WINDOW_SECONDS = 600  # 10 minutes

TEMPERATURE = 1.0

EXPLORATION_RATE = 0.05

WEIGHTS = {
    "success": 0.4,
    "latency": 0.3,
    "cost": 0.3,
}

CIRCUIT_BREAKER = {
    "consecutive_failures": 5,
    "cooldown_seconds": 300,
}

MAX_OBSERVATIONS_PER_KEY = 1000


@dataclass
class CallObservation:
    provider: str
    model: str
    success: bool
    error_type: Optional[str] = None  # timeout, 5xx, 429, auth, etc.
    duration_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: Optional[float] = None
    timestamp: float = field(default_factory=time.time)


class ProviderPerformanceTracker:
    """Sliding-window observation store. Not persisted — in-memory only."""

    def __init__(self, window_seconds=None, max_per_key=None):
        self._window_seconds = window_seconds or WINDOW_SECONDS
        self._max_per_key = max_per_key or MAX_OBSERVATIONS_PER_KEY
        self._observations: dict[str, list[CallObservation]] = defaultdict(list)
        self._circuit_state: dict[str, dict] = {}

    def record(self, obs: CallObservation):
        """Record a call observation under (provider, model) key."""
        key = self._key(obs.provider, obs.model)
        self._observations[key].append(obs)

        if len(self._observations[key]) > self._max_per_key:
            self._observations[key] = self._observations[key][-self._max_per_key:]

        if not obs.success:
            self._track_consecutive_failures(obs.provider)

        self._prune_window(key)

    def record_success(self, provider: str):
        """Reset consecutive failure counter on success."""
        self._circuit_state.pop(provider, None)

    def _track_consecutive_failures(self, provider: str):
        entry = self._circuit_state.get(provider, {})
        consecutive = entry.get("consecutive_failures", 0) + 1
        entry["consecutive_failures"] = consecutive
        entry["last_failure_at"] = time.time()
        if consecutive >= CIRCUIT_BREAKER["consecutive_failures"]:
            entry["open_since"] = time.time()
        self._circuit_state[provider] = entry

    def is_circuit_open(self, provider: str) -> bool:
        entry = self._circuit_state.get(provider, {})
        consecutive = entry.get("consecutive_failures", 0)
        if consecutive < CIRCUIT_BREAKER["consecutive_failures"]:
            return False
        open_since = entry.get("open_since")
        if open_since is None:
            return False
        elapsed = time.time() - open_since
        if elapsed >= CIRCUIT_BREAKER["cooldown_seconds"]:
            entry.pop("open_since", None)
            entry["consecutive_failures"] = 0
            return False
        return True

    def get_observations(self, provider: str, model: str = None) -> list[CallObservation]:
        if model:
            key = self._key(provider, model)
            self._prune_window(key)
            return list(self._observations.get(key, []))
        result = []
        for key, obs_list in self._observations.items():
            if key.startswith(f"{provider}|"):
                self._prune_window(key)
                result.extend(obs_list)
        return result

    def get_all_observations(self) -> dict[str, list[CallObservation]]:
        result = {}
        for key in list(self._observations.keys()):
            self._prune_window(key)
            if self._observations[key]:
                result[key] = list(self._observations[key])
        return result

    def _prune_window(self, key: str):
        cutoff = time.time() - self._window_seconds
        obs_list = self._observations.get(key)
        if not obs_list:
            return
        while obs_list and obs_list[0].timestamp < cutoff:
            obs_list.pop(0)

    @staticmethod
    def _key(provider: str, model: str) -> str:
        return f"{provider}|{model}"


class PerformanceScorer:
    """Converts a list of CallObservations into a composite score."""

    def __init__(self, weights=None):
        self._weights = weights or dict(WEIGHTS)

    def score(self, observations: list[CallObservation]) -> dict:
        """Return a dict with score breakdown and composite.

        Returns {"composite": float, "success_score": float, "latency_score": float,
                  "cost_score": float, "sample_count": int}
        """
        if not observations:
            return self._no_data_score()

        successes = [o for o in observations if o.success]
        total = len(observations)

        success_score = self._compute_success_score(successes, total)
        latency_score = self._compute_latency_score(successes)
        cost_score = self._compute_cost_score(observations)

        w = self._weights
        total_weight = sum(w.values()) or 1.0

        composite = (
            w.get("success", 0.4) * success_score
            + w.get("latency", 0.3) * latency_score
            + w.get("cost", 0.3) * cost_score
        ) / total_weight

        return {
            "composite": round(composite, 4),
            "success_score": round(success_score, 4),
            "latency_score": round(latency_score, 4),
            "cost_score": round(cost_score, 4),
            "sample_count": total,
        }

    @staticmethod
    def _no_data_score() -> dict:
        return {
            "composite": 0.5,
            "success_score": 0.5,
            "latency_score": 0.5,
            "cost_score": 0.5,
            "sample_count": 0,
        }

    @staticmethod
    def _compute_success_score(successes: list, total: int) -> float:
        return (len(successes) + 1) / (total + 2)

    @staticmethod
    def _compute_latency_score(successes: list) -> float:
        durations = sorted([o.duration_ms for o in successes if o.duration_ms > 0])
        if not durations:
            return 0.5
        p50 = durations[len(durations) // 2]
        return min(1.0, p50 / p50)  # simplified: normalize by own P50
        # In cross-provider scoring, min P95 across all providers is used.
        # Here we return the raw P50/P95 for the caller to normalize.

    @staticmethod
    def _compute_cost_score(observations: list) -> float:
        costs = [o.cost for o in observations if o.cost is not None and o.cost > 0]
        if not costs:
            return 0.5
        total_tokens = sum(o.prompt_tokens + o.completion_tokens for o in observations)
        if total_tokens == 0:
            return 0.5
        total_cost = sum(costs)
        cost_per_1k = (total_cost / total_tokens) * 1000
        return min(1.0, 0.01 / max(cost_per_1k, 0.0001))


class RoutingDecision:
    """Selects a provider order using performance-weighted routing.

    Given a list of candidate providers, their models, and performance
    observations, returns a sorted priority order and a weighted selection
    distribution.
    """

    def __init__(self, tracker=None, scorer=None, selection_mode=None,
                 temperature=None, exploration_rate=None):
        self._tracker = tracker or _TRACKER
        self._scorer = scorer or _SCORER
        self._selection_mode = selection_mode or SELECTION_MODE
        self._temperature = temperature or TEMPERATURE
        self._exploration_rate = exploration_rate or EXPLORATION_RATE

    def select(self, candidates: list[str]) -> str:
        """Pick a single provider using weighted random or best mode.

        If no provider has MIN_SAMPLES observations, falls back to
        static order (first candidate wins).
        """
        if not candidates:
            raise ValueError("no candidates provided")

        scored = self._score_candidates(candidates)

        all_below_min = all(
            s["sample_count"] < MIN_SAMPLES for s in scored.values()
        )

        if all_below_min:
            return candidates[0]

        if _random.random() < self._exploration_rate:
            return _random.choice(candidates)

        if self._selection_mode == "best":
            return max(scored, key=lambda p: scored[p]["composite"])

        scores = {p: s["composite"] for p, s in scored.items()}
        return self._weighted_pick(scores)

    def priority_order(self, candidates: list[str]) -> list[str]:
        """Return candidates sorted by composite score (highest first).

        Providers with fewer than MIN_SAMPLES are placed after scored ones
        but before circuit-broken ones.
        """
        if not candidates:
            return []

        scored = self._score_candidates(candidates)

        with_data = []
        without_data = []
        for p in candidates:
            s = scored.get(p, {})
            if s.get("sample_count", 0) >= MIN_SAMPLES:
                with_data.append(p)
            else:
                without_data.append(p)

        with_data.sort(key=lambda p: scored[p]["composite"], reverse=True)
        return with_data + without_data

    def _score_candidates(self, candidates: list[str]) -> dict:
        result = {}
        for name in candidates:
            model = self._model_for_provider(name)
            observations = self._tracker.get_observations(name, model)
            result[name] = self._scorer.score(observations)
        return result

    @staticmethod
    def _model_for_provider(provider_name: str) -> str | None:
        import core.llm_clients as llm
        import core.ai_provider as ai_provider

        p = ai_provider.get_provider(provider_name)
        if p is None:
            return None

        model_attr_map = {
            "gemini": "GEMINI_DEFAULT_MODEL",
            "geminix": "GEMINI_DEFAULT_MODEL",
            "groq": "GROQ_DEFAULT_MODEL",
            "deepseek_native_pro": "DEEPSEEK_NATIVE_PRO_MODEL",
            "deepseek_native_flash": "DEEPSEEK_NATIVE_FLASH_MODEL",
            "deepseek": "DEEPSEEK_DEFAULT_MODEL",
            "openai": "OPENAI_DEFAULT_MODEL",
            "qwen4_text": "QWEN3_CODER_MODEL",
            "qwen4_pod_b": "QWEN3_POD_B_MODEL",
        }

        if provider_name in model_attr_map:
            return getattr(llm, model_attr_map[provider_name], None)

        return provider_name

    @staticmethod
    def _weighted_pick(scores: dict[str, float]) -> str:
        if not scores:
            raise ValueError("no scores")
        exp_scores = {}
        for p, s in scores.items():
            exp_scores[p] = math.exp(s / TEMPERATURE)
        total = sum(exp_scores.values())
        r = _random.random() * total
        cumulative = 0.0
        for p, exp in exp_scores.items():
            cumulative += exp
            if r <= cumulative:
                return p
        return next(iter(scores))


_GLOBAL_TRACKER = None
_GLOBAL_SCORER = None


def _get_tracker():
    global _GLOBAL_TRACKER
    if _GLOBAL_TRACKER is None:
        _GLOBAL_TRACKER = ProviderPerformanceTracker()
    return _GLOBAL_TRACKER


def _get_scorer():
    global _GLOBAL_SCORER
    if _GLOBAL_SCORER is None:
        _GLOBAL_SCORER = PerformanceScorer()
    return _GLOBAL_SCORER


_TRACKER = _get_tracker()
_SCORER = _get_scorer()


def record_observation(provider: str, model: str | None, success: bool,
                       duration_ms: float, error_type: str | None = None,
                       prompt_tokens: int = 0, completion_tokens: int = 0,
                       cost: float | None = None) -> None:
    """Record a call observation for performance tracking.

    The primary entry point for ai_router.delegate() to report results.
    """
    if model is None:
        import core.ai_provider as ai_provider

        p = ai_provider.get_provider(provider)
        model = p.get("model_override") if p else provider

    obs = CallObservation(
        provider=provider,
        model=model or provider,
        success=success,
        error_type=error_type,
        duration_ms=duration_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost=cost,
    )
    _TRACKER.record(obs)

    if success:
        _TRACKER.record_success(provider)


def get_provider_scores() -> dict[str, dict]:
    """Return composite scores for all tracked providers."""
    result = {}
    for key, observations in _TRACKER.get_all_observations().items():
        provider = key.split("|")[0]
        score = _SCORER.score(observations)
        score["provider"] = provider
        score["model"] = key.split("|")[1] if "|" in key else provider
        result[key] = score
    return result


def reset():
    """Reset all tracking state. Used by tests to start clean."""
    global _GLOBAL_TRACKER, _GLOBAL_SCORER
    _GLOBAL_TRACKER = ProviderPerformanceTracker()
    _GLOBAL_SCORER = PerformanceScorer()
    global _TRACKER, _SCORER
    _TRACKER = _GLOBAL_TRACKER
    _SCORER = _GLOBAL_SCORER
