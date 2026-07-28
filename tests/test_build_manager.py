import pytest

import core.build_manager as build_manager
from core.lifecycle import InvalidTransition


def test_create_build_starts_in_requested_state():
    build = build_manager.create_build("todo-app", "A simple todo app", "/tmp/proj")

    assert build["status"] == "REQUESTED"
    assert build["name"] == "todo-app"
    assert build["project_path"] == "/tmp/proj"
    assert build["qa_history"] == []

    loaded = build_manager.get_build(build["id"])
    assert loaded["id"] == build["id"]


def test_get_build_returns_none_for_unknown_id():
    assert build_manager.get_build("does-not-exist") is None


def test_list_builds_returns_all_created_builds():
    build_manager.create_build("a", "desc", "/tmp/a")
    build_manager.create_build("b", "desc", "/tmp/b")

    builds = build_manager.list_builds()

    assert len(builds) == 2


def test_submit_answer_requires_waiting_for_user_state():
    build = build_manager.create_build("todo-app", "desc", "/tmp/proj")

    with pytest.raises(InvalidTransition):
        build_manager.submit_answer(build["id"], "use React")


def test_approve_architecture_requires_waiting_for_user_state():
    build = build_manager.create_build("todo-app", "desc", "/tmp/proj")

    with pytest.raises(InvalidTransition):
        build_manager.approve_architecture(build["id"], operator="alice")


def test_start_generation_requires_architecture_approved_state():
    build = build_manager.create_build("todo-app", "desc", "/tmp/proj")

    with pytest.raises(InvalidTransition):
        build_manager.start_generation(build["id"])


def _force_status(build_id, status):
    # Test helper: jump a build straight to a state without going through
    # advance_builds(), so downstream-endpoint tests don't depend on the
    # coding_bridge-driven planning step actually running.
    builds = build_manager.load_builds()
    for b in builds:
        if b["id"] == build_id:
            b["status"] = status
    build_manager.save_builds(builds)


def test_submit_answer_from_waiting_for_user_records_answer_and_returns_to_planning():
    build = build_manager.create_build("todo-app", "desc", "/tmp/proj")
    _force_status(build["id"], "WAITING_FOR_USER")

    updated = build_manager.submit_answer(build["id"], "Use Postgres, not SQLite")

    assert updated["status"] == "PLANNING"
    assert updated["qa_history"] == [{"answer": "Use Postgres, not SQLite"}]


def test_approve_architecture_transitions_and_tags_operator():
    build = build_manager.create_build("todo-app", "desc", "/tmp/proj")
    _force_status(build["id"], "WAITING_FOR_USER")

    updated = build_manager.approve_architecture(build["id"], operator="cloudcli-plugin")

    assert updated["status"] == "ARCHITECTURE_APPROVED"
    assert updated["architecture_approved_by"] == "cloudcli-plugin"


def test_start_generation_transitions_to_generating():
    build = build_manager.create_build("todo-app", "desc", "/tmp/proj")
    _force_status(build["id"], "ARCHITECTURE_APPROVED")

    updated = build_manager.start_generation(build["id"])

    assert updated["status"] == "GENERATING"


def test_advance_builds_drives_planning_to_waiting_for_user(monkeypatch):
    build = build_manager.create_build("todo-app", "Build a todo app", "/tmp/proj")
    _force_status(build["id"], "PLANNING")

    monkeypatch.setattr(
        build_manager,
        "run_coding_task",
        lambda project_path, instruction, **kwargs: {
            "success": True,
            "aborted": False,
            "session_id": "sess-1",
            "response_text": "Plan: use FastAPI + React. Any preference on database?",
            "files_changed": [],
            "commits": [],
            "tool_errors": [],
        },
    )

    advanced = build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "WAITING_FOR_USER"
    assert "FastAPI" in updated["plan"]
    assert any(b["id"] == build["id"] for b in advanced)


