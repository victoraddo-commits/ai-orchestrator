import pytest

import core.build_learning as build_learning


def _build(template="fastapi", status="COMPLETED", **overrides):
    build = {
        "id": "b1",
        "name": "todo-app",
        "template": template,
        "status": status,
        "failure_reason": overrides.get("failure_reason"),
        "security_report": overrides.get("security_report"),
        "generation_result": overrides.get("generation_result"),
        "deployment": overrides.get("deployment"),
    }
    return build


def test_record_build_outcome_stores_a_history_entry():
    build_learning.record_build_outcome(_build())

    history = build_learning.get_build_history()

    assert len(history) == 1
    assert history[0]["template"] == "fastapi"
    assert history[0]["status"] == "COMPLETED"


def test_record_build_outcome_captures_failure_reason():
    build_learning.record_build_outcome(_build(status="FAILED", failure_reason="Docker image build failed"))

    history = build_learning.get_build_history()

    assert history[0]["failure_reason"] == "Docker image build failed"


def test_record_build_outcome_captures_security_summary():
    build_learning.record_build_outcome(_build(
        security_report={"total_findings": 3, "highest_severity": "high"}
    ))

    history = build_learning.get_build_history()

    assert history[0]["security_findings"] == 3
    assert history[0]["highest_severity"] == "high"


def test_get_template_success_rate_computes_percentage():
    build_learning.record_build_outcome(_build(template="fastapi", status="COMPLETED"))
    build_learning.record_build_outcome(_build(template="fastapi", status="COMPLETED"))
    build_learning.record_build_outcome(_build(template="fastapi", status="FAILED"))

    result = build_learning.get_template_success_rate("fastapi")

    assert result["attempts"] == 3
    assert result["success_rate"] == 66.67


def test_get_template_success_rate_treats_rolled_back_as_not_successful():
    build_learning.record_build_outcome(_build(template="fastapi", status="COMPLETED"))
    build_learning.record_build_outcome(_build(template="fastapi", status="ROLLED_BACK"))

    result = build_learning.get_template_success_rate("fastapi")

    assert result["success_rate"] == 50.0


def test_get_template_success_rate_with_no_history():
    result = build_learning.get_template_success_rate("django")

    assert result == {"success_rate": 0, "attempts": 0}


def test_evaluate_template_recommends_trusted_above_80_percent():
    for _ in range(5):
        build_learning.record_build_outcome(_build(template="fastapi", status="COMPLETED"))

    result = build_learning.evaluate_template("fastapi")

    assert result["recommendation"] == "trusted"


def test_evaluate_template_recommends_avoid_below_50_percent():
    build_learning.record_build_outcome(_build(template="react", status="COMPLETED"))
    for _ in range(3):
        build_learning.record_build_outcome(_build(template="react", status="FAILED"))

    result = build_learning.evaluate_template("react")

    assert result["recommendation"] == "avoid"


def test_evaluate_template_with_no_history_is_insufficient():
    result = build_learning.evaluate_template("django")

    assert result["recommendation"] == "insufficient_history"


def test_summarize_templates_covers_every_template_with_history():
    build_learning.record_build_outcome(_build(template="fastapi", status="COMPLETED"))
    build_learning.record_build_outcome(_build(template="react", status="FAILED"))

    summary = build_learning.summarize_templates()

    assert set(summary) == {"fastapi", "react"}
    assert summary["fastapi"]["recommendation"] in ("trusted", "observe", "avoid", "insufficient_history")


def test_record_build_outcome_handles_builds_without_a_template():
    build_learning.record_build_outcome(_build(template=None))

    history = build_learning.get_build_history()

    assert history[0]["template"] is None
    summary = build_learning.summarize_templates()
    assert "None" not in summary and None not in summary


# --- 13F: rollback root-cause capture ------------------------------------

def test_record_build_outcome_captures_rollback_root_cause_when_rolled_back():
    build_learning.record_build_outcome(_build(
        status="ROLLED_BACK",
        deployment={
            "remediation_id": "r1",
            "rollback": {
                "attempted": True,
                "available": False,
                "reason": "No rollback strategy registered for action 'deploy_build'",
            },
        },
    ))

    history = build_learning.get_build_history()

    assert history[0]["status"] == "ROLLED_BACK"
    assert history[0]["rollback_root_cause"] == (
        "No rollback strategy registered for action 'deploy_build'"
    )


def test_record_build_outcome_omits_rollback_root_cause_for_non_rolled_back_statuses():
    build_learning.record_build_outcome(_build(status="COMPLETED"))

    history = build_learning.get_build_history()

    assert "rollback_root_cause" not in history[0]


def test_derive_rollback_root_cause_prefers_explicit_rollback_reason():
    build = _build(
        status="ROLLED_BACK",
        deployment={
            "rollback": {
                "attempted": True,
                "available": True,
                "reason": "manual operator override",
                "result": {"error": "ignored because reason wins"},
            }
        },
        failure_reason="ignored because reason wins",
    )

    assert build_learning._derive_rollback_root_cause(build) == "manual operator override"


