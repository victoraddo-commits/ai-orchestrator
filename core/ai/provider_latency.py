"""Phase 17R: wall-clock degraded-state detection for AI providers.

Tracks provider response times using exponential moving average (EMA) and
compares current request latency against the historical baseline. Providers
whose latencies significantly exceed their baseline are automatically
deprioritized through a degraded flag.
"""

from datetime import datetime

from core.memory import load, save


LATENCY_STATE_FILE = "provider_latency.json"

EMA_ALPHA = 0.2

DEGRADATION_FACTOR_THRESHOLD = 3.0

MIN_SAMPLES_FOR_BASELINE = 3


def _load_state():
    return load(LATENCY_STATE_FILE) or {}


def _save_state(state):
    save(LATENCY_STATE_FILE, state)


def record_latency(provider, duration_ms):
    state = _load_state()
    entry = state.get(provider, {})

    count = entry.get("count", 0) + 1
    old_ema = entry.get("ema_ms", duration_ms)
    ema = old_ema
    if count > 1:
        ema = EMA_ALPHA * duration_ms + (1 - EMA_ALPHA) * old_ema

    entry["count"] = count
    entry["ema_ms"] = ema
    entry["baseline_ema_ms"] = old_ema
    entry["last_duration_ms"] = duration_ms
    entry["last_updated"] = datetime.now().isoformat()

    state[provider] = entry
    _save_state(state)

    # 17R: sync latency degradation to provider_health so the dashboard
    # surfaces it alongside circuit-breaker and quota state.
    if is_latency_degraded(provider):
        from core.ai.provider_health import capture_provider_error
        capture_provider_error(provider, detail=(
            f"latency degraded: {duration_ms}ms (baseline ~{old_ema:.0f}ms)"
        ))

    return entry


def is_latency_degraded(provider, current_duration_ms=None):
    state = _load_state()
    entry = state.get(provider)

    if entry is None or entry.get("count", 0) < MIN_SAMPLES_FOR_BASELINE:
        return False

    baseline = entry.get("baseline_ema_ms")
    if baseline is None or baseline <= 0:
        return False

    compare_ms = current_duration_ms if current_duration_ms is not None else entry.get("last_duration_ms")

    if compare_ms is None:
        return False

    return compare_ms > baseline * DEGRADATION_FACTOR_THRESHOLD


def get_latency_snapshot(provider):
    state = _load_state()
    entry = state.get(provider)
    if entry is None:
        return None
    return dict(entry)
