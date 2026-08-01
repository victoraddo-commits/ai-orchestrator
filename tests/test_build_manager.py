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
        lambda description, **kwargs: {"provider": "gemini", "task_type": "planning", "response": "Architecture: FastAPI + React", "duration_ms": 10},
    )

    build_manager.advance_builds()

    assert target.is_dir()
    assert (target / ".git").is_dir()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "WAITING_FOR_ARCHITECTURE_APPROVAL"


def test_ensure_repo_creates_the_build_branch_in_both_repos_of_a_dual_workspace(tmp_path):
    # A dual-repo self-build workspace is a plain parent directory holding
    # two sibling clones (see roadmap_manager._create_isolated_self_clone).
    # The build branch must be created in each actual repo -- and the parent
    # itself must never be git-inited, which would bury both clones as
    # untracked nested repos.
    import subprocess

    for name in ("ai-orchestrator", "ai-orchestrator-plugin"):
        repo = tmp_path / name
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@e.com"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "initial"], check=True)

    build_manager._ensure_repo({"id": "b9", "project_path": str(tmp_path)})

    for name in ("ai-orchestrator", "ai-orchestrator-plugin"):
        branch = subprocess.run(
            ["git", "-C", str(tmp_path / name), "branch", "--show-current"],
            capture_output=True, text=True,
        ).stdout.strip()
        assert branch == "build-b9", f"expected build branch in {name}, got {branch!r}"

    assert not (tmp_path / ".git").exists()


def test_ensure_repo_single_repo_behavior_is_unchanged(tmp_path):
    # Ordinary builds (and single-repo self-builds) still get exactly the
    # old behavior: project_path itself is the repo, branch checked out there.
    import subprocess

    target = tmp_path / "todo-app"

    build_manager._ensure_repo({"id": "b7", "project_path": str(target)})

    assert (target / ".git").is_dir()
    branch = subprocess.run(
        ["git", "-C", str(target), "branch", "--show-current"],
        capture_output=True, text=True,
    ).stdout.strip()
    assert branch == "build-b7"


def test_advance_builds_checks_out_a_dedicated_branch_for_the_build(monkeypatch, tmp_path):
    target = tmp_path / "todo-app"
    build = build_manager.create_build("todo-app", "Build a todo app", str(target))

    monkeypatch.setattr(
        build_manager,
        "delegate",
        lambda description, **kwargs: {"provider": "gemini", "task_type": "planning", "response": "Architecture: FastAPI + React", "duration_ms": 10},
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
    _force_status(build["id"], "WAITING_FOR_USER_INPUT")

    updated = build_manager.submit_answer(build["id"], "Use Postgres, not SQLite")

    assert updated["status"] == "PLANNING"
    assert updated["qa_history"] == [{"answer": "Use Postgres, not SQLite"}]


def test_approve_architecture_transitions_and_tags_operator():
    build = build_manager.create_build("todo-app", "desc", "/tmp/proj")
    _force_status(build["id"], "WAITING_FOR_ARCHITECTURE_APPROVAL")

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
    _force_status(build["id"], "WAITING_FOR_DEPLOY_APPROVAL")

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


def test_advance_builds_restarts_stale_services_after_a_successful_self_modifying_deploy(monkeypatch):
    # 17C: a merged self-modifying deploy leaves the running services on
    # stale pre-merge code until they restart -- the deploy pipeline itself
    # must trigger the restart, after the build's COMPLETED outcome is
    # already persisted.
    build = build_manager.create_build("17C", "desc", "/tmp/proj")
    _force_status(build["id"], "DEPLOYING")

    deploy_result = {
        "deployed": True,
        "merged_branch": f"build-{build['id']}",
        "merge_commit": "abc123",
        "changed_files": ["core/api.py"],
    }
    monkeypatch.setattr(build_manager, "deploy_build", lambda b: deploy_result)

    restart_calls = []

    def fake_restart(b, result):
        # The build must already be persisted as COMPLETED before any
        # restart runs: restarting ai-orchestrator.service kills the very
        # process executing this, and the outcome must never be lost.
        persisted = build_manager.get_build(b["id"])
        restart_calls.append((b["id"], result, persisted["status"]))
        return [{"service": "ai-orchestrator-api.service", "restarted": True}]

    monkeypatch.setattr(build_manager, "restart_services_if_needed", fake_restart)

    build_manager.advance_builds()

    assert len(restart_calls) == 1
    build_id, result, persisted_status = restart_calls[0]
    assert build_id == build["id"]
    assert result["changed_files"] == ["core/api.py"]
    assert persisted_status == "COMPLETED"


def test_advance_builds_never_restarts_services_after_a_failed_deploy(monkeypatch):
    build = build_manager.create_build("17C", "desc", "/tmp/proj")
    _force_status(build["id"], "DEPLOYING")

    monkeypatch.setattr(
        build_manager,
        "deploy_build",
        lambda b: {"deployed": False, "reason": "Merge conflict merging build-x"},
    )

    restart_calls = []
    monkeypatch.setattr(
        build_manager,
        "restart_services_if_needed",
        lambda b, result: restart_calls.append(b["id"]),
    )

    build_manager.advance_builds()

    assert build_manager.get_build(build["id"])["status"] == "FAILED"
    assert restart_calls == []


def test_advance_builds_redeploys_the_plugin_bundle_before_restarting_services(monkeypatch):
    # 17H: a merged self-modifying deploy that landed plugin source leaves
    # CloudCLI serving a stale compiled bundle until npm run build runs and
    # the output is copied+served -- the deploy pipeline itself must trigger
    # that, and it must happen BEFORE the 17C service restarts (the queued
    # scheduler restart kills the very process that would run npm).
    build = build_manager.create_build("17H", "desc", "/tmp/proj")
    _force_status(build["id"], "DEPLOYING")

    deploy_result = {
        "deployed": True,
        "merged_branch": f"build-{build['id']}",
        "merge_commit": "abc123",
        "merged_repos": {
            "/project/src/ai-orchestrator": "abc123",
            "/project/src/ai-orchestrator-plugin": "def456",
        },
        "changed_files": ["roadmap.json"],
    }
    monkeypatch.setattr(build_manager, "deploy_build", lambda b: deploy_result)

    calls = []
    monkeypatch.setattr(
        build_manager,
        "redeploy_plugin_if_needed",
        lambda b, result: calls.append(("plugin", b["id"], result)),
    )
    monkeypatch.setattr(
        build_manager,
        "restart_services_if_needed",
        lambda b, result: calls.append(("services", b["id"], result)),
    )

    build_manager.advance_builds()

    assert [(kind, build_id) for kind, build_id, _ in calls] == [
        ("plugin", build["id"]),
        ("services", build["id"]),
    ]
    # Both hooks see the same deploy result, including merged_repos --
    # what the plugin redeploy scopes its decision by.
    assert calls[0][2]["merged_repos"]["/project/src/ai-orchestrator-plugin"] == "def456"


def test_advance_builds_never_redeploys_the_plugin_after_a_failed_deploy(monkeypatch):
    build = build_manager.create_build("17H", "desc", "/tmp/proj")
    _force_status(build["id"], "DEPLOYING")

    monkeypatch.setattr(
        build_manager,
        "deploy_build",
        lambda b: {"deployed": False, "reason": "Merge conflict merging build-x"},
    )

    redeploy_calls = []
    monkeypatch.setattr(
        build_manager,
        "redeploy_plugin_if_needed",
        lambda b, result: redeploy_calls.append(b["id"]),
    )

    build_manager.advance_builds()

    assert build_manager.get_build(build["id"])["status"] == "FAILED"
    assert redeploy_calls == []


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
        build_manager, "delegate",
        lambda description, **kwargs: (_ for _ in ()).throw(
            AllProvidersFailed("claude: generation did not succeed; opencode: not available")
        ),
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


def test_rollback_deployment_stores_rollback_info_on_the_build(monkeypatch):
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
        lambda remediation_id: {
            "rollback": {
                "attempted": True,
                "available": True,
                "result": {"rolled_back_to": "previous production container"},
            }
        },
    )

    updated = build_manager.rollback_deployment(build["id"])

    assert updated["deployment"]["rollback"]["attempted"] is True
    assert updated["deployment"]["rollback"]["available"] is True
    assert updated["deployment"]["rollback"]["result"]["rolled_back_to"] == (
        "previous production container"
    )


