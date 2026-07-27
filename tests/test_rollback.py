from core.remediation import (
    create_remediation,
    start_remediation,
    complete_remediation,
    attempt_rollback,
    register_rollback,
    ROLLBACK_STRATEGIES,
)


def make_completed_remediation(action="restart_container"):
    remediation = create_remediation(
        approval_id="a1", trace_id="inc1", action=action, service="svc-a"
    )
    start_remediation(
        remediation["id"],
        snapshot={"before": "running", "command": "docker restart svc-a", "expected_result": "container running"}
    )
    return complete_remediation(remediation["id"], {"status": "success"})


def test_snapshot_captures_previous_state_command_and_expected_result():
    remediation = make_completed_remediation()

    assert remediation["snapshot"]["before"] == "running"
    assert remediation["snapshot"]["command"] == "docker restart svc-a"
    assert remediation["snapshot"]["expected_result"] == "container running"


def test_rollback_without_a_registered_strategy_is_recorded_as_unavailable():
    remediation = make_completed_remediation()

    result = attempt_rollback(remediation["id"])

    assert result["status"] == "completed"
    assert result["rollback"]["attempted"] is True
    assert result["rollback"]["available"] is False


def test_rollback_with_a_registered_strategy_executes_and_transitions():
    ROLLBACK_STRATEGIES.clear()
    calls = []

    def fake_strategy(remediation):
        calls.append(remediation["service"])
        return {"status": "success", "detail": "restored previous size"}

    register_rollback("resize_resources", fake_strategy)

    remediation = make_completed_remediation(action="resize_resources")

    result = attempt_rollback(remediation["id"])

    assert calls == ["svc-a"]
    assert result["status"] == "rolled_back"
    assert result["rollback"]["attempted"] is True
    assert result["rollback"]["available"] is True
    assert result["rollback"]["result"]["status"] == "success"

    ROLLBACK_STRATEGIES.clear()


def test_attempt_rollback_on_unknown_remediation_returns_none():
    assert attempt_rollback("does-not-exist") is None
