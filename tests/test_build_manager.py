import pytest

import core.build_manager as build_manager
from core.lifecycle import InvalidTransition
from core.ai.ai_router import AllProvidersFailed


def test_create_build_rejects_unknown_template():
    with pytest.raises(ValueError):
        build_manager.create_build("todo-app", "desc", "/tmp/proj", template="cobol-mainframe")


def test_create_build_accepts_known_template():
    build = build_manager.create_build("todo-app", "desc", "/tmp/proj", template="fastapi")

    assert build["template"] == "fastapi"


def test_advance_builds_creates_the_repo_before_planning(monkeypatch, tmp_path):
    target = tmp_path / "todo-app"
    build = build_manager.create_build("todo-app", "Build a todo app", str(target))

    monkeypatch.setattr(
        build_manager,
        "delegate",
        lambda description, **kwargs: {"provider": "gemini", "task_type": "planning", "response": "Plan.", "duration_ms": 10},
    )

    build_manager.advance_builds()

    assert target.is_dir()
    assert (target / ".git").is_dir()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "WAITING_FOR_USER"


def test_advance_builds_checks_out_a_dedicated_branch_for_the_build(monkeypatch, tmp_path):
    target = tmp_path / "todo-app"
    build = build_manager.create_build("todo-app", "Build a todo app", str(target))

    monkeypatch.setattr(
        build_manager,
        "delegate",
        lambda description, **kwargs: {"provider": "gemini", "task_type": "planning", "response": "Plan.", "duration_ms": 10},
    )

    build_manager.advance_builds()

    import subprocess
    current = subprocess.run(
        ["git", "branch", "--show-current"], cwd=str(target), capture_output=True, text=True
    ).stdout.strip()
    assert current == f"build-{build['id']}"


def test_planning_prompt_includes_template_base_instruction():
    build = build_manager.create_build("api", "desc", "/tmp/proj", template="fastapi")

    prompt = build_manager._planning_prompt(build)

    assert "FastAPI" in prompt


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


def test_approve_deploy_requires_deploy_approval_state():
    build = build_manager.create_build("todo-app", "desc", "/tmp/proj")

    with pytest.raises(InvalidTransition):
        build_manager.approve_deploy(build["id"], operator="alice")


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


def test_approve_deploy_transitions_and_tags_operator():
    build = build_manager.create_build("todo-app", "desc", "/tmp/proj")
    _force_status(build["id"], "DEPLOY_APPROVAL")

    updated = build_manager.approve_deploy(build["id"], operator="cloudcli-plugin")

    assert updated["status"] == "DEPLOYING"
    assert updated["deploy_approved_by"] == "cloudcli-plugin"


def test_advance_builds_drives_deploying_to_completed_on_success(monkeypatch):
    build = build_manager.create_build("todo-app", "desc", "/tmp/proj")
    _force_status(build["id"], "DEPLOYING")

    monkeypatch.setattr(
        build_manager,
        "deploy_build",
        lambda b: {"deployed": True, "container": "aiapp-todo-app", "port": 32768, "remediation_id": "r1"},
    )

    build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "COMPLETED"
    assert updated["deployment"]["port"] == 32768


def test_advance_builds_drives_deploying_to_failed_on_unsuccessful_deploy(monkeypatch):
    build = build_manager.create_build("todo-app", "desc", "/tmp/proj")
    _force_status(build["id"], "DEPLOYING")

    monkeypatch.setattr(
        build_manager,
        "deploy_build",
        lambda b: {"deployed": False, "reason": "container crashed on boot"},
    )

    build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "FAILED"
    assert "crashed" in updated["failure_reason"]


def test_rollback_deployment_requires_a_prior_deployment():
    build = build_manager.create_build("todo-app", "desc", "/tmp/proj")
    _force_status(build["id"], "COMPLETED")

    with pytest.raises(ValueError):
        build_manager.rollback_deployment(build["id"])


def test_advance_builds_records_learning_outcome_on_generation_failure(monkeypatch):
    from core.build_learning import get_build_history

    build = build_manager.create_build("todo-app", "desc", "/tmp/proj", template="fastapi")
    _force_status(build["id"], "GENERATING")

    monkeypatch.setattr(
        build_manager, "run_coding_task",
        lambda project_path, instruction, **kwargs: {
            "success": False, "aborted": False, "session_id": "s",
            "response_text": "", "files_changed": [], "commits": [], "tool_errors": [],
        },
    )

    build_manager.advance_builds()

    history = get_build_history()
    assert len(history) == 1
    assert history[0]["status"] == "FAILED"
    assert history[0]["template"] == "fastapi"


def test_advance_builds_records_learning_outcome_on_deploy_success(monkeypatch):
    from core.build_learning import get_build_history

    build = build_manager.create_build("todo-app", "desc", "/tmp/proj", template="docker")
    _force_status(build["id"], "DEPLOYING")

    monkeypatch.setattr(
        build_manager, "deploy_build",
        lambda b: {"deployed": True, "container": "aiapp-todo-app", "port": 1234, "remediation_id": "r1"},
    )

    build_manager.advance_builds()

    history = get_build_history()
    assert len(history) == 1
    assert history[0]["status"] == "COMPLETED"


def test_rollback_deployment_transitions_to_rolled_back(monkeypatch):
    build = build_manager.create_build("todo-app", "desc", "/tmp/proj")
    _force_status(build["id"], "COMPLETED")

    builds = build_manager.load_builds()
    for b in builds:
        if b["id"] == build["id"]:
            b["deployment"] = {"deployed": True, "remediation_id": "r1"}
    build_manager.save_builds(builds)

    monkeypatch.setattr(
        build_manager,
        "attempt_rollback",
        lambda remediation_id: {"rollback": {"result": {"rolled_back_to": "previous production container"}}},
    )

    updated = build_manager.rollback_deployment(build["id"])

    assert updated["status"] == "ROLLED_BACK"


