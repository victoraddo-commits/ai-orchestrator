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
