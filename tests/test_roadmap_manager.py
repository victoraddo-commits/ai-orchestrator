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
    # table up to WAITING_FOR_DEPLOY_APPROVAL -- the same states
    # _run_generation's normal (reused as-is) success path leaves a build in
    # -- without invoking the actual AI delegation/security-scan machinery.
    build = roadmap_manager.create_build(name="X", description="d", project_path=project_path)

    builds = load_builds()
    for b in builds:
        if b["id"] == build["id"]:
            for status in ("PLANNING", "WAITING_FOR_ARCHITECTURE_APPROVAL", "ARCHITECTURE_APPROVED", "GENERATING", "SECURITY_REVIEW", "WAITING_FOR_DEPLOY_APPROVAL"):
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


def test_is_self_modifying_true_for_an_isolated_self_build_clone(tmp_path, monkeypatch):
    # _create_isolated_self_clone() gives every self-modifying build its own
    # disposable clone under SELF_BUILD_WORKSPACE_ROOT rather than operating
    # on SELF_PROJECT_PATH directly (see its docstring) -- so a build's
    # project_path is never actually equal to SELF_PROJECT_PATH in practice.
    # is_self_modifying() must recognize those clones too, or every
    # self-modifying build looks like a normal (non-self-modifying) one to
    # anything that branches on this check (e.g. deploy_build()).
    workspace_root = tmp_path / "self-build-workspaces"
    monkeypatch.setattr(roadmap_manager, "SELF_BUILD_WORKSPACE_ROOT", workspace_root)

    clone_dir = workspace_root / "abc123def456"
    clone_dir.mkdir(parents=True)

    assert roadmap_manager.is_self_modifying(str(clone_dir)) is True


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
    monkeypatch.setattr(roadmap_manager, "_create_isolated_self_clone", lambda **kwargs: fake_clone_path)

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

    monkeypatch.setattr(roadmap_manager, "_create_isolated_self_clone", lambda **kwargs: str(tmp_path / "clone"))

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