def test_derive_rollback_root_cause_describes_missing_strategy():
    build = _build(
        status="ROLLED_BACK",
        deployment={
            "action": "deploy_build",
            "rollback": {"attempted": True, "available": False},
        },
    )

    cause = build_learning._derive_rollback_root_cause(build)

    assert "deploy_build" in cause
    assert "No rollback strategy" in cause


def test_derive_rollback_root_cause_falls_back_to_strategy_result_error():
    build = _build(
        status="ROLLED_BACK",
        deployment={
            "rollback": {
                "attempted": True,
                "available": True,
                "result": {"error": "previous container missing"},
            }
        },
    )

    assert build_learning._derive_rollback_root_cause(build) == "Rollback error: previous container missing"


def test_derive_rollback_root_cause_falls_back_to_rolled_back_to_target():
    build = _build(
        status="ROLLED_BACK",
        deployment={
            "rollback": {
                "attempted": True,
                "available": True,
                "result": {"rolled_back_to": "previous production container"},
            }
        },
    )

    assert build_learning._derive_rollback_root_cause(build) == "Rolled back to previous production container"


def test_derive_rollback_root_cause_falls_back_to_build_failure_reason():
    build = _build(
        status="ROLLED_BACK",
        failure_reason="verification failed: container crashed",
        deployment={"rollback": {"attempted": True, "available": True}},
    )

    assert build_learning._derive_rollback_root_cause(build) == "Build failed: verification failed: container crashed"


def test_derive_rollback_root_cause_handles_missing_data():
    assert build_learning._derive_rollback_root_cause({"status": "ROLLED_BACK"}) == (
        "Rollback requested without a recorded cause"
    )


# --- 13F: lesson store (preferred architectures / common failures /
#          successful solutions / avoided approaches) ---------------------

def test_record_lesson_rejects_unknown_category():
    with pytest.raises(ValueError):
        build_learning.record_lesson(
            category="not_a_real_category",
            subject="x",
            source="test",
        )


def test_record_lesson_persists_a_new_entry():
    build_learning.record_lesson(
        category="preferred_architecture",
        subject="fastapi",
        source="build_outcome",
        evidence={"build_id": "b1"},
        recommendation="trusted",
    )

    lessons = build_learning.get_lessons()

    assert len(lessons) == 1
    assert lessons[0]["category"] == "preferred_architecture"
    assert lessons[0]["subject"] == "fastapi"
    assert lessons[0]["recommendation"] == "trusted"
    assert lessons[0]["evidence"] == {"build_id": "b1"}
    assert "timestamp" in lessons[0]


def test_get_lessons_filters_by_category():
    build_learning.record_lesson("preferred_architecture", "fastapi", "build_outcome", recommendation="trusted")
    build_learning.record_lesson("avoided_approach", "use_redis", "proposal_store", recommendation="avoid")

    preferred = build_learning.get_lessons(category="preferred_architecture")
    avoided = build_learning.get_lessons(category="avoided_approach")

    assert [lesson["subject"] for lesson in preferred] == ["fastapi"]
    assert [lesson["subject"] for lesson in avoided] == ["use_redis"]


def test_summarize_lessons_applies_observe_band_to_preferred_architecture():
    # 3 positive + 2 negative = 5 attempts, 60% -> observe (50 <= rate < 80)
    for _ in range(3):
        build_learning.record_lesson(
            "preferred_architecture", "fastapi", "build_outcome", recommendation="trusted"
        )
    for _ in range(2):
        build_learning.record_lesson(
            "preferred_architecture", "fastapi", "build_outcome", recommendation="avoid"
        )

    summary = build_learning.summarize_lessons(category="preferred_architecture")

    assert summary["fastapi"]["attempts"] == 5
    assert summary["fastapi"]["positive"] == 3
    assert summary["fastapi"]["success_rate"] == 60.0
    assert summary["fastapi"]["recommendation"] == "observe"


def test_summarize_lessons_marks_trusted_when_positive_ratio_meets_threshold():
    # 4 positive + 1 negative = 5 attempts, 80% -> trusted (rate >= 80)
    for _ in range(4):
        build_learning.record_lesson(
            "preferred_architecture", "fastapi", "build_outcome", recommendation="trusted"
        )
    build_learning.record_lesson(
        "preferred_architecture", "fastapi", "build_outcome", recommendation="avoid"
    )

    summary = build_learning.summarize_lessons()

    assert summary["fastapi"]["attempts"] == 5
    assert summary["fastapi"]["positive"] == 4
    assert summary["fastapi"]["recommendation"] == "trusted"


