import pytest

import core.ai.ai_router as ai_router
import core.ai.provider_evidence as provider_evidence
import core.build_learning as build_learning
from core.memory import save


def _entry(provider, task_type, success, error=None):
    return {
        "provider": provider,
        "task_type": task_type,
        "description": "whatever",
        "success": success,
        "duration_ms": 1000,
        "error": error,
        "timestamp": "2026-07-29T00:00:00",
    }


def _write_history(entries):
    save(ai_router.USAGE_HISTORY_FILE, entries)


# --- evaluate_provider_role -------------------------------------------------

def test_evaluate_provider_role_counts_attempts_successes_and_failures():
    _write_history([
        _entry("minimax", "planning", True),
        _entry("minimax", "planning", True),
        _entry("minimax", "planning", False, error="boom"),
        _entry("gemini", "planning", True),
    ])

    stats = provider_evidence.evaluate_provider_role("minimax", "planning")

    assert stats["provider"] == "minimax"
    assert stats["task_type"] == "planning"
    assert stats["attempts"] == 3
    assert stats["successes"] == 2
    assert stats["failures"] == 1
    assert stats["success_rate"] == 66.67


def test_evaluate_provider_role_ignores_other_task_types():
    _write_history([
        _entry("minimax", "planning", False),
        _entry("minimax", "documentation", True),
    ])

    assert provider_evidence.evaluate_provider_role("minimax", "planning")["attempts"] == 1
    assert provider_evidence.evaluate_provider_role("minimax", "documentation")["successes"] == 1


def test_evaluate_provider_role_without_task_type_aggregates_every_role():
    _write_history([
        _entry("minimax", "planning", False),
        _entry("minimax", "documentation", True),
    ])

    stats = provider_evidence.evaluate_provider_role("minimax")

    assert stats["task_type"] is None
    assert stats["attempts"] == 2
    assert stats["successes"] == 1


def test_evaluate_provider_role_reports_insufficient_history_with_no_attempts():
    _write_history([_entry("gemini", "planning", True)])

    stats = provider_evidence.evaluate_provider_role("minimax", "log_analysis")

    assert stats["attempts"] == 0
    assert stats["success_rate"] == 0
    assert stats["recommendation"] == "insufficient_history"
    assert stats["sufficient_sample"] is False


def test_evaluate_provider_role_never_says_trusted_below_the_sample_guard():
    # A perfect record over fewer than MIN_SAMPLE_SIZE attempts is real
    # evidence, but not enough of it to call a provider "trusted" -- that is
    # exactly the over-reaction 13T exists to avoid, in both directions.
    _write_history([_entry("opencode_minimax", "coding", True)] * (provider_evidence.MIN_SAMPLE_SIZE - 1))

    stats = provider_evidence.evaluate_provider_role("opencode_minimax", "coding")

    assert stats["success_rate"] == 100.0
    assert stats["sufficient_sample"] is False
    assert stats["recommendation"] == "observe"


def test_evaluate_provider_role_says_trusted_once_the_sample_guard_is_met():
    _write_history([_entry("opencode_minimax", "coding", True)] * provider_evidence.MIN_SAMPLE_SIZE)

    stats = provider_evidence.evaluate_provider_role("opencode_minimax", "coding")

    assert stats["sufficient_sample"] is True
    assert stats["recommendation"] == "trusted"
    assert stats["min_sample_size"] == provider_evidence.MIN_SAMPLE_SIZE


def test_evaluate_provider_role_says_avoid_for_a_mostly_failing_record():
    _write_history([
        _entry("minimax", "planning", False, error="ConnectionError"),
        _entry("minimax", "planning", False, error="ConnectionError"),
        _entry("minimax", "planning", True),
    ])

    assert provider_evidence.evaluate_provider_role("minimax", "planning")["recommendation"] == "avoid"


def test_evaluate_provider_role_collects_distinct_failure_errors_in_order():
    _write_history([
        _entry("minimax", "planning", False, error="ConnectionError"),
        _entry("minimax", "planning", False, error="ConnectionError"),
        _entry("minimax", "planning", False, error="timeout"),
        _entry("minimax", "planning", True),
    ])

    stats = provider_evidence.evaluate_provider_role("minimax", "planning")

    assert stats["failure_errors"] == ["ConnectionError", "timeout"]


def test_evaluate_provider_role_accepts_an_explicit_history_argument():
    _write_history([])

    stats = provider_evidence.evaluate_provider_role(
        "minimax", "planning", history=[_entry("minimax", "planning", True)]
    )

    assert stats["attempts"] == 1


# --- summarize_usage --------------------------------------------------------

