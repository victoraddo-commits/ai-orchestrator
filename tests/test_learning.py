from core.remediation_memory import record_result, get_history
from core.learning_engine import evaluate_action
from core.learning import summarize


def test_record_result_captures_what_happened_and_root_cause():
    record_result(
        "inc1", "restart_container", "success",
        issue="Container unhealthy", root_cause="severity=critical, occurrences=3"
    )

    entry = get_history()[0]

    assert entry["issue"] == "Container unhealthy"
    assert entry["root_cause"] == "severity=critical, occurrences=3"
    assert entry["result"] == "success"


def test_record_result_still_works_without_optional_context():
    record_result("inc1", "restart_container", "success")

    entry = get_history()[0]

    assert entry["issue"] is None
    assert entry["root_cause"] is None


def test_summarize_returns_learning_engine_classification_per_action():
    record_result("inc1", "restart_container", "success")
    record_result("inc2", "restart_container", "success")
    record_result("inc3", "restart_container", "success")
    record_result("inc4", "restart_container", "success")

    summary = summarize()

    assert "restart_container" in summary
    assert summary["restart_container"]["recommendation"] == "trusted"
    assert summary["restart_container"] == evaluate_action("restart_container")


def test_summarize_is_empty_with_no_history():
    assert summarize() == {}