def test_rollback_deployment_records_root_cause_in_build_history(monkeypatch):
    from core.build_learning import get_build_history

    build = build_manager.create_build("todo-app", "desc", "/tmp/proj", template="fastapi")
    _force_status(build["id"], "COMPLETED")

    builds = build_manager.load_builds()
    for b in builds:
        if b["id"] == build["id"]:
            b["deployment"] = {
                "deployed": True,
                "remediation_id": "r1",
                "rollback": {"attempted": True, "available": False},
            }
    build_manager.save_builds(builds)

    monkeypatch.setattr(
        build_manager,
        "attempt_rollback",
        lambda remediation_id: {
            "rollback": {"attempted": True, "available": False}
        },
    )

    build_manager.rollback_deployment(build["id"])

    history = get_build_history()
    assert len(history) == 1
    assert history[0]["status"] == "ROLLED_BACK"
    assert history[0]["template"] == "fastapi"
    assert "No rollback strategy" in history[0]["rollback_root_cause"]


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
    assert updated["status"] == "WAITING_FOR_USER_INPUT"
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


def test_generation_reporting_success_with_no_changes_is_treated_as_failure(monkeypatch):
    # opencode_bridge.run_coding_task (and equivalents) define success purely
    # as "process exited cleanly, no tool errors" -- a coding agent that
    # stops early (hits its own internal turn/step budget without ever
    # implementing anything) can exit 0 with that flag still set. Confirmed
    # live 2026-07-29: build 1b3875d7 (13U) reported success via
    # opencode_claude_sonnet with files_changed=[] and no commits, response
    # text mid-exploration. Every generation prompt explicitly instructs
    # "write the code, and commit your work with git as you go" -- a claimed
    # success with neither files_changed nor commits must not cascade to a
    # human-facing deploy approval for a no-op diff.
    build = build_manager.create_build("todo-app", "Build a todo app", "/tmp/proj")
    _force_status(build["id"], "GENERATING")

    monkeypatch.setattr(
        build_manager,
        "delegate",
        lambda description, **kwargs: {
            "provider": "opencode_claude_sonnet", "task_type": "coding", "duration_ms": 10,
            "response": {
                "success": True,
                "response_text": "Now let's check how this workspace relates to the project...",
                "files_changed": [],
                "commits": [],
                "tool_errors": [],
            },
        },
    )

    build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "FAILED"
    assert "no changes" in updated["failure_reason"].lower()


def test_generation_with_no_files_changed_but_a_real_commit_is_not_treated_as_no_op(monkeypatch):
    # A commit without any "write" tool_use events being parsed (e.g. a
    # commit amending/renaming existing files some other way) still counts
    # as real evidence of work -- only genuinely empty (no files AND no
    # commits) should be rejected.
    build = build_manager.create_build("todo-app", "Build a todo app", "/tmp/proj")
    _force_status(build["id"], "GENERATING")

    monkeypatch.setattr(
        build_manager,
        "delegate",
        lambda description, **kwargs: {
            "provider": "claude", "task_type": "coding", "duration_ms": 10,
            "response": {
                "success": True,
                "response_text": "Done.",
                "files_changed": [],
                "commits": [{"sha": "abc123", "message": "implement todo app"}],
                "tool_errors": [],
            },
        },
    )
    monkeypatch.setattr(
        build_manager, "run_all_scans",
        lambda project_path: {"scanners": {}, "total_findings": 0, "highest_severity": None},
    )

    build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "WAITING_FOR_DEPLOY_APPROVAL"


