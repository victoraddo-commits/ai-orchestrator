import json
from pathlib import Path

import pytest

import core.roadmap_manager as roadmap_manager
import core.roadmap_engine as roadmap_engine


@pytest.fixture(autouse=True)
def isolated_roadmap(tmp_path, monkeypatch):
    roadmap_path = tmp_path / "roadmap.json"
    monkeypatch.setattr(roadmap_engine, "ROADMAP_PATH", roadmap_path)
    return roadmap_path


def _write(path, phases):
    path.write_text(json.dumps({"schema_version": 1, "phases": phases}))


def test_autonomous_mode_defaults_to_disabled():
    assert roadmap_manager.is_autonomous_mode_enabled() is False


def test_enable_and_disable_autonomous_mode():
    roadmap_manager.enable_autonomous_mode()
    assert roadmap_manager.is_autonomous_mode_enabled() is True

    roadmap_manager.disable_autonomous_mode()
    assert roadmap_manager.is_autonomous_mode_enabled() is False


def test_is_self_modifying_true_for_the_ai_orchestrator_repo_itself():
    assert roadmap_manager.is_self_modifying(str(roadmap_manager.SELF_PROJECT_PATH)) is True


def test_is_self_modifying_false_for_an_unrelated_path(tmp_path):
    assert roadmap_manager.is_self_modifying(str(tmp_path)) is False


def test_advance_roadmap_does_nothing_when_autonomous_mode_disabled(isolated_roadmap, monkeypatch):
    _write(isolated_roadmap, [{"id": "X", "status": "pending", "dependencies": [], "priority": 1}])

    monkeypatch.setattr(
        roadmap_manager, "create_build",
        lambda *a, **k: pytest.fail("should not create a build while autonomous mode is disabled"),
    )

    result = roadmap_manager.advance_roadmap()

    assert result["action"] == "disabled"
    assert roadmap_engine.get_phase("X")["status"] == "pending"


def test_advance_roadmap_creates_a_self_targeting_build_for_the_next_phase(isolated_roadmap, monkeypatch, tmp_path):
    _write(isolated_roadmap, [
        {"id": "X", "name": "Improve logging", "description": "Add structured logs",
         "completion_criteria": ["logs are structured"], "status": "pending", "dependencies": [], "priority": 1},
    ])
    roadmap_manager.enable_autonomous_mode()

    fake_clone_path = str(tmp_path / "isolated-clone")
    monkeypatch.setattr(roadmap_manager, "_create_isolated_self_clone", lambda: fake_clone_path)

    captured = {}

    def fake_create_build(name, description, project_path, template=None):
        captured["name"] = name
        captured["project_path"] = project_path
        return {"id": "build-123", "status": "REQUESTED"}

    monkeypatch.setattr(roadmap_manager, "create_build", fake_create_build)

    result = roadmap_manager.advance_roadmap()

    assert result["action"] == "started_phase"
    assert result["phase_id"] == "X"
    assert captured["project_path"] == fake_clone_path

    phase = roadmap_engine.get_phase("X")
    assert phase["status"] == "in_progress"
    assert phase["build_id"] == "build-123"


def test_advance_roadmap_never_operates_directly_on_the_live_working_directory(isolated_roadmap, monkeypatch, tmp_path):
    # This is the exact bug behind tonight's repeated build failures: every
    # self-modifying build checked out a branch in this session's own live
    # working directory (SELF_PROJECT_PATH), colliding with interactive git
    # commands and service restarts. create_build must never be called with
    # that literal path again -- it must always receive an isolated clone.
    _write(isolated_roadmap, [
        {"id": "X", "name": "Improve logging", "description": "Add structured logs",
         "completion_criteria": [], "status": "pending", "dependencies": [], "priority": 1},
    ])
    roadmap_manager.enable_autonomous_mode()

    monkeypatch.setattr(roadmap_manager, "_create_isolated_self_clone", lambda: str(tmp_path / "clone"))

    captured = {}

    def fake_create_build(name, description, project_path, template=None):
        captured["project_path"] = project_path
        return {"id": "build-123", "status": "REQUESTED"}

    monkeypatch.setattr(roadmap_manager, "create_build", fake_create_build)

    roadmap_manager.advance_roadmap()

    assert captured["project_path"] != str(roadmap_manager.SELF_PROJECT_PATH)