def test_advance_builds_marks_planning_failed_when_bridge_errors(monkeypatch):
    build = build_manager.create_build("todo-app", "Build a todo app", "/tmp/proj")
    _force_status(build["id"], "PLANNING")

    def boom(project_path, instruction, **kwargs):
        raise RuntimeError("CloudCLI /api/agent request failed with status 500")

    monkeypatch.setattr(build_manager, "run_coding_task", boom)

    build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "FAILED"
    assert "500" in updated["failure_reason"]


def test_advance_builds_drives_generating_to_completed_on_success(monkeypatch):
    build = build_manager.create_build("todo-app", "Build a todo app", "/tmp/proj")
    _force_status(build["id"], "GENERATING")

    monkeypatch.setattr(
        build_manager,
        "run_coding_task",
        lambda project_path, instruction, **kwargs: {
            "success": True,
            "aborted": False,
            "session_id": "sess-2",
            "response_text": "Done.",
            "files_changed": ["app/main.py"],
            "commits": [{"sha": "abc123", "message": "implement todo app"}],
            "tool_errors": [],
        },
    )

    build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "COMPLETED"
    assert updated["generation_result"]["commits"] == [{"sha": "abc123", "message": "implement todo app"}]


def test_advance_builds_drives_generating_to_failed_on_unsuccessful_run(monkeypatch):
    build = build_manager.create_build("todo-app", "Build a todo app", "/tmp/proj")
    _force_status(build["id"], "GENERATING")

    monkeypatch.setattr(
        build_manager,
        "run_coding_task",
        lambda project_path, instruction, **kwargs: {
            "success": False,
            "aborted": False,
            "session_id": "sess-3",
            "response_text": "",
            "files_changed": [],
            "commits": [],
            "tool_errors": [{"tool": "Bash", "content": "tests failed"}],
        },
    )

    build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "FAILED"


def test_advance_builds_leaves_terminal_state_builds_alone(monkeypatch):
    build = build_manager.create_build("todo-app", "desc", "/tmp/proj")
    _force_status(build["id"], "COMPLETED")

    monkeypatch.setattr(
        build_manager,
        "run_coding_task",
        lambda *a, **k: pytest.fail("run_coding_task should not be called for a COMPLETED build"),
    )

    build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "COMPLETED"


def test_advance_builds_drives_freshly_requested_build_to_waiting_for_user(monkeypatch):
    build = build_manager.create_build("todo-app", "Build a todo app", "/tmp/proj")
    assert build["status"] == "REQUESTED"

    monkeypatch.setattr(
        build_manager,
        "run_coding_task",
        lambda project_path, instruction, **kwargs: {
            "success": True,
            "aborted": False,
            "session_id": "sess-1",
            "response_text": "Plan: use FastAPI + React.",
            "files_changed": [],
            "commits": [],
            "tool_errors": [],
        },
    )

    build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "WAITING_FOR_USER"


def test_full_lifecycle_happy_path(monkeypatch):
    build = build_manager.create_build("todo-app", "Build a todo app", "/tmp/proj")
    assert build["status"] == "REQUESTED"

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
    build_manager.advance_builds()
    build = build_manager.get_build(build["id"])
    assert build["status"] == "WAITING_FOR_USER"

    build = build_manager.approve_architecture(build["id"], operator="cloudcli-plugin")
    assert build["status"] == "ARCHITECTURE_APPROVED"

    build = build_manager.start_generation(build["id"])
    assert build["status"] == "GENERATING"

    monkeypatch.setattr(
        build_manager,
        "run_coding_task",
        lambda project_path, instruction, **kwargs: {
            "success": True,
            "aborted": False,
            "session_id": "sess-1",
            "response_text": "Implemented.",
            "files_changed": ["app/main.py"],
            "commits": [{"sha": "def456", "message": "implement todo app"}],
            "tool_errors": [],
        },
    )
    build_manager.advance_builds()
    build = build_manager.get_build(build["id"])
    assert build["status"] == "COMPLETED"