def _init_git_repo(path, marker_filename):
    import subprocess

    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    (path / marker_filename).write_text("marker\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "initial"], check=True)
    return path


def test_phase_requires_plugin_with_explicit_flag():
    assert roadmap_manager.phase_requires_plugin(
        {"requires_plugin": True, "description": "backend-only wording"}
    ) is True


def test_phase_requires_plugin_via_keyword_in_description():
    # Case-insensitive fallback: historical phases (13G, 13J, 13K, ...) all
    # reference the plugin repo by name without carrying the explicit flag.
    phase = {"description": "New tab in the CloudCLI plugin (/project/src/AI-Orchestrator-Plugin)"}
    assert roadmap_manager.phase_requires_plugin(phase) is True


def test_phase_requires_plugin_via_keyword_in_completion_criteria():
    phase = {
        "description": "frontend work",
        "completion_criteria": ["files under ai-orchestrator-plugin can be edited"],
    }
    assert roadmap_manager.phase_requires_plugin(phase) is True


def test_phase_requires_plugin_false_without_flag_or_keyword():
    phase = {
        "description": "Backend-only refactor of the scheduler",
        "completion_criteria": ["scheduler is faster"],
    }
    assert roadmap_manager.phase_requires_plugin(phase) is False


def test_create_isolated_self_clone_with_plugin_produces_sibling_clones(tmp_path, monkeypatch):
    live_orchestrator = _init_git_repo(tmp_path / "live-orchestrator", "orchestrator_marker.py")
    live_plugin = _init_git_repo(tmp_path / "live-plugin", "plugin_marker.ts")

    workspace_root = tmp_path / "workspaces"
    monkeypatch.setattr(roadmap_manager, "SELF_PROJECT_PATH", live_orchestrator)
    monkeypatch.setattr(roadmap_manager, "PLUGIN_PROJECT_PATH", live_plugin)
    monkeypatch.setattr(roadmap_manager, "SELF_BUILD_WORKSPACE_ROOT", workspace_root)

    workspace = Path(roadmap_manager._create_isolated_self_clone(include_plugin=True))

    # Both repos are real sibling clones under one parent, so a single
    # --dir/project_path covers both trees.
    assert (workspace / "ai-orchestrator" / ".git").exists()
    assert (workspace / "ai-orchestrator" / "orchestrator_marker.py").exists()
    assert (workspace / "ai-orchestrator-plugin" / ".git").exists()
    assert (workspace / "ai-orchestrator-plugin" / "plugin_marker.ts").exists()

    # The parent workspace itself is NOT a git repo -- it's just a directory.
    assert not (workspace / ".git").exists()

    # Everything downstream must still recognize this workspace.
    assert roadmap_manager.is_self_modifying(str(workspace)) is True
    assert roadmap_manager.is_dual_repo_workspace(str(workspace)) is True
    assert roadmap_manager.self_build_repo_paths(str(workspace)) == [
        str(workspace / "ai-orchestrator"),
        str(workspace / "ai-orchestrator-plugin"),
    ]
    assert roadmap_manager.orchestrator_repo_path(str(workspace)) == str(workspace / "ai-orchestrator")


def test_create_isolated_self_clone_copies_memory_snapshot(tmp_path, monkeypatch):
    # memory/ is gitignored, so a plain `git clone` leaves it empty -- a
    # phase whose task is to analyze real memory state (e.g. 13T reading
    # ai_usage_history.json) fails outright without a snapshot. Confirmed
    # live 2026-07-29.
    live_repo = _init_git_repo(tmp_path / "live-orchestrator", "marker.py")
    (live_repo / "memory").mkdir()
    (live_repo / "memory" / "ai_usage_history.json").write_text('{"schema_version": 1, "records": []}')

    monkeypatch.setattr(roadmap_manager, "SELF_PROJECT_PATH", live_repo)
    monkeypatch.setattr(roadmap_manager, "SELF_BUILD_WORKSPACE_ROOT", tmp_path / "workspaces")

    workspace = Path(roadmap_manager._create_isolated_self_clone())

    copied = workspace / "memory" / "ai_usage_history.json"
    assert copied.exists()
    assert copied.read_text() == '{"schema_version": 1, "records": []}'


def test_create_isolated_self_clone_copies_memory_snapshot_into_plugin_workspace_orchestrator_leg(tmp_path, monkeypatch):
    live_orchestrator = _init_git_repo(tmp_path / "live-orchestrator", "orchestrator_marker.py")
    live_plugin = _init_git_repo(tmp_path / "live-plugin", "plugin_marker.ts")
    (live_orchestrator / "memory").mkdir()
    (live_orchestrator / "memory" / "learning_lessons.json").write_text('{"schema_version": 1, "records": {}}')

    monkeypatch.setattr(roadmap_manager, "SELF_PROJECT_PATH", live_orchestrator)
    monkeypatch.setattr(roadmap_manager, "PLUGIN_PROJECT_PATH", live_plugin)
    monkeypatch.setattr(roadmap_manager, "SELF_BUILD_WORKSPACE_ROOT", tmp_path / "workspaces")

    workspace = Path(roadmap_manager._create_isolated_self_clone(include_plugin=True))

    assert (workspace / "ai-orchestrator" / "memory" / "learning_lessons.json").exists()
    # The plugin repo has no memory/ concept -- nothing should be copied there.
    assert not (workspace / "ai-orchestrator-plugin" / "memory").exists()


def test_single_repo_workspace_layout_is_unchanged_without_plugin(tmp_path, monkeypatch):
    # Phases that never touch the plugin must see no behavior change: the
    # workspace itself is the clone, not a parent of sibling clones.
    monkeypatch.setattr(roadmap_manager, "SELF_BUILD_WORKSPACE_ROOT", tmp_path / "workspaces")

    workspace = roadmap_manager._create_isolated_self_clone()

    assert Path(workspace, ".git").exists()
    assert roadmap_manager.is_dual_repo_workspace(workspace) is False
    assert roadmap_manager.self_build_repo_paths(workspace) == [workspace]
    assert roadmap_manager.orchestrator_repo_path(workspace) == workspace


def test_advance_roadmap_requests_a_dual_repo_workspace_for_plugin_phases(isolated_roadmap, monkeypatch, tmp_path):
    _write(isolated_roadmap, [
        {"id": "X", "name": "Control Center tab", "description": "Add a tab to ai-orchestrator-plugin",
         "completion_criteria": [], "status": "pending", "dependencies": [], "priority": 1},
    ])
    roadmap_manager.enable_autonomous_mode()

    captured = {}

    def fake_clone(include_plugin=False):
        captured["include_plugin"] = include_plugin
        return str(tmp_path / "workspace")

    monkeypatch.setattr(roadmap_manager, "_create_isolated_self_clone", fake_clone)
    monkeypatch.setattr(
        roadmap_manager, "create_build",
        lambda name, description, project_path, template=None: {"id": "build-x", "status": "REQUESTED"},
    )

    result = roadmap_manager.advance_roadmap()

    assert result["action"] == "started_phase"
    assert captured["include_plugin"] is True


def test_advance_roadmap_requests_a_single_repo_workspace_for_non_plugin_phases(isolated_roadmap, monkeypatch, tmp_path):
    _write(isolated_roadmap, [
        {"id": "X", "name": "Improve logging", "description": "Add structured logs",
         "completion_criteria": [], "status": "pending", "dependencies": [], "priority": 1},
    ])
    roadmap_manager.enable_autonomous_mode()

    captured = {}

    def fake_clone(include_plugin=False):
        captured["include_plugin"] = include_plugin
        return str(tmp_path / "workspace")

    monkeypatch.setattr(roadmap_manager, "_create_isolated_self_clone", fake_clone)
    monkeypatch.setattr(
        roadmap_manager, "create_build",
        lambda name, description, project_path, template=None: {"id": "build-x", "status": "REQUESTED"},
    )

    result = roadmap_manager.advance_roadmap()

    assert result["action"] == "started_phase"
    assert captured["include_plugin"] is False


def test_build_description_explains_the_dual_repo_layout_for_plugin_phases():
    phase = {
        "id": "X", "description": "Add a tab to ai-orchestrator-plugin",
        "completion_criteria": ["tab exists"],
    }

    description = roadmap_manager._build_description(phase)

    assert "ai-orchestrator/" in description
    assert "ai-orchestrator-plugin/" in description


def test_run_self_build_tests_runs_in_the_orchestrator_repo_of_a_dual_workspace(monkeypatch, tmp_path):
    # tests/ lives in the orchestrator clone, not the workspace parent --
    # pytest must run there or every dual-repo build would fail its gate.
    (tmp_path / "ai-orchestrator" / ".git").mkdir(parents=True)
    (tmp_path / "ai-orchestrator-plugin" / ".git").mkdir(parents=True)

    captured = {}

    class FakeCompleted:
        returncode = 0
        stdout = "3 passed"
        stderr = ""

    def fake_run(cmd, cwd, capture_output, text, timeout):
        captured["cwd"] = cwd
        return FakeCompleted()

    monkeypatch.setattr(roadmap_manager.subprocess, "run", fake_run)

    result = roadmap_manager._run_self_build_tests(str(tmp_path))

    assert captured["cwd"] == str(tmp_path / "ai-orchestrator")
    assert result["passed"] is True


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

    monkeypatch.setattr(roadmap_manager, "_create_isolated_self_clone", lambda **kwargs: str(tmp_path / "clone"))
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

    assert captured["cmd"] == [
        str(roadmap_manager.SELF_PROJECT_PATH / ".venv" / "bin" / "pytest"),
        "tests/", "-m", "not external_api",
    ]
    assert captured["cwd"] == str(tmp_path)
    assert result == {"passed": True, "returncode": 0, "output": "3 passed"}


def test_run_self_build_tests_reports_failure_on_nonzero_exit(monkeypatch, tmp_path):
    class FakeSyncOk:
        returncode = 0
        stdout = ""
        stderr = ""

    class FakeTestFailure:
        returncode = 1
        stdout = ""
        stderr = "1 failed, 2 passed"

    def fake_run(cmd, cwd, capture_output, text, timeout):
        return FakeTestFailure() if cmd[0].endswith("pytest") else FakeSyncOk()

    monkeypatch.setattr(roadmap_manager.subprocess, "run", fake_run)

    result = roadmap_manager._run_self_build_tests(str(tmp_path))

    assert result["passed"] is False
    assert result["returncode"] == 1
    assert "1 failed" in result["output"]


def test_run_self_build_tests_syncs_the_clone_with_live_main_before_testing(monkeypatch, tmp_path):
    calls = []

    class FakeOk:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, cwd, capture_output, text, timeout):
        calls.append(cmd)
        return FakeOk()

    monkeypatch.setattr(roadmap_manager.subprocess, "run", fake_run)

    roadmap_manager._run_self_build_tests(str(tmp_path))

    assert calls[0] == ["git", "fetch", "-q", "origin"]
    assert calls[1] == ["git", "merge", "--no-edit", "-q", "origin/main"]
    assert calls[2][0].endswith("pytest")