def test_create_isolated_self_clone_produces_a_real_working_clone(tmp_path, monkeypatch):
    monkeypatch.setattr(roadmap_manager, "SELF_BUILD_WORKSPACE_ROOT", tmp_path / "workspaces")

    workspace = roadmap_manager._create_isolated_self_clone()

    assert Path(workspace).is_dir()
    assert Path(workspace, ".git").exists()
    assert Path(workspace).resolve() != roadmap_manager.SELF_PROJECT_PATH
    # A real clone of the live repo -- not an empty scaffold -- so planning
    # and generation see the actual current codebase, not a blank slate.
    assert Path(workspace, "core", "roadmap_manager.py").exists()


def test_advance_roadmap_marks_phase_completed_when_linked_build_completes(isolated_roadmap, monkeypatch):
    _write(isolated_roadmap, [
        {"id": "X", "status": "in_progress", "dependencies": [], "priority": 1, "build_id": "build-123"},
    ])
    roadmap_manager.enable_autonomous_mode()

    monkeypatch.setattr(roadmap_manager, "get_build", lambda build_id: {"id": build_id, "status": "COMPLETED"})

    result = roadmap_manager.advance_roadmap()

    assert result["action"] == "phase_completed"
    assert roadmap_engine.get_phase("X")["status"] == "completed"


def test_advance_roadmap_marks_phase_failed_when_linked_build_fails_and_does_not_retry(isolated_roadmap, monkeypatch):
    _write(isolated_roadmap, [
        {"id": "X", "status": "in_progress", "dependencies": [], "priority": 1, "build_id": "build-123"},
        {"id": "Y", "status": "pending", "dependencies": [], "priority": 2},
    ])
    roadmap_manager.enable_autonomous_mode()

    monkeypatch.setattr(roadmap_manager, "get_build", lambda build_id: {"id": build_id, "status": "FAILED", "failure_reason": "tests failed"})
    monkeypatch.setattr(
        roadmap_manager, "create_build",
        lambda *a, **k: pytest.fail("a failed phase must not be auto-retried"),
    )

    result = roadmap_manager.advance_roadmap()

    assert result["action"] == "phase_failed"
    assert roadmap_engine.get_phase("X")["status"] == "failed"
    # Y is NOT started in the same tick -- a human needs to look at the
    # failure first; the next advance_roadmap() call will pick Y up.
    assert roadmap_engine.get_phase("Y")["status"] == "pending"


def test_advance_roadmap_does_not_start_a_new_phase_while_another_is_still_waiting(isolated_roadmap, monkeypatch):
    # Real bug found live: a phase stuck at WAITING_FOR_USER (not COMPLETED,
    # not FAILED/ROLLED_BACK) fell through the old loop silently, and
    # advance_roadmap() went on to start a second, unrelated phase
    # concurrently -- both self-modifying builds sharing the same working
    # directory. Confirmed live: this is what crashed the first build's
    # Claude process (a concurrent git checkout on the same working tree).
    _write(isolated_roadmap, [
        {"id": "X", "status": "in_progress", "dependencies": [], "priority": 1, "build_id": "build-1"},
        {"id": "Y", "status": "pending", "dependencies": [], "priority": 2},
    ])
    roadmap_manager.enable_autonomous_mode()

    monkeypatch.setattr(roadmap_manager, "get_build", lambda build_id: {"id": build_id, "status": "WAITING_FOR_ARCHITECTURE_APPROVAL"})
    monkeypatch.setattr(
        roadmap_manager, "create_build",
        lambda *a, **k: pytest.fail("must not start Y while X is still waiting on a human"),
    )

    result = roadmap_manager.advance_roadmap()

    assert result["action"] == "waiting_on_human"
    assert result["phase_id"] == "X"
    assert roadmap_engine.get_phase("Y")["status"] == "pending"


def test_advance_roadmap_reports_nothing_to_do_when_roadmap_is_fully_resolved(isolated_roadmap, monkeypatch):
    _write(isolated_roadmap, [{"id": "X", "status": "completed", "dependencies": [], "priority": 1}])
    roadmap_manager.enable_autonomous_mode()

    result = roadmap_manager.advance_roadmap()

    assert result["action"] == "nothing_to_do"


def test_advance_roadmap_never_auto_approves_anything():
    # There is no function in this module that calls approve_architecture
    # or approve_deploy -- those must only ever be invoked by a human via
    # the API. This test exists to make the intent explicit and catch any
    # future regression that adds one.
    import inspect

    source = inspect.getsource(roadmap_manager)
    assert "approve_architecture" not in source
    assert "approve_deploy(" not in source
