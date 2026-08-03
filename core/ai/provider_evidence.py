"""Phase 13T: evidence-based provider/role evaluation from real usage history.

13P recorded a ``common_failure`` lesson against "minimax" off a *single*
bad planning response. That was the right call for the incident but the
wrong shape of evidence for a routing decision: one datapoint can neither
condemn a provider across every role nor clear it in any. This module turns
``memory/ai_usage_history.json`` -- which core.ai.ai_router.record_usage has
been appending to since 12J -- into per-(provider, task_type) success and
failure counts so the next such question is answered from data instead of
from the most recent memorable incident.

Deliberately reuses core.build_learning's existing trusted/observe/avoid
ladder (``_recommend_from_rate``, lifted out in 13F precisely so lessons
could share it) rather than inventing a second classification scheme, with
one addition: MIN_SAMPLE_SIZE. A 1/1 or 3/3 record scores 100% and would
otherwise come out "trusted" off almost no evidence, so anything below the
guard is capped at "observe" -- real signal, not yet a promotion.

Adds support for Qwen3-Coder specific metrics to enable evidence-based
provider evaluation for dedicated GPU accelerators.
"""

from core.build_learning import LESSON_CATEGORIES, _recommend_from_rate, record_lesson
import core.ai.ai_router as ai_router
import time


# Below this many recorded attempts, a perfect (or terrible) rate is not yet
# a promotion signal -- see the module docstring. 5 is the smallest sample
# where a single outcome moves the rate by less than the 30-point gap
# between the observe and trusted thresholds.
MIN_SAMPLE_SIZE = 5

UNKNOWN_TASK_TYPE = "unknown"


def _history(history=None):
    return ai_router.get_usage_history() if history is None else history


def _stats(provider, task_type, entries):
    """Counts for an already-filtered set of usage entries."""
    attempts = len(entries)
    successes = sum(1 for entry in entries if entry.get("success"))
    failures = attempts - successes
    success_rate = round(successes / attempts * 100, 2) if attempts else 0

    sufficient_sample = attempts >= MIN_SAMPLE_SIZE
    recommendation = _recommend_from_rate(success_rate, attempts)

    # "trusted" off 1/1 or 3/3 is exactly the over-reaction 13T exists to
    # avoid -- in the favourable direction this time, but the same mistake.
    if recommendation == "trusted" and not sufficient_sample:
        recommendation = "observe"

    failure_errors = []
    for entry in entries:
        if entry.get("success"):
            continue
        error = entry.get("error")
        if error and error not in failure_errors:
            failure_errors.append(error)

    return {
        "provider": provider,
        "task_type": task_type,
        "attempts": attempts,
        "successes": successes,
        "failures": failures,
        "success_rate": success_rate,
        "sufficient_sample": sufficient_sample,
        "min_sample_size": MIN_SAMPLE_SIZE,
        "recommendation": recommendation,
        "failure_errors": failure_errors,
    }


def evaluate_provider_role(provider, task_type=None, history=None):
    """Real success/failure counts for one provider in one role.

    ``task_type=None`` aggregates every role the provider has been used
    for. The returned ``recommendation`` is core.build_learning's ladder,
    capped at "observe" while ``sufficient_sample`` is False.
    """
    entries = [
        entry for entry in _history(history)
        if entry.get("provider") == provider
        and (task_type is None or entry.get("task_type") == task_type)
    ]

    return _stats(provider, task_type, entries)


def summarize_usage(history=None):
    """Every (provider, task_type) pair in the history, nested by provider.

    Buckets entries directly rather than re-filtering per pair, so a
    provider with a mix of tagged and untagged entries keeps them in
    separate buckets (``evaluate_provider_role(provider, None)`` would
    fold every role into the untagged one).
    """
    buckets = {}

    for entry in _history(history):
        provider = entry.get("provider")
        if not provider:
            continue
        task_type = entry.get("task_type") or UNKNOWN_TASK_TYPE
        buckets.setdefault(provider, {}).setdefault(task_type, []).append(entry)

    return {
        provider: {
            task_type: _stats(provider, task_type, entries)
            for task_type, entries in sorted(by_task_type.items())
        }
        for provider, by_task_type in buckets.items()
    }


def record_usage_lesson(
    provider,
    task_type=None,
    source=None,
    subject=None,
    category=None,
    recommendation=None,
    evidence=None,
    history=None,
):
    """Record a core.build_learning lesson carrying the real usage counts.
    
    The derived stats become the lesson's evidence, so a future reader sees
    the numbers the conclusion rested on rather than just the conclusion.
    ``evidence`` is merged last and therefore wins: a review that actually
    inspected the outputs (and found the recorded ``success`` flags
    misleading -- see the module docstring) can correct the record without
    hiding the raw counts.
    """
    if category is not None and category not in LESSON_CATEGORIES:
        raise ValueError(f"Unknown lesson category: {category!r}")

    stats = evaluate_provider_role(provider, task_type, history=history)

    resolved_recommendation = recommendation or stats["recommendation"]

    if category is None:
        category = "common_failure" if resolved_recommendation == "avoid" else "successful_solution"

    if subject is None:
        subject = f"{provider}/{task_type}" if task_type else provider

    return record_lesson(
        category=category,
        subject=subject,
        source=source,
        evidence={**stats, **(evidence or {})},
        recommendation=resolved_recommendation,
    )


def get_qwen3_coder_metrics():
    """
    Retrieve Qwen3-Coder specific performance metrics from usage history
    """
    try:
        # Get all Qwen3-Coder related entries
        entries = [
            entry for entry in _history()
            if entry.get("provider", "").startswith("qwen3_coder") or entry.get("provider", "") == "qwen3_coder"
        ]
        
        if not entries:
            return {
                "provider": "qwen3_coder",
                "total_requests": 0,
                "success_rate": 0.0,
                "avg_response_time": 0.0,
                "error_count": 0,
                "latency_distribution": {}
            }
        
        # Calculate metrics
        total_requests = len(entries)
        successful_requests = sum(1 for e in entries if e.get("success", False))
        success_rate = round((successful_requests / total_requests) * 100, 2)
        
        # Calculate average response time
        total_response_time = 0
        error_count = 0
        latency_distribution = {}
        
        for entry in entries:
            response_time = entry.get("response_time", 0)
            if response_time:
                total_response_time += response_time
                
            if not entry.get("success", False):
                error_count += 1
                
            # Track latency distribution (bucketed)
            latency_bucket = "0-1s"
            if response_time > 1:
                latency_bucket = "1-5s"
            if response_time > 5:
                latency_bucket = "5-10s"
            if response_time > 10:
                latency_bucket = "10+s"
                
            latency_distribution[latency_bucket] = latency_distribution.get(latency_bucket, 0) + 1
        
        avg_response_time = round(total_response_time / len(entries), 2) if entries else 0
        
        return {
            "provider": "qwen3_coder",
            "total_requests": total_requests,
            "success_rate": success_rate,
            "avg_response_time": avg_response_time,
            "error_count": error_count,
            "latency_distribution": latency_distribution
        }
        
    except Exception as e:
        return {
            "provider": "qwen3_coder",
            "error": str(e),
            "total_requests": 0,
            "success_rate": 0.0,
            "avg_response_time": 0.0,
            "error_count": 0,
            "latency_distribution": {}
        }