def test_run_self_build_tests_reports_stale_base_without_running_pytest_on_merge_conflict(monkeypatch, tmp_path):
    class FakeFetchOk:
        returncode = 0
        stdout = ""
        stderr = ""

    class FakeMergeConflict:
        returncode = 1
        stdout = ""
        stderr = "CONFLICT (content): Merge conflict in core/api.py"

    calls = []

    def fake_run(cmd, cwd, capture_output, text, timeout):
        calls.append(cmd)
        if cmd[:2] == ["git", "fetch"]:
            return FakeFetchOk()
        if cmd[:2] == ["git", "merge"] and "--abort" not in cmd:
            return FakeMergeConflict()
        return FakeFetchOk()

    monkeypatch.setattr(roadmap_manager.subprocess, "run", fake_run)

    result = roadmap_manager._run_self_build_tests(str(tmp_path))

    assert result["passed"] is False
    assert "Stale base" in result["output"]
    assert "CONFLICT" in result["output"]
    assert not any(str(c[0]).endswith("pytest") for c in calls)
    assert ["git", "merge", "--abort"] in calls


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
    # Still sitting at WAITING_FOR_DEPLOY_APPROVAL -- a passing test run must
    # not itself advance the build any further; deploy approval is still a
    # human gate.
    assert updated_build["status"] == "WAITING_FOR_DEPLOY_APPROVAL"


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