def test_advance_builds_drives_planning_to_waiting_for_user(monkeypatch):
    build = build_manager.create_build("todo-app", "Build a todo app", "/tmp/proj")
    _force_status(build["id"], "PLANNING")

    monkeypatch.setattr(
        build_manager,
        "delegate",
        lambda description, **kwargs: {
            "provider": "gemini", "task_type": "planning",
            "response": "Plan: use FastAPI + React. Any preference on database?",
            "duration_ms": 10,
        },
    )

    advanced = build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "WAITING_FOR_USER"
    assert "FastAPI" in updated["plan"]
    assert updated["planned_by"] == "gemini"
    assert any(b["id"] == build["id"] for b in advanced)


def test_advance_builds_marks_planning_failed_when_bridge_errors(monkeypatch):
    build = build_manager.create_build("todo-app", "Build a todo app", "/tmp/proj")
    _force_status(build["id"], "PLANNING")

    def boom(description, **kwargs):
        raise RuntimeError("CloudCLI /api/agent request failed with status 500")

    monkeypatch.setattr(build_manager, "delegate", boom)

    build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "FAILED"
    assert "500" in updated["failure_reason"]


def test_advance_builds_marks_planning_failed_when_all_providers_fail(monkeypatch):
    # delegate() already tries every candidate provider (Gemini/OpenRouter/
    # Minimax/Claude for planning) before raising AllProvidersFailed -- this
    # is a genuine every-provider-failed case, not just "Claude is busy".
    build = build_manager.create_build("todo-app", "Build a todo app", "/tmp/proj")
    _force_status(build["id"], "PLANNING")

    monkeypatch.setattr(
        build_manager, "delegate",
        lambda description, **kwargs: (_ for _ in ()).throw(
            AllProvidersFailed("gemini: not available; openrouter: not available; claude: usage limit reached")
        ),
    )

    build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "FAILED"
    assert "usage limit reached" in updated["failure_reason"]


def test_run_generation_uses_the_longer_generation_timeout_not_the_planning_one(monkeypatch):
    # Generation involves real file writes/tool calls/tests and legitimately
    # takes longer than a quick text-only planning response -- confirmed
    # live tonight (13C's generation hit the 300s wall-clock ceiling while
    # still actively working on a genuinely larger module).
    build = build_manager.create_build("todo-app", "Build a todo app", "/tmp/proj")
    _force_status(build["id"], "GENERATING")

    captured = {}

    def fake_run_coding_task(project_path, instruction, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return {
            "success": True, "aborted": False, "session_id": "s",
            "response_text": "Done.", "files_changed": [], "commits": [], "tool_errors": [],
        }

    monkeypatch.setattr(build_manager, "run_coding_task", fake_run_coding_task)
    monkeypatch.setattr(build_manager, "run_all_scans", lambda project_path: {"scanners": {}, "total_findings": 0, "highest_severity": None})

    build_manager.advance_builds()

    assert captured["timeout"] == build_manager.GENERATION_TIMEOUT
    assert build_manager.GENERATION_TIMEOUT > build_manager.PLANNING_TIMEOUT


def test_advance_builds_drives_generating_to_deploy_approval_via_security_review(monkeypatch):
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
    monkeypatch.setattr(
        build_manager,
        "run_all_scans",
        lambda project_path: {
            "scanners": {}, "total_findings": 2, "highest_severity": "medium",
        },
    )

    build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "DEPLOY_APPROVAL"
    assert updated["generation_result"]["commits"] == [{"sha": "abc123", "message": "implement todo app"}]
    assert updated["security_report"]["total_findings"] == 2
    assert updated["security_report"]["highest_severity"] == "medium"


def test_advance_builds_reaches_deploy_approval_even_with_critical_findings(monkeypatch):
    # Security findings are surfaced for human review via DEPLOY_APPROVAL,
    # not auto-blocked -- consistent with every other approval gate in this
    # system (the human decides, the system doesn't silently decide for them).
    build = build_manager.create_build("todo-app", "Build a todo app", "/tmp/proj")
    _force_status(build["id"], "GENERATING")

    monkeypatch.setattr(
        build_manager,
        "run_coding_task",
        lambda project_path, instruction, **kwargs: {
            "success": True, "aborted": False, "session_id": "s",
            "response_text": "Done.", "files_changed": [], "commits": [], "tool_errors": [],
        },
    )
    monkeypatch.setattr(
        build_manager,
        "run_all_scans",
        lambda project_path: {"scanners": {}, "total_findings": 5, "highest_severity": "critical"},
    )

    build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "DEPLOY_APPROVAL"
    assert updated["security_report"]["highest_severity"] == "critical"


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
        "delegate",
        lambda description, **kwargs: {
            "provider": "gemini", "task_type": "planning",
            "response": "Plan: use FastAPI + React.", "duration_ms": 10,
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
        "delegate",
        lambda description, **kwargs: {
            "provider": "gemini", "task_type": "planning",
            "response": "Plan: FastAPI + SQLite.", "duration_ms": 10,
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
    monkeypatch.setattr(
        build_manager,
        "run_all_scans",
        lambda project_path: {"scanners": {}, "total_findings": 0, "highest_severity": None},
    )
    build_manager.advance_builds()
    build = build_manager.get_build(build["id"])
    assert build["status"] == "DEPLOY_APPROVAL"
    assert build["security_report"]["total_findings"] == 0