def test_run_generation_uses_the_longer_generation_timeout_not_the_planning_one(monkeypatch):
    # Generation involves real file writes/tool calls/tests and legitimately
    # takes longer than a quick text-only planning response -- confirmed
    # live tonight (13C's generation hit the 300s wall-clock ceiling while
    # still actively working on a genuinely larger module).
    build = build_manager.create_build("todo-app", "Build a todo app", "/tmp/proj")
    _force_status(build["id"], "GENERATING")

    captured = {}

    def fake_delegate(description, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return {
            "provider": "claude", "task_type": "coding", "duration_ms": 10,
            "response": {"success": True, "response_text": "Done.", "files_changed": ["app.py"], "commits": [], "tool_errors": []},
        }

    monkeypatch.setattr(build_manager, "delegate", fake_delegate)
    monkeypatch.setattr(build_manager, "run_all_scans", lambda project_path: {"scanners": {}, "total_findings": 0, "highest_severity": None})

    build_manager.advance_builds()

    assert captured["timeout"] == build_manager.GENERATION_TIMEOUT
    assert build_manager.GENERATION_TIMEOUT > build_manager.PLANNING_TIMEOUT


def test_advance_builds_drives_generating_to_deploy_approval_via_security_review(monkeypatch):
    build = build_manager.create_build("todo-app", "Build a todo app", "/tmp/proj")
    _force_status(build["id"], "GENERATING")

    monkeypatch.setattr(
        build_manager,
        "delegate",
        lambda description, **kwargs: {
            "provider": "claude", "task_type": "coding", "duration_ms": 10,
            "response": {
                "success": True,
                "response_text": "Done.",
                "files_changed": ["app/main.py"],
                "commits": [{"sha": "abc123", "message": "implement todo app"}],
                "tool_errors": [],
            },
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
    assert updated["status"] == "WAITING_FOR_DEPLOY_APPROVAL"
    assert updated["generation_result"]["commits"] == [{"sha": "abc123", "message": "implement todo app"}]
    assert updated["generated_by"] == "claude"
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
        "delegate",
        lambda description, **kwargs: {
            "provider": "claude", "task_type": "coding", "duration_ms": 10,
            "response": {"success": True, "response_text": "Done.", "files_changed": ["app.py"], "commits": [], "tool_errors": []},
        },
    )
    monkeypatch.setattr(
        build_manager,
        "run_all_scans",
        lambda project_path: {"scanners": {}, "total_findings": 5, "highest_severity": "critical"},
    )

    build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "WAITING_FOR_DEPLOY_APPROVAL"
    assert updated["security_report"]["highest_severity"] == "critical"


def test_advance_builds_drives_generating_to_failed_on_unsuccessful_run(monkeypatch):
    build = build_manager.create_build("todo-app", "Build a todo app", "/tmp/proj")
    _force_status(build["id"], "GENERATING")

    monkeypatch.setattr(
        build_manager,
        "delegate",
        lambda description, **kwargs: (_ for _ in ()).throw(
            AllProvidersFailed("claude: tests failed; opencode: not available")
        ),
    )

    build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "FAILED"


def test_advance_builds_leaves_terminal_state_builds_alone(monkeypatch):
    build = build_manager.create_build("todo-app", "desc", "/tmp/proj")
    _force_status(build["id"], "COMPLETED")

    monkeypatch.setattr(
        build_manager,
        "delegate",
        lambda *a, **k: pytest.fail("delegate should not be called for a COMPLETED build"),
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
    assert updated["status"] == "WAITING_FOR_ARCHITECTURE_APPROVAL"


# -- 13R: generation discipline preamble --------------------------------------

def test_generation_prompt_includes_discipline_preamble():
    build = build_manager.create_build("todo-app", "desc", "/tmp/proj")

    prompt = build_manager._generation_prompt(build)

    assert prompt.startswith(build_manager.GENERATION_DISCIPLINE_PREAMBLE)
    assert "write tests" in prompt
    assert "Do not claim something works without having run it" in prompt


# -- 13R: max_concurrent_builds configuration ---------------------------------

def test_max_concurrent_builds_read_from_providers_yaml(tmp_path, monkeypatch):
    config = tmp_path / "providers.yaml"
    config.write_text("max_concurrent_builds: 5\nproviders: {}\n")
    monkeypatch.setattr(build_manager, "PROVIDERS_CONFIG_PATH", config)

    assert build_manager._load_max_concurrent_builds() == 5


def test_max_concurrent_builds_defaults_when_config_has_no_key(tmp_path, monkeypatch):
    config = tmp_path / "providers.yaml"
    config.write_text("providers: {}\n")
    monkeypatch.setattr(build_manager, "PROVIDERS_CONFIG_PATH", config)

    assert build_manager._load_max_concurrent_builds() == build_manager.DEFAULT_MAX_CONCURRENT_BUILDS


def test_max_concurrent_builds_defaults_when_config_is_missing_or_bad(tmp_path, monkeypatch):
    monkeypatch.setattr(build_manager, "PROVIDERS_CONFIG_PATH", tmp_path / "nope.yaml")
    assert build_manager._load_max_concurrent_builds() == build_manager.DEFAULT_MAX_CONCURRENT_BUILDS

    bad = tmp_path / "bad.yaml"
    bad.write_text("max_concurrent_builds: -3\n")
    monkeypatch.setattr(build_manager, "PROVIDERS_CONFIG_PATH", bad)
    assert build_manager._load_max_concurrent_builds() == build_manager.DEFAULT_MAX_CONCURRENT_BUILDS


# -- 13R: concurrent dispatch --------------------------------------------------

def _stub_claude_reviewer(monkeypatch, findings="No issues found."):
    fake = {
        "available_fn": lambda: True,
        "run_text_task": lambda prompt, timeout=60, project_path=None: findings,
    }
    monkeypatch.setattr(
        build_manager.ai_provider, "get_provider",
        lambda name: fake if name == "claude" else None,
    )
    return fake


def test_advance_builds_runs_two_ready_builds_concurrently_without_cross_talk(monkeypatch, tmp_path):
    # Two builds in GENERATING at once must actually run in parallel (the
    # barrier only releases when both delegate calls are in flight
    # simultaneously) and each build's result must land on the right build.
    import threading

    build_a = build_manager.create_build("app-a", "desc", str(tmp_path / "a"))
    build_b = build_manager.create_build("app-b", "desc", str(tmp_path / "b"))
    _force_status(build_a["id"], "GENERATING")
    _force_status(build_b["id"], "GENERATING")

    barrier = threading.Barrier(2, timeout=10)

    def fake_delegate(description, **kwargs):
        barrier.wait()  # deadlocks (and breaks) unless both run at once
        provider = "claude" if "app-a" in description else "opencode"
        return {
            "provider": provider, "task_type": "coding", "duration_ms": 10,
            "response": {
                "success": True,
                "response_text": f"built by {provider}",
                "files_changed": ["app.py"], "commits": [], "tool_errors": [],
            },
        }

    monkeypatch.setattr(build_manager, "delegate", fake_delegate)
    monkeypatch.setattr(
        build_manager, "run_all_scans",
        lambda project_path: {"scanners": {}, "total_findings": 0, "highest_severity": None},
    )
    _stub_claude_reviewer(monkeypatch)

    build_manager.advance_builds()

    updated_a = build_manager.get_build(build_a["id"])
    updated_b = build_manager.get_build(build_b["id"])

    assert updated_a["status"] == "WAITING_FOR_DEPLOY_APPROVAL"
    assert updated_b["status"] == "WAITING_FOR_DEPLOY_APPROVAL"
    assert updated_a["generated_by"] == "claude"
    assert updated_b["generated_by"] == "opencode"
    assert updated_a["generation_result"]["response_text"] == "built by claude"
    assert updated_b["generation_result"]["response_text"] == "built by opencode"


def test_one_builds_crash_does_not_lose_the_other_builds_result(monkeypatch, tmp_path):
    # An unexpected exception outside the per-phase try blocks (here:
    # run_all_scans blowing up for one build) must fail only that build --
    # the other build's concurrently-computed result still gets persisted.
    build_a = build_manager.create_build("app-a", "desc", str(tmp_path / "a"))
    build_b = build_manager.create_build("app-b", "desc", str(tmp_path / "b"))
    _force_status(build_a["id"], "GENERATING")
    _force_status(build_b["id"], "GENERATING")

    monkeypatch.setattr(
        build_manager, "delegate",
        lambda description, **kwargs: {
            "provider": "claude", "task_type": "coding", "duration_ms": 10,
            "response": {"success": True, "response_text": "Done.", "files_changed": ["app.py"], "commits": [], "tool_errors": []},
        },
    )

    def scan(project_path):
        if project_path.endswith("/a"):
            raise RuntimeError("scanner exploded")
        return {"scanners": {}, "total_findings": 0, "highest_severity": None}

    monkeypatch.setattr(build_manager, "run_all_scans", scan)

    build_manager.advance_builds()

    updated_a = build_manager.get_build(build_a["id"])
    updated_b = build_manager.get_build(build_b["id"])

    assert updated_a["status"] == "FAILED"
    assert "scanner exploded" in updated_a["failure_reason"]
    assert updated_b["status"] == "WAITING_FOR_DEPLOY_APPROVAL"


def test_persist_build_updates_only_that_builds_record():
    build_a = build_manager.create_build("app-a", "desc", "/tmp/a")
    build_b = build_manager.create_build("app-b", "desc", "/tmp/b")

    build_a["status"] = "PLANNING"
    build_manager._persist_build(build_a)

    assert build_manager.get_build(build_a["id"])["status"] == "PLANNING"
    assert build_manager.get_build(build_b["id"])["status"] == "REQUESTED"
    assert len(build_manager.list_builds()) == 2


# -- 13R: CODE_REVIEW ----------------------------------------------------------

def test_code_review_is_skipped_for_claude_generated_builds(monkeypatch):
    build = build_manager.create_build("todo-app", "desc", "/tmp/proj")
    _force_status(build["id"], "GENERATING")

    monkeypatch.setattr(
        build_manager, "delegate",
        lambda description, **kwargs: {
            "provider": "claude", "task_type": "coding", "duration_ms": 10,
            "response": {"success": True, "response_text": "Done.", "files_changed": ["app.py"], "commits": [], "tool_errors": []},
        },
    )
    monkeypatch.setattr(
        build_manager, "run_all_scans",
        lambda project_path: {"scanners": {}, "total_findings": 0, "highest_severity": None},
    )
    monkeypatch.setattr(
        build_manager.ai_provider, "get_provider",
        lambda name: pytest.fail("claude must not be asked to review its own work"),
    )

    build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "WAITING_FOR_DEPLOY_APPROVAL"
    assert updated["code_review"] == {"skipped": True, "reason": "generated by claude"}


def test_code_review_attaches_claude_findings_for_non_claude_builds(monkeypatch, tmp_path):
    build = build_manager.create_build("todo-app", "desc", str(tmp_path / "proj"))
    _force_status(build["id"], "GENERATING")

    monkeypatch.setattr(
        build_manager, "delegate",
        lambda description, **kwargs: {
            "provider": "opencode", "task_type": "coding", "duration_ms": 10,
            "response": {
                "success": True, "response_text": "Done.",
                "files_changed": ["app/main.py"],
                "commits": [{"sha": "abc123", "message": "implement"}],
                "tool_errors": [],
            },
        },
    )
    monkeypatch.setattr(
        build_manager, "run_all_scans",
        lambda project_path: {"scanners": {}, "total_findings": 0, "highest_severity": None},
    )
    _stub_claude_reviewer(monkeypatch, findings="Advisory: app/main.py has no tests.")

    build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "WAITING_FOR_DEPLOY_APPROVAL"
    assert updated["code_review"] == {
        "skipped": False,
        "reviewer": "claude",
        "findings": "Advisory: app/main.py has no tests.",
    }


def test_code_review_findings_never_block_the_build(monkeypatch, tmp_path):
    # Even scathing findings are advisory only -- the build still reaches
    # the human deploy-approval gate; nothing auto-fails it.
    build = build_manager.create_build("todo-app", "desc", str(tmp_path / "proj"))
    _force_status(build["id"], "GENERATING")

    monkeypatch.setattr(
        build_manager, "delegate",
        lambda description, **kwargs: {
            "provider": "opencode", "task_type": "coding", "duration_ms": 10,
            "response": {"success": True, "response_text": "Done.", "files_changed": ["app.py"], "commits": [], "tool_errors": []},
        },
    )
    monkeypatch.setattr(
        build_manager, "run_all_scans",
        lambda project_path: {"scanners": {}, "total_findings": 0, "highest_severity": None},
    )
    _stub_claude_reviewer(monkeypatch, findings="CRITICAL: this code is broken and must not ship.")

    build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "WAITING_FOR_DEPLOY_APPROVAL"
    assert "must not ship" in updated["code_review"]["findings"]


def test_code_review_skips_gracefully_when_claude_is_unavailable(monkeypatch, tmp_path):
    build = build_manager.create_build("todo-app", "desc", str(tmp_path / "proj"))
    _force_status(build["id"], "GENERATING")

    monkeypatch.setattr(
        build_manager, "delegate",
        lambda description, **kwargs: {
            "provider": "opencode", "task_type": "coding", "duration_ms": 10,
            "response": {"success": True, "response_text": "Done.", "files_changed": ["app.py"], "commits": [], "tool_errors": []},
        },
    )
    monkeypatch.setattr(
        build_manager, "run_all_scans",
        lambda project_path: {"scanners": {}, "total_findings": 0, "highest_severity": None},
    )
    monkeypatch.setattr(
        build_manager.ai_provider, "get_provider",
        lambda name: {"available_fn": lambda: False, "run_text_task": None},
    )

    build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "WAITING_FOR_DEPLOY_APPROVAL"
    assert updated["code_review"] == {"skipped": True, "reason": "claude unavailable"}


def test_code_review_skips_gracefully_when_the_claude_call_fails(monkeypatch, tmp_path):
    build = build_manager.create_build("todo-app", "desc", str(tmp_path / "proj"))
    _force_status(build["id"], "GENERATING")

    monkeypatch.setattr(
        build_manager, "delegate",
        lambda description, **kwargs: {
            "provider": "opencode", "task_type": "coding", "duration_ms": 10,
            "response": {"success": True, "response_text": "Done.", "files_changed": ["app.py"], "commits": [], "tool_errors": []},
        },
    )
    monkeypatch.setattr(
        build_manager, "run_all_scans",
        lambda project_path: {"scanners": {}, "total_findings": 0, "highest_severity": None},
    )

    def boom(prompt, timeout=60, project_path=None):
        raise RuntimeError("Claude usage limit reached")

    monkeypatch.setattr(
        build_manager.ai_provider, "get_provider",
        lambda name: {"available_fn": lambda: True, "run_text_task": boom},
    )

    build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "WAITING_FOR_DEPLOY_APPROVAL"
    assert updated["code_review"]["skipped"] is True
    assert "usage limit" in updated["code_review"]["reason"]


def test_advance_builds_picks_up_a_build_stranded_in_code_review(monkeypatch):
    # Defensive fallback: the normal path cascades through CODE_REVIEW
    # inside _run_generation, but a build persisted mid-cascade (e.g. after
    # a crash) must still be driven forward by the dispatch loop.
    build = build_manager.create_build("todo-app", "desc", "/tmp/proj")
    _force_status(build["id"], "CODE_REVIEW")

    builds = build_manager.load_builds()
    for b in builds:
        if b["id"] == build["id"]:
            b["generated_by"] = "claude"
    build_manager.save_builds(builds)

    monkeypatch.setattr(
        build_manager, "run_all_scans",
        lambda project_path: {"scanners": {}, "total_findings": 0, "highest_severity": None},
    )

    build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "WAITING_FOR_DEPLOY_APPROVAL"
    assert updated["code_review"]["skipped"] is True


def test_deploy_approval_surfaces_code_review_findings(monkeypatch, tmp_path):
    from core.approval import load_requests

    build = build_manager.create_build("todo-app", "desc", str(tmp_path / "proj"))
    _force_status(build["id"], "GENERATING")

    monkeypatch.setattr(
        build_manager, "delegate",
        lambda description, **kwargs: {
            "provider": "opencode", "task_type": "coding", "duration_ms": 10,
            "response": {"success": True, "response_text": "Done.", "files_changed": ["app.py"], "commits": [], "tool_errors": []},
        },
    )
    monkeypatch.setattr(
        build_manager, "run_all_scans",
        lambda project_path: {"scanners": {}, "total_findings": 1, "highest_severity": "low"},
    )
    _stub_claude_reviewer(monkeypatch, findings="Advisory: missing input validation.")

    build_manager.advance_builds()

    approvals = [r for r in load_requests() if r.get("build_id") == build["id"]]
    assert len(approvals) == 1
    assert "1 security finding(s) found." in approvals[0]["description"]
    assert "missing input validation" in approvals[0]["description"]


def test_code_review_prompt_is_read_only_and_includes_generation_context():
    build = build_manager.create_build("todo-app", "desc", "/tmp/proj")
    build["generated_by"] = "opencode"
    build["generation_result"] = {
        "response_text": "Implemented the app.",
        "files_changed": ["app/main.py"],
        "commits": [{"sha": "abc123", "message": "implement todo app"}],
    }

    prompt = build_manager._code_review_prompt(build)

    assert "Do NOT" in prompt
    assert "app/main.py" in prompt
    assert "abc123" in prompt
    assert "advisory" in prompt.lower()


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
    assert build["status"] == "WAITING_FOR_ARCHITECTURE_APPROVAL"

    build = build_manager.approve_architecture(build["id"], operator="cloudcli-plugin")
    assert build["status"] == "ARCHITECTURE_APPROVED"

    build = build_manager.start_generation(build["id"])
    assert build["status"] == "GENERATING"

    monkeypatch.setattr(
        build_manager,
        "delegate",
        lambda description, **kwargs: {
            "provider": "claude", "task_type": "coding", "duration_ms": 10,
            "response": {
                "success": True,
                "response_text": "Implemented.",
                "files_changed": ["app/main.py"],
                "commits": [{"sha": "def456", "message": "implement todo app"}],
                "tool_errors": [],
            },
        },
    )
    monkeypatch.setattr(
        build_manager,
        "run_all_scans",
        lambda project_path: {"scanners": {}, "total_findings": 0, "highest_severity": None},
    )
    build_manager.advance_builds()
    build = build_manager.get_build(build["id"])
    assert build["status"] == "WAITING_FOR_DEPLOY_APPROVAL"
    assert build["security_report"]["total_findings"] == 0


# 13Y: _plan_needs_clarification's bare "'?' in plan_text" false-positived
# live on 13P's own plan twice (2026-07-29) -- once on a rhetorical closing
# sign-off, and again on a self-referential plan (about this exact bug) that
# quoted '?' characters as illustrative examples. These fixtures are the
# actual incident text (see roadmap phase 13Y).

_13P_DECISION_POINTS_PLAN = """Here is the proposed architecture and implementation plan for phase 13P.

#### Decision Points (if applicable)
- Interval Configuration: Confirm whether the fixed interval for the heartbeat should be set to 30, 45, or 60 seconds. A lower value could reduce the risk of being flagged as hung, but it would come at the cost of slightly more frequent notifications.
- Threading Limitations: Discuss potential issues or limitations of using threads in the current scheduling environment. Are there concerns about thread safety or shared resources that we need to account for?

By following this architecture plan, we will be able to address the identified bug effectively."""

_13P_SIGNOFF_PLAN = """Here is the proposed architecture plan.

### Questions / Clarifications Needed?
Everything specified in the prompt and prior clarifications is clear and actionable.

Are there any objections or final check you would like to make on this plan before proceeding to code modification?"""

_13Y_SELF_REFERENTIAL_PLAN = """Plan discusses core.build_manager._plan_needs_clarification(plan_text) uses '?' in plan_text to detect open questions.

Example genuine question: Should we use database A or database B for persistence?
Example rhetorical: Are there any concerns...?

### 4. Questions / Clarifications for the Requester
Everything specified in the roadmap phase and prior clarifications is clear and actionable. Are there any objections or additional edge cases you would like considered before coding begins?"""

# 17A hit this live 2026-08-01, six planning cycles in a row: "specific" and
# "before we proceed to implementation" weren't in the existing pattern set
# ("other "/"additional " edge cases, "before implementation"/"before
# proceeding" as separate phrases), so this exact rhetorical sign-off kept
# getting treated as a genuine blocking question every single cycle.
_17A_SPECIFIC_EDGE_CASES_PLAN = """Here is the proposed architecture plan for phase 17A.

### Questions / Clarifications for the Requester
Everything needed is fully specified. Are there any specific edge cases or additional constraints you would like addressed before we proceed to implementation?"""


@pytest.mark.parametrize("plan_text", [
    _13P_DECISION_POINTS_PLAN,
    _13P_SIGNOFF_PLAN,
    _13Y_SELF_REFERENTIAL_PLAN,
    _17A_SPECIFIC_EDGE_CASES_PLAN,
])
def test_plan_needs_clarification_false_positives_from_13p_and_13y_incidents_are_fixed(plan_text):
    assert build_manager._plan_needs_clarification(plan_text) is False
    assert build_manager._extract_pending_question(plan_text) is None


def test_plan_needs_clarification_still_catches_a_genuine_open_question():
    plan_text = "Architecture: use FastAPI. Should we use database A or database B for persistence?"

    assert build_manager._plan_needs_clarification(plan_text) is True
    assert "database A or database B" in build_manager._extract_pending_question(plan_text)


def test_plan_needs_clarification_still_catches_a_genuine_question_with_no_heading():
    plan_text = "Plan: use FastAPI + React. Any preference on database?"

    assert build_manager._plan_needs_clarification(plan_text) is True
    assert build_manager._extract_pending_question(plan_text) == "Any preference on database?"


def test_advance_builds_proceeds_to_architecture_approval_when_plan_only_has_a_signoff_question(monkeypatch):
    build = build_manager.create_build("todo-app", "Build a todo app", "/tmp/proj")
    _force_status(build["id"], "PLANNING")

    monkeypatch.setattr(
        build_manager,
        "delegate",
        lambda description, **kwargs: {
            "provider": "gemini", "task_type": "planning",
            "response": _13P_SIGNOFF_PLAN,
            "duration_ms": 10,
        },
    )

    build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "WAITING_FOR_ARCHITECTURE_APPROVAL"
    assert updated.get("pending_question") is None


def test_advance_builds_populates_pending_question_for_a_genuine_open_question(monkeypatch):
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

    build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "WAITING_FOR_USER_INPUT"
    assert updated["pending_question"] == "Any preference on database?"


def test_submit_answer_clears_pending_question():
    build = build_manager.create_build("todo-app", "desc", "/tmp/proj")
    _force_status(build["id"], "WAITING_FOR_USER_INPUT")

    builds = build_manager.load_builds()
    for b in builds:
        if b["id"] == build["id"]:
            b["pending_question"] = "Any preference on database?"
    build_manager.save_builds(builds)

    updated = build_manager.submit_answer(build["id"], "SQLite")

    assert updated["pending_question"] is None
    assert updated["status"] == "PLANNING"


# -- 13S: plan validation (tool-call leak / empty-plan detection) -----------

_MINIMAX_TOOL_CALL_LEAK = """<minimax:tool_call>
{"name": "bash", "arguments": {"command": "echo hello"}}
</minimax:tool_call>"""

_OPENROUTER_TOOL_CALL_LEAK = """The plan is:
<openrouter:tool_call id="123">{"action": "read_file"}</openrouter:tool_call>
then proceed with implementation."""

_JSON_TOOL_CALL_LEAK = """Here is the plan:
{"tool_calls": [{"type": "function", "name": "create_file"}]}
This is the architecture."""

_VALID_PROSE_PLAN = "Architecture: use FastAPI + React with a PostgreSQL database."

_EMPTY_STRING = ""

_WHITESPACE_ONLY = "   \n\n   "

_VERY_SHORT = "OK."


class TestLooksLikeToolCallLeak:
    def test_tool_call_markup_tag_rejected(self):
        assert build_manager._looks_like_tool_call_leak(_MINIMAX_TOOL_CALL_LEAK) is True

    def test_tool_call_markup_tag_with_namespace_rejected(self):
        assert build_manager._looks_like_tool_call_leak(_OPENROUTER_TOOL_CALL_LEAK) is True

    def test_json_tool_call_shape_rejected(self):
        assert build_manager._looks_like_tool_call_leak(_JSON_TOOL_CALL_LEAK) is True

    def test_empty_text_rejected(self):
        assert build_manager._looks_like_tool_call_leak(_EMPTY_STRING) is True

    def test_none_text_rejected(self):
        assert build_manager._looks_like_tool_call_leak(None) is True

    def test_whitespace_only_rejected(self):
        assert build_manager._looks_like_tool_call_leak(_WHITESPACE_ONLY) is True

    def test_very_short_text_rejected(self):
        assert build_manager._looks_like_tool_call_leak(_VERY_SHORT) is True

    def test_normal_prose_plan_passes(self):
        assert build_manager._looks_like_tool_call_leak(_VALID_PROSE_PLAN) is False

    def test_plan_with_question_passes(self):
        assert build_manager._looks_like_tool_call_leak(
            "Plan: use FastAPI + React. Any preference on database?"
        ) is False

    def test_minimax_closing_tag_is_matched(self):
        # closing tag </minimax:tool_call> should also be caught
        assert build_manager._looks_like_tool_call_leak(
            "The plan for the project.</minimax:tool_call>"
        ) is True

    def test_bare_bash_tag_leak_rejected(self):
        # Confirmed live 2026-07-30 (13V, planned_by=deepseek): a plain
        # text_task call with no real tool access hallucinated <bash>...
        # </bash> blocks with unexecuted shell commands as its entire
        # "plan" -- no provider-prefixed :tool_call suffix at all, so the
        # original colon-form pattern missed it.
        assert build_manager._looks_like_tool_call_leak(
            "I'll analyze the codebase first.\n<bash>\nfind / -name '*.py'\n</bash>"
        ) is True

    def test_other_generic_tool_tags_rejected(self):
        for tag in ("tool_use", "invoke", "function_calls", "execute", "shell"):
            assert build_manager._looks_like_tool_call_leak(
                f"Let me check.\n<{tag}>do a thing</{tag}>"
            ) is True, tag

    def test_prose_mentioning_bash_without_tags_still_passes(self):
        # The word "bash" alone in normal prose (discussing a bash script
        # as part of the actual plan) must not be flagged -- only the tag
        # markup itself is the signal.
        assert build_manager._looks_like_tool_call_leak(
            "Architecture: a bash deployment script runs migrations before the app starts."
        ) is False

    def test_fenced_bash_block_leak_rejected(self):
        # Confirmed live 2026-07-30 (17B retry, planned_by=deepseek): a
        # text_task call with no real tool access produced "I'll explore
        # the repositories ... ```bash\npwd && ls -la\n```" as its entire
        # "plan" -- a markdown-fenced shell block, not the XML-tag syntax
        # the earlier <bash> incident used, so the original pattern missed it.
        assert build_manager._looks_like_tool_call_leak(
            "I'll explore the repositories first.\n```bash\npwd && ls -la\n```"
        ) is True

    def test_other_fenced_shell_languages_rejected(self):
        for lang in ("sh", "shell", "zsh", "console"):
            assert build_manager._looks_like_tool_call_leak(
                f"Let me check.\n```{lang}\nls -la\n```"
            ) is True, lang

    def test_fenced_python_block_in_real_plan_still_passes(self):
        # A legitimate plan may include an illustrative code snippet
        # describing a design -- only shell-family fences (commands
        # intended to be executed) are the hallucination signal, not any
        # fenced code block at all.
        assert build_manager._looks_like_tool_call_leak(
            "Architecture: add a new provider entry.\n"
            "```python\n"
            "\"openrouter_claude\": {\"model\": \"anthropic/claude-sonnet-4.6\"}\n"
            "```\n"
            "This wires into the existing ROLE_PROVIDERS chain."
        ) is False

    def test_unknown_tool_tag_name_rejected(self):
        # Confirmed live 2026-07-30 (13M, planned_by=deepseek): a text_task
        # call with no real tool access produced
        # "<read_file><path>core/ai_provider.py</path></read_file>" as its
        # entire "plan" -- a third distinct tag name in one session (after
        # <bash> and a fenced ```bash block), never enumerated in any
        # tag-name list. The general non-HTML-tag rule catches it without
        # needing to know "read_file" or "path" specifically in advance.
        assert build_manager._looks_like_tool_call_leak(
            "I'll start by reading the design document.\n"
            "<read_file>\n<path>core/ai_provider.py</path>\n</read_file>"
        ) is True

    def test_common_html_tags_in_frontend_plan_still_pass(self):
        # A legitimate frontend-touching plan may reasonably mention real
        # HTML elements inline -- only non-HTML bare tags are the
        # hallucination signal.
        assert build_manager._looks_like_tool_call_leak(
            "Architecture: add a <button> that POSTs the <input> field's "
            "value to /kai/chat, appending the response to a <div> "
            "containing the scrolling message history."
        ) is False


def test_bad_plan_stays_in_planning_and_does_not_reach_approval(monkeypatch):
    build = build_manager.create_build("todo-app", "Build a todo app", "/tmp/proj")
    _force_status(build["id"], "PLANNING")

    monkeypatch.setattr(
        build_manager,
        "delegate",
        lambda description, **kwargs: {
            "provider": "minimax", "task_type": "planning",
            "response": _MINIMAX_TOOL_CALL_LEAK,
            "duration_ms": 10,
        },
    )

    build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "PLANNING"
    assert updated["planned_by"] == "minimax"
    assert updated.get("_consecutive_planning_rejections") == 1


def test_empty_plan_stays_in_planning_and_does_not_reach_approval(monkeypatch):
    build = build_manager.create_build("todo-app", "Build a todo app", "/tmp/proj")
    _force_status(build["id"], "PLANNING")

    monkeypatch.setattr(
        build_manager,
        "delegate",
        lambda description, **kwargs: {
            "provider": "gemini", "task_type": "planning",
            "response": "",
            "duration_ms": 10,
        },
    )

    build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "PLANNING"
    assert updated.get("_consecutive_planning_rejections") == 1


def test_consecutive_bad_plans_increment_counter(monkeypatch):
    build = build_manager.create_build("todo-app", "Build a todo app", "/tmp/proj")
    _force_status(build["id"], "PLANNING")

    monkeypatch.setattr(
        build_manager,
        "delegate",
        lambda description, **kwargs: {
            "provider": "minimax", "task_type": "planning",
            "response": _MINIMAX_TOOL_CALL_LEAK,
            "duration_ms": 10,
        },
    )

    build_manager.advance_builds()
    assert build_manager.get_build(build["id"])["_consecutive_planning_rejections"] == 1

    build_manager.advance_builds()
    assert build_manager.get_build(build["id"])["_consecutive_planning_rejections"] == 2


def test_three_consecutive_bad_plans_fail_the_build(monkeypatch):
    build = build_manager.create_build("todo-app", "Build a todo app", "/tmp/proj")
    _force_status(build["id"], "PLANNING")

    monkeypatch.setattr(
        build_manager,
        "delegate",
        lambda description, **kwargs: {
            "provider": "minimax", "task_type": "planning",
            "response": _MINIMAX_TOOL_CALL_LEAK,
            "duration_ms": 10,
        },
    )

    build_manager.advance_builds()
    assert build_manager.get_build(build["id"])["status"] == "PLANNING"

    build_manager.advance_builds()
    assert build_manager.get_build(build["id"])["status"] == "PLANNING"

    build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "FAILED"
    assert "3 consecutive unusable planning responses" in updated["failure_reason"]


def test_counter_resets_after_a_valid_prose_plan(monkeypatch):
    build = build_manager.create_build("todo-app", "Build a todo app", "/tmp/proj")
    _force_status(build["id"], "PLANNING")

    call_count = [0]

    def alternating_delegate(description, **kwargs):
        call_count[0] += 1
        if call_count[0] <= 2:
            return {
                "provider": "minimax", "task_type": "planning",
                "response": _MINIMAX_TOOL_CALL_LEAK,
                "duration_ms": 10,
            }
        return {
            "provider": "gemini", "task_type": "planning",
            "response": _VALID_PROSE_PLAN,
            "duration_ms": 10,
        }

    monkeypatch.setattr(build_manager, "delegate", alternating_delegate)

    build_manager.advance_builds()
    assert build_manager.get_build(build["id"])["_consecutive_planning_rejections"] == 1

    build_manager.advance_builds()
    assert build_manager.get_build(build["id"])["_consecutive_planning_rejections"] == 2

    build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "WAITING_FOR_ARCHITECTURE_APPROVAL"
    assert updated["_consecutive_planning_rejections"] == 0


def test_clarifying_question_still_works_after_prior_rejections(monkeypatch):
    build = build_manager.create_build("todo-app", "Build a todo app", "/tmp/proj")
    _force_status(build["id"], "PLANNING")

    call_count = [0]

    def delegate_sequence(description, **kwargs):
        call_count[0] += 1
        if call_count[0] <= 1:
            return {
                "provider": "minimax", "task_type": "planning",
                "response": _MINIMAX_TOOL_CALL_LEAK,
                "duration_ms": 10,
            }
        return {
            "provider": "gemini", "task_type": "planning",
            "response": "Plan: use FastAPI + React. Any preference on database?",
            "duration_ms": 10,
        }

    monkeypatch.setattr(build_manager, "delegate", delegate_sequence)

    build_manager.advance_builds()
    assert build_manager.get_build(build["id"])["_consecutive_planning_rejections"] == 1

    build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "WAITING_FOR_USER_INPUT"
    assert updated["pending_question"] == "Any preference on database?"
    assert updated["_consecutive_planning_rejections"] == 0


def test_plan_with_json_tool_call_shape_is_rejected_and_retries(monkeypatch):
    build = build_manager.create_build("todo-app", "Build a todo app", "/tmp/proj")
    _force_status(build["id"], "PLANNING")

    monkeypatch.setattr(
        build_manager,
        "delegate",
        lambda description, **kwargs: {
            "provider": "openrouter", "task_type": "planning",
            "response": _JSON_TOOL_CALL_LEAK,
            "duration_ms": 10,
        },
    )

    build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "PLANNING"
    assert updated.get("_consecutive_planning_rejections") == 1


def test_normal_prose_plan_proceeds_to_architecture_approval_via_new_gate(monkeypatch):
    build = build_manager.create_build("todo-app", "Build a todo app", "/tmp/proj")
    _force_status(build["id"], "PLANNING")

    monkeypatch.setattr(
        build_manager,
        "delegate",
        lambda description, **kwargs: {
            "provider": "gemini", "task_type": "planning",
            "response": _VALID_PROSE_PLAN,
            "duration_ms": 10,
        },
    )

    build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "WAITING_FOR_ARCHITECTURE_APPROVAL"
    assert updated["_consecutive_planning_rejections"] == 0


def test_advance_from_requested_with_bad_plan_retries_via_planning_path(monkeypatch):
    build = build_manager.create_build("todo-app", "Build a todo app", "/tmp/proj")
    assert build["status"] == "REQUESTED"

    call_count = [0]

    def delegate_sequence(description, **kwargs):
        call_count[0] += 1
        if call_count[0] <= 1:
            return {
                "provider": "minimax", "task_type": "planning",
                "response": _MINIMAX_TOOL_CALL_LEAK,
                "duration_ms": 10,
            }
        return {
            "provider": "gemini", "task_type": "planning",
            "response": _VALID_PROSE_PLAN,
            "duration_ms": 10,
        }

    monkeypatch.setattr(build_manager, "delegate", delegate_sequence)

    build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "PLANNING"
    assert updated["_consecutive_planning_rejections"] == 1

    build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "WAITING_FOR_ARCHITECTURE_APPROVAL"
    assert updated["_consecutive_planning_rejections"] == 0


def test_plan_needs_clarification_is_preserved_with_tool_call_leak_rejected():
    # A plan with both a '?' AND tool-call markup should be caught by
    # _looks_like_tool_call_leak first (the leak check comes before the
    # question check in _run_planning), so it never reaches
    # _plan_needs_clarification.  Regardless, _plan_needs_clarification
    # behavior on normal plans is unchanged.
    assert build_manager._plan_needs_clarification(_VALID_PROSE_PLAN) is False
    assert build_manager._plan_needs_clarification(
        "Plan: use FastAPI + React. Any preference on database?"
    ) is True