def test_advance_roadmap_rejects_a_pending_deploy_approval_when_self_build_tests_fail(isolated_roadmap, monkeypatch, tmp_path):
    # build_manager._create_deploy_approval runs inside advance_builds(),
    # which core.orchestrator_cycle.run_cycle() always calls just before
    # advance_roadmap() -- so a build's deploy Approval can already exist by
    # the time this self-test gate fails it, in the very same cycle. That
    # Approval must not be left sitting "pending" in the CloudCLI Approval
    # Center for a build that's already dead.
    from core.approval import create_build_approval, load_requests

    project_path = str(tmp_path / "clone")
    build_id = _drive_build_to_deploy_approval(project_path)
    approval = create_build_approval(
        build_id=build_id, phase_id="X", approval_type="deploy",
        title="Approve deployment for X", description="0 findings", risk=None,
        requested_action="approve_deploy",
    )

    _write(isolated_roadmap, [
        {"id": "X", "status": "in_progress", "dependencies": [], "priority": 1, "build_id": build_id},
    ])
    roadmap_manager.enable_autonomous_mode()

    monkeypatch.setattr(
        roadmap_manager, "_run_self_build_tests",
        lambda path: {"passed": False, "returncode": 1, "output": "1 failed"},
    )

    roadmap_manager.advance_roadmap()

    updated = [r for r in load_requests() if r["id"] == approval["id"]][0]
    assert updated["status"] == "rejected"


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


def test_self_build_gate_marker_excludes_only_third_party_api_tests_not_local_security_checks():
    # Real regression test for a caught-live security-review finding: the
    # first cut of this fix used "-m not integration", which also silently
    # excluded test_sandbox.py (real Docker sandbox isolation) and
    # test_security_scanner.py (real bandit/semgrep scans) from the
    # self-build deploy gate -- exactly the checks that must NOT be skipped
    # before a self-modifying deploy. external_api must be a strict subset
    # of integration containing ONLY genuinely third-party-API-dependent
    # tests, verified here against pytest's own marker collection rather
    # than just asserting the command string.
    # Uses sys.executable (the interpreter actually running this test right
    # now) rather than SELF_PROJECT_PATH's .venv/bin/pytest -- confirmed
    # live 2026-08-01: SELF_PROJECT_PATH is derived from __file__, so when
    # this whole suite runs from inside an isolated self-build clone (as
    # the self-build gate does), it resolved to the CLONE's own path, which
    # never carries a .venv (gitignored), causing a FileNotFoundError. The
    # currently-running interpreter always has pytest available, regardless
    # of which copy of this test file is executing.
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "pytest",
         "--collect-only", "-q", "-m", "not external_api"],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True, text=True, timeout=60,
    )

    collected = result.stdout

    # Local, no-third-party-API integration tests must still be collected
    # (i.e. still gate self-modifying deploys).
    assert "test_run_in_sandbox_actually_runs_a_real_container" in collected
    assert "test_run_all_scans_against_a_real_vulnerable_fixture" in collected
    assert "test_deploy_build_real_end_to_end" in collected

    # Genuinely third-party-API-dependent tests must be excluded.
    assert "test_call_openrouter_against_real_api" not in collected
    assert "test_openrouter_claude_sonnet_coding_path_against_real_api" not in collected
    assert "test_call_gemini_against_real_api" not in collected
