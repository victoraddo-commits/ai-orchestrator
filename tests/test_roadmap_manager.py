import json
from pathlib import Path

import pytest

import core.roadmap_manager as roadmap_manager
import core.roadmap_engine as roadmap_engine
from core.kai.planner import load_proposals, save_proposals
from core.build_manager import BUILD_TRANSITIONS, load_builds, save_builds
from core.lifecycle import transition


@pytest.fixture(autouse=True)
def isolated_roadmap(tmp_path, monkeypatch):
    roadmap_path = tmp_path / "roadmap.json"
    monkeypatch.setattr(roadmap_engine, "ROADMAP_PATH", roadmap_path)
    return roadmap_path


def _write(path, phases):
    path.write_text(json.dumps({"schema_version": 1, "phases": phases}))


def _write_proposal(roadmap_phase_id, **fields):
    proposals = load_proposals()
    proposals.append({
        "id": f"prop-{roadmap_phase_id}",
        "roadmap_phase_id": roadmap_phase_id,
        "rationale": "",
        "description": "",
        "suggested_action": "",
        **fields,
    })
    save_proposals(proposals)


def _drive_build_to_deploy_approval(project_path):
    # Drives a real build through the real, unmodified BUILD_TRANSITIONS
    # table up to DEPLOY_APPROVAL -- the same states _run_generation's
    # normal (reused as-is) success path leaves a build in -- without
    # invoking the actual AI delegation/security-scan machinery.
    build = roadmap_manager.create_build(name="X", description="d", project_path=project_path)

    builds = load_builds()
    for b in builds:
        if b["id"] == build["id"]:
            for status in ("PLANNING", "WAITING_FOR_USER", "ARCHITECTURE_APPROVED", "GENERATING", "SECURITY_REVIEW", "DEPLOY_APPROVAL"):
                transition(b, status, BUILD_TRANSITIONS)
    save_builds(builds)

    return build["id"]


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

    monkeypatch.setattr(roadmap_manager, "get_build", lambda build_id: {"id": build_id, "status": "WAITING_FOR_USER"})
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


def test_select_next_phase_prefers_higher_value_over_lower_priority_number(isolated_roadmap):
    _write(isolated_roadmap, [
        {"id": "A", "name": "Cosmetic tweak", "description": "A cosmetic, low priority cleanup",
         "status": "pending", "dependencies": [], "priority": 1},
        {"id": "B", "name": "Important fix", "description": "placeholder",
         "status": "pending", "dependencies": [], "priority": 2},
    ])
    _write_proposal(
        "B",
        rationale="This is high value and high impact, and low risk since it's an isolated, read-only change.",
    )

    next_phase = roadmap_manager._select_next_phase()

    assert next_phase["id"] == "B"


def test_select_next_phase_uses_structured_risk_and_benefit_fields_when_present(isolated_roadmap):
    _write(isolated_roadmap, [
        {"id": "A", "status": "pending", "dependencies": [], "priority": 1, "description": ""},
        {"id": "B", "status": "pending", "dependencies": [], "priority": 2, "description": ""},
    ])
    _write_proposal("A", risk_level="high", expected_benefit="low")
    _write_proposal("B", risk_level="low", expected_benefit="high")

    assert roadmap_manager._select_next_phase()["id"] == "B"


def test_select_next_phase_falls_back_to_priority_when_value_scores_tie(isolated_roadmap):
    _write(isolated_roadmap, [
        {"id": "A", "status": "pending", "dependencies": [], "priority": 5, "description": "generic work"},
        {"id": "B", "status": "pending", "dependencies": [], "priority": 2, "description": "generic work"},
    ])

    assert roadmap_manager._select_next_phase()["id"] == "B"


def test_select_next_phase_returns_none_when_no_candidates(isolated_roadmap):
    _write(isolated_roadmap, [{"id": "A", "status": "completed", "dependencies": [], "priority": 1}])

    assert roadmap_manager._select_next_phase() is None


def test_advance_roadmap_starts_the_highest_value_phase_not_just_lowest_priority(isolated_roadmap, monkeypatch, tmp_path):
    _write(isolated_roadmap, [
        {"id": "A", "name": "Cosmetic tweak", "description": "A cosmetic, low priority, nice to have tweak",
         "status": "pending", "dependencies": [], "priority": 1},
        {"id": "B", "name": "Critical fix", "description": "placeholder",
         "status": "pending", "dependencies": [], "priority": 2},
    ])
    _write_proposal(
        "B",
        rationale="This is a critical fix with high impact and low risk since it's an isolated, read-only change.",
    )
    roadmap_manager.enable_autonomous_mode()

    monkeypatch.setattr(roadmap_manager, "_create_isolated_self_clone", lambda: str(tmp_path / "clone"))
    monkeypatch.setattr(
        roadmap_manager, "create_build",
        lambda name, description, project_path, template=None: {"id": "build-x", "status": "REQUESTED"},
    )

    result = roadmap_manager.advance_roadmap()

    assert result["action"] == "started_phase"
    assert result["phase_id"] == "B"