def test_summarize_lessons_marks_avoid_when_positive_ratio_below_50_percent():
    # 1 positive + 4 negative = 5 attempts, 20% -> avoid
    build_learning.record_lesson(
        "preferred_architecture", "fastapi", "build_outcome", recommendation="trusted"
    )
    for _ in range(4):
        build_learning.record_lesson(
            "preferred_architecture", "fastapi", "build_outcome", recommendation="avoid"
        )

    summary = build_learning.summarize_lessons()

    assert summary["fastapi"]["attempts"] == 5
    assert summary["fastapi"]["positive"] == 1
    assert summary["fastapi"]["recommendation"] == "avoid"


def test_summarize_lessons_treats_avoided_approaches_as_negative_signal():
    build_learning.record_lesson(
        "avoided_approach", "use_redis", "proposal_store", recommendation="avoid"
    )

    summary = build_learning.summarize_lessons()

    assert summary["use_redis"]["positive"] == 0
    assert summary["use_redis"]["recommendation"] == "avoid"


def test_summarize_lessons_treats_common_failure_as_negative_signal():
    build_learning.record_lesson(
        "common_failure", "docker_image_build_failed", "build_outcome", recommendation="avoid"
    )

    summary = build_learning.summarize_lessons()

    assert summary["docker_image_build_failed"]["positive"] == 0
    assert summary["docker_image_build_failed"]["category"] == "common_failure"


def test_summarize_lessons_groups_separately_per_subject():
    # fastapi: 5 trusted = 100% -> trusted
    # django:  1 avoid    = 0%   -> avoid
    for _ in range(5):
        build_learning.record_lesson(
            "preferred_architecture", "fastapi", "build_outcome", recommendation="trusted"
        )
    build_learning.record_lesson(
        "preferred_architecture", "django", "build_outcome", recommendation="avoid"
    )

    summary = build_learning.summarize_lessons()

    assert summary["fastapi"]["recommendation"] == "trusted"
    assert summary["django"]["recommendation"] == "avoid"


def test_summarize_lessons_returns_empty_dict_when_no_lessons():
    assert build_learning.summarize_lessons() == {}


def test_recommend_from_rate_zero_attempts_is_insufficient():
    assert build_learning._recommend_from_rate(0, 0) == "insufficient_history"


def test_recommend_from_rate_threshold_boundaries():
    # Exactly at 80 -> trusted; just under -> observe
    assert build_learning._recommend_from_rate(80, 1) == "trusted"
    assert build_learning._recommend_from_rate(79.99, 1) == "observe"
    # Exactly at 50 -> observe; just under -> avoid
    assert build_learning._recommend_from_rate(50, 1) == "observe"
    assert build_learning._recommend_from_rate(49.99, 1) == "avoid"


# --- 13F: ingest rejected proposals from 13C's Improvement Proposal store

def test_ingest_rejected_proposals_creates_avoided_approach_lessons(monkeypatch):
    from core.kai.planner import load_proposals
    from core.lifecycle import new_object

    proposals = [
        new_object(
            "rejected",
            title="Switch to Redis",
            description="Use Redis instead of in-memory cache",
            suggested_action="Add redis dependency",
            rationale="Caches are flaky",
        ),
        new_object(
            "proposed",
            title="Add monitoring",
            description="Wire Prometheus",
            suggested_action="add Prometheus",
            rationale="visibility",
        ),
    ]
    # Append a transition note to the rejected proposal's history.
    proposals[0]["history"].append({"status": "rejected", "timestamp": "now", "note": "out of scope"})

    monkeypatch.setattr("core.kai.planner.load_proposals", lambda: list(proposals))

    result = build_learning.ingest_rejected_proposals()

    assert result["ingested"] == 1
    assert result["total_rejected"] == 1

    lessons = build_learning.get_lessons(category="avoided_approach")
    assert len(lessons) == 1
    lesson = lessons[0]
    assert lesson["subject"] == "Switch to Redis"
    assert lesson["recommendation"] == "avoid"
    assert lesson["evidence"]["proposal_id"] == proposals[0]["id"]
    assert lesson["evidence"]["rejection_note"] == "out of scope"
    assert lesson["evidence"]["suggested_action"] == "Add redis dependency"


def test_ingest_rejected_proposals_with_no_rejections_creates_nothing(monkeypatch):
    from core.lifecycle import new_object

    monkeypatch.setattr(
        "core.kai.planner.load_proposals",
        lambda: [new_object("proposed", title="T", description="d")],
    )

    result = build_learning.ingest_rejected_proposals()

    assert result == {"ingested": 0, "total_rejected": 0}
    assert build_learning.get_lessons(category="avoided_approach") == []


def test_ingest_rejected_proposals_includes_proposals_without_a_rejection_note(monkeypatch):
    from core.lifecycle import new_object

    proposal = new_object("rejected", title="Risky migration", description="d")
    monkeypatch.setattr("core.kai.planner.load_proposals", lambda: [proposal])

    build_learning.ingest_rejected_proposals()

    lessons = build_learning.get_lessons(category="avoided_approach")
    assert lessons[0]["evidence"]["rejection_note"] is None

