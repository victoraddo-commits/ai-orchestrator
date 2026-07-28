from core.orchestrator_cycle import run_cycle
from core import build_manager


def test_run_cycle_completes_without_error_and_has_expected_shape():
    result = run_cycle()

    assert set(result) == {
        "state", "findings", "incidents", "decisions", "builds", "remediation", "verification"
    }
    assert isinstance(result["incidents"], list)
    assert isinstance(result["decisions"], list)
    assert isinstance(result["builds"], list)
    assert isinstance(result["remediation"], list)
    assert isinstance(result["verification"], list)


def test_run_cycle_advances_pending_builds(monkeypatch):
    build_manager.create_build("todo-app", "Build a todo app", "/tmp/proj")

    monkeypatch.setattr(
        build_manager,
        "run_coding_task",
        lambda project_path, instruction, **kwargs: {
            "success": True,
            "aborted": False,
            "session_id": "sess-1",
            "response_text": "Plan: FastAPI + SQLite.",
            "files_changed": [],
            "commits": [],
            "tool_errors": [],
        },
    )

    result = run_cycle()

    assert len(result["builds"]) == 1
    assert result["builds"][0]["status"] == "WAITING_FOR_USER"


def test_run_cycle_incidents_have_unified_lifecycle_fields():
    result = run_cycle()

    for incident in result["incidents"]:
        assert "trace_id" in incident
        assert "history" in incident
        assert incident["status"] in (
            "open", "investigating", "approved", "executing",
            "verifying", "resolved", "failed", "closed",
        )