def test_run_self_build_tests_uses_the_fixed_pytest_command(monkeypatch, tmp_path):
    captured = {}

    class FakeCompleted:
        returncode = 0
        stdout = "3 passed"
        stderr = ""

    def fake_run(cmd, cwd, capture_output, text, timeout):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        return FakeCompleted()

    monkeypatch.setattr(roadmap_manager.subprocess, "run", fake_run)

    result = roadmap_manager._run_self_build_tests(str(tmp_path))

    assert captured["cmd"] == [str(roadmap_manager.SELF_PROJECT_PATH / ".venv" / "bin" / "pytest"), "tests/"]
    assert captured["cwd"] == str(tmp_path)
    assert result == {"passed": True, "returncode": 0, "output": "3 passed"}


def test_run_self_build_tests_reports_failure_on_nonzero_exit(monkeypatch, tmp_path):
    class FakeCompleted:
        returncode = 1
        stdout = ""
        stderr = "1 failed, 2 passed"

    monkeypatch.setattr(roadmap_manager.subprocess, "run", lambda *a, **k: FakeCompleted())

    result = roadmap_manager._run_self_build_tests(str(tmp_path))

    assert result["passed"] is False
    assert result["returncode"] == 1
    assert "1 failed" in result["output"]


def test_run_self_build_tests_handles_a_missing_pytest_executable(monkeypatch, tmp_path):
    def fake_run(*a, **k):
        raise FileNotFoundError("no such file or directory")

    monkeypatch.setattr(roadmap_manager.subprocess, "run", fake_run)

    result = roadmap_manager._run_self_build_tests(str(tmp_path))

    assert result["passed"] is False
    assert result["returncode"] is None


def test_advance_roadmap_runs_self_build_tests_once_build_reaches_deploy_approval(isolated_roadmap, monkeypatch, tmp_path):
    project_path = str(tmp_path / "clone")
    build_id = _drive_build_to_deploy_approval(project_path)

    _write(isolated_roadmap, [
        {"id": "X", "status": "in_progress", "dependencies": [], "priority": 1, "build_id": build_id},
    ])
    roadmap_manager.enable_autonomous_mode()

    captured = {}

    def fake_run_tests(path):
        captured["project_path"] = path
        return {"passed": True, "returncode": 0, "output": "5 passed"}

    monkeypatch.setattr(roadmap_manager, "_run_self_build_tests", fake_run_tests)

    result = roadmap_manager.advance_roadmap()

    assert captured["project_path"] == project_path
    assert result["action"] == "waiting_on_human"

    updated_build = roadmap_manager.get_build(build_id)
    assert updated_build["self_build_test_result"]["passed"] is True
    # Still sitting at DEPLOY_APPROVAL -- a passing test run must not itself
    # advance the build any further; deploy approval is still a human gate.
    assert updated_build["status"] == "DEPLOY_APPROVAL"


def test_advance_roadmap_fails_the_phase_when_self_build_tests_fail(isolated_roadmap, monkeypatch, tmp_path):
    project_path = str(tmp_path / "clone")
    build_id = _drive_build_to_deploy_approval(project_path)

    _write(isolated_roadmap, [
        {"id": "X", "status": "in_progress", "dependencies": [], "priority": 1, "build_id": build_id},
    ])
    roadmap_manager.enable_autonomous_mode()

    monkeypatch.setattr(
        roadmap_manager, "_run_self_build_tests",
        lambda path: {"passed": False, "returncode": 1, "output": "1 failed"},
    )

    result = roadmap_manager.advance_roadmap()

    assert result["action"] == "phase_failed"
    assert result["phase_id"] == "X"
    assert "Self-build test suite failed" in result["reason"]

    updated_build = roadmap_manager.get_build(build_id)
    assert updated_build["status"] == "FAILED"
    assert updated_build["self_build_test_result"]["passed"] is False

    assert roadmap_engine.get_phase("X")["status"] == "failed"


def test_advance_roadmap_does_not_rerun_self_build_tests_once_recorded(isolated_roadmap, monkeypatch, tmp_path):
    project_path = str(tmp_path / "clone")
    build_id = _drive_build_to_deploy_approval(project_path)

    builds = load_builds()
    for b in builds:
        if b["id"] == build_id:
            b["self_build_test_result"] = {"passed": True, "returncode": 0, "output": "ok"}
    save_builds(builds)

    _write(isolated_roadmap, [
        {"id": "X", "status": "in_progress", "dependencies": [], "priority": 1, "build_id": build_id},
    ])
    roadmap_manager.enable_autonomous_mode()

    monkeypatch.setattr(
        roadmap_manager, "_run_self_build_tests",
        lambda *a, **k: pytest.fail("must not rerun tests once a result is already recorded"),
    )

    result = roadmap_manager.advance_roadmap()

    assert result["action"] == "waiting_on_human"


def test_advance_roadmap_never_auto_approves_anything():
    # There is no function in this module that calls approve_architecture
    # or approve_deploy -- those must only ever be invoked by a human via
    # the API. This test exists to make the intent explicit and catch any
    # future regression that adds one.
    import inspect

    source = inspect.getsource(roadmap_manager)
    assert "approve_architecture" not in source
    assert "approve_deploy(" not in source
