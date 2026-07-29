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
    assert updated["status"] == "WAITING_FOR_ARCHITECTURE_APPROVAL"


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
            "response": {"success": True, "response_text": "Done.", "files_changed": [], "commits": [], "tool_errors": []},
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
            "response": {"success": True, "response_text": "Done.", "files_changed": [], "commits": [], "tool_errors": []},
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
                "files_changed": [], "commits": [], "tool_errors": [],
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
            "response": {"success": True, "response_text": "Done.", "files_changed": [], "commits": [], "tool_errors": []},
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
            "response": {"success": True, "response_text": "Done.", "files_changed": [], "commits": [], "tool_errors": []},
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
            "response": {"success": True, "response_text": "Done.", "files_changed": [], "commits": [], "tool_errors": []},
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
            "response": {"success": True, "response_text": "Done.", "files_changed": [], "commits": [], "tool_errors": []},
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
            "response": {"success": True, "response_text": "Done.", "files_changed": [], "commits": [], "tool_errors": []},
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
            "response": {"success": True, "response_text": "Done.", "files_changed": [], "commits": [], "tool_errors": []},
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
