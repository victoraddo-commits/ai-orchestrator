from core.orchestrator_cycle import run_cycle
from core import build_manager
from core import roadmap_manager


def test_run_cycle_completes_without_error_and_has_expected_shape():
    result = run_cycle()

    assert set(result) == {
        "state", "findings", "incidents", "decisions", "builds", "roadmap_progress", "remediation", "verification"
    }
    assert isinstance(result["incidents"], list)
    assert isinstance(result["decisions"], list)
    assert isinstance(result["builds"], list)
    assert result["roadmap_progress"]["action"] == "disabled"
    assert isinstance(result["remediation"], list)
    assert isinstance(result["verification"], list)


def test_run_cycle_processes_a_roadmap_created_build_in_the_same_cycle(monkeypatch, tmp_path):
    # Real gap found live: advance_roadmap() runs *after* advance_builds()
    # within run_cycle(), so a build it creates this cycle previously sat
    # at REQUESTED, untouched, until the *next* scheduled cycle -- a full
    # extra INTERVAL of latency stacked on top of the wait for this cycle
    # to fire at all. A build created by the roadmap step should be worked
    # on the same cycle it's created, not the next one.
    import core.roadmap_engine as roadmap_engine

    roadmap_path = tmp_path / "roadmap.json"
    roadmap_path.write_text(
        '{"schema_version": 1, "phases": [{"id": "X", "name": "n", "description": "d", '
        '"status": "pending", "dependencies": [], "priority": 1}]}'
    )
    monkeypatch.setattr(roadmap_engine, "ROADMAP_PATH", roadmap_path)
    # advance_roadmap() creates self-targeting builds against
    # SELF_PROJECT_PATH -- must not be the real ai-orchestrator repo during
    # a test, or this would do a real `git checkout` against this actual
    # working directory (exactly the bug class fixed live tonight).
    monkeypatch.setattr(roadmap_manager, "SELF_PROJECT_PATH", tmp_path)
    roadmap_manager.enable_autonomous_mode()

    monkeypatch.setattr(
        build_manager,
        "run_coding_task",
        lambda project_path, instruction, **kwargs: {
            "success": True, "aborted": False, "session_id": "s",
            "response_text": "Plan.", "files_changed": [], "commits": [], "tool_errors": [],
        },
    )

    result = run_cycle()

    assert result["roadmap_progress"]["action"] == "started_phase"
    assert len(result["builds"]) == 1
    assert result["builds"][0]["status"] == "WAITING_FOR_USER"


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