def test_summarize_usage_nests_stats_by_provider_then_task_type():
    _write_history([
        _entry("minimax", "planning", False),
        _entry("opencode", "coding", True),
        _entry("opencode", "coding", True),
    ])

    summary = provider_evidence.summarize_usage()

    assert set(summary) == {"minimax", "opencode"}
    assert summary["minimax"]["planning"]["attempts"] == 1
    assert summary["opencode"]["coding"]["successes"] == 2


def test_summarize_usage_is_empty_for_an_empty_history():
    _write_history([])

    assert provider_evidence.summarize_usage() == {}


def test_summarize_usage_buckets_entries_with_no_task_type_under_unknown():
    _write_history([{"provider": "minimax", "success": True}])

    summary = provider_evidence.summarize_usage()

    assert summary["minimax"]["unknown"]["attempts"] == 1


def test_summarize_usage_keeps_untagged_entries_out_of_the_tagged_buckets():
    _write_history([
        _entry("minimax", "planning", False),
        _entry("minimax", "planning", False),
        {"provider": "minimax", "success": True},
    ])

    summary = provider_evidence.summarize_usage()

    assert summary["minimax"]["planning"]["attempts"] == 2
    assert summary["minimax"]["unknown"]["attempts"] == 1
    assert summary["minimax"]["unknown"]["task_type"] == "unknown"


# --- record_usage_lesson ----------------------------------------------------

def test_record_usage_lesson_embeds_the_real_counts_as_evidence():
    _write_history([
        _entry("minimax", "planning", True),
        _entry("minimax", "planning", False, error="ConnectionError"),
    ])

    lesson = provider_evidence.record_usage_lesson(
        "minimax", "planning", source="13T usage-history review"
    )

    assert lesson["evidence"]["attempts"] == 2
    assert lesson["evidence"]["successes"] == 1
    assert lesson["evidence"]["failures"] == 1
    assert lesson["evidence"]["success_rate"] == 50.0
    assert lesson["source"] == "13T usage-history review"
    assert build_learning.get_lessons() == [lesson]


def test_record_usage_lesson_defaults_a_failing_record_to_common_failure():
    _write_history([_entry("minimax", "planning", False)])

    lesson = provider_evidence.record_usage_lesson("minimax", "planning", source="13T")

    assert lesson["category"] == "common_failure"
    assert lesson["recommendation"] == "avoid"


def test_record_usage_lesson_defaults_a_passing_record_to_successful_solution():
    _write_history([_entry("opencode", "coding", True)] * 3)

    lesson = provider_evidence.record_usage_lesson("opencode", "coding", source="13T")

    assert lesson["category"] == "successful_solution"
    assert lesson["recommendation"] == "observe"


def test_record_usage_lesson_defaults_subject_to_provider_and_task_type():
    _write_history([_entry("opencode", "coding", True)])

    lesson = provider_evidence.record_usage_lesson("opencode", "coding", source="13T")

    assert lesson["subject"] == "opencode/coding"


def test_record_usage_lesson_accepts_an_explicit_subject():
    _write_history([_entry("opencode", "coding", True)])

    lesson = provider_evidence.record_usage_lesson(
        "opencode", "coding", source="13T", subject="opencode/minimax-m2.7 coding_agent"
    )

    assert lesson["subject"] == "opencode/minimax-m2.7 coding_agent"


def test_record_usage_lesson_lets_caller_evidence_override_the_derived_counts():
    # The recorded success flag is not always the truth (13S: before plan
    # validation landed, any HTTP 200 counted as success) -- a review that
    # verified the actual outputs must be able to say so without losing the
    # raw recorded numbers.
    _write_history([_entry("minimax", "planning", True)] * 3)

    lesson = provider_evidence.record_usage_lesson(
        "minimax",
        "planning",
        source="13T",
        recommendation="avoid",
        evidence={"verified_successes": 0, "recorded_success_flags_unreliable": True},
    )

    assert lesson["evidence"]["successes"] == 3
    assert lesson["evidence"]["verified_successes"] == 0
    assert lesson["evidence"]["recorded_success_flags_unreliable"] is True
    assert lesson["recommendation"] == "avoid"
    assert lesson["category"] == "common_failure"


def test_record_usage_lesson_accepts_an_explicit_category():
    _write_history([_entry("opencode", "coding", True)] * 3)

    lesson = provider_evidence.record_usage_lesson(
        "opencode", "coding", source="13T", category="preferred_architecture"
    )

    assert lesson["category"] == "preferred_architecture"


def test_record_usage_lesson_rejects_an_unknown_category():
    _write_history([_entry("opencode", "coding", True)])

    with pytest.raises(ValueError):
        provider_evidence.record_usage_lesson(
            "opencode", "coding", source="13T", category="not_a_category"
        )
