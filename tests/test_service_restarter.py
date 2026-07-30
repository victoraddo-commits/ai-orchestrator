"""17C: after a successful self-modifying merge, the live services that
imported the changed code must actually be restarted -- Python never
hot-reloads, so without this every such deploy silently serves stale code
(confirmed live 2026-07-30: 17B's GET /kai/chat 404'd until a manual
restart despite the deploy reporting COMPLETED/VERIFIED)."""

import subprocess

import core.service_restarter as restarter


def _proc(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _capture_systemctl(monkeypatch, returncode=0, stderr=""):
    calls = []

    def fake_systemctl(*args, timeout=60):
        calls.append(args)
        return _proc(returncode=returncode, stderr=stderr)

    monkeypatch.setattr(restarter, "_systemctl", fake_systemctl)
    return calls


def _merged_deploy(changed_files):
    return {
        "deployed": True,
        "merged_branch": "build-b1",
        "merge_commit": "abc123",
        "changed_files": changed_files,
    }


_BUILD = {"id": "b1", "name": "17C"}


# --- the three completion-criteria scenarios -------------------------------


def test_successful_merge_touching_core_api_restarts_the_api_service(monkeypatch):
    calls = _capture_systemctl(monkeypatch)
    result = _merged_deploy(["core/api.py"])

    records = restarter.restart_services_if_needed(_BUILD, result)

    assert calls == [("restart", restarter.API_SERVICE)]
    assert records == [{"service": restarter.API_SERVICE, "restarted": True, "queued": False}]
    # Observability: the restart outcome is recorded on the deploy result.
    assert result["service_restarts"] == records


def test_roadmap_only_change_does_not_restart_anything(monkeypatch):
    calls = _capture_systemctl(monkeypatch)
    result = _merged_deploy(["roadmap.json"])

    records = restarter.restart_services_if_needed(_BUILD, result)

    assert calls == []
    assert records == []
    assert result["service_restarts"] == []


def test_failed_merge_never_restarts(monkeypatch):
    calls = _capture_systemctl(monkeypatch)
    failed = {"deployed": False, "reason": "Merge conflict merging build-b1"}

    records = restarter.restart_services_if_needed(_BUILD, failed)

    assert calls == []
    assert records == []
    assert "service_restarts" not in failed


# --- scoping: restart only the service(s) that imported the change ---------


def test_docs_and_tests_only_change_does_not_restart(monkeypatch):
    calls = _capture_systemctl(monkeypatch)

    records = restarter.restart_services_if_needed(
        _BUILD, _merged_deploy(["README.md", "docs/notes.md", "tests/test_api.py"])
    )

    assert calls == []
    assert records == []


def test_shared_core_module_restarts_both_services_api_first(monkeypatch):
    calls = _capture_systemctl(monkeypatch)

    records = restarter.restart_services_if_needed(
        _BUILD, _merged_deploy(["core/build_manager.py"])
    )

    # API first (blocking), then the scheduler daemon -- our own process --
    # last and queued via --no-block so we don't deadlock awaiting our own
    # death.
    assert calls == [
        ("restart", restarter.API_SERVICE),
        ("restart", "--no-block", restarter.SCHEDULER_SERVICE),
    ]
    assert [r["service"] for r in records] == [restarter.API_SERVICE, restarter.SCHEDULER_SERVICE]
    assert all(r["restarted"] for r in records)


def test_scheduler_only_module_restarts_only_the_scheduler(monkeypatch):
    calls = _capture_systemctl(monkeypatch)

    restarter.restart_services_if_needed(
        _BUILD, _merged_deploy(["core/orchestrator_cycle.py"])
    )

    assert calls == [("restart", "--no-block", restarter.SCHEDULER_SERVICE)]


def test_api_only_kai_module_restarts_only_the_api_service(monkeypatch):
    calls = _capture_systemctl(monkeypatch)

    restarter.restart_services_if_needed(
        _BUILD, _merged_deploy(["core/kai/commands.py"])
    )

    assert calls == [("restart", restarter.API_SERVICE)]


def test_unknown_changed_files_conservatively_restarts_both(monkeypatch):
    # git couldn't tell us what changed -- stale live code is a silent
    # failure, an extra restart is a blip: restart both.
    calls = _capture_systemctl(monkeypatch)

    restarter.restart_services_if_needed(_BUILD, _merged_deploy(None))

    assert calls == [
        ("restart", restarter.API_SERVICE),
        ("restart", "--no-block", restarter.SCHEDULER_SERVICE),
    ]


def test_container_deploy_is_never_restarted(monkeypatch):
    # A regular app deploy (no merged_branch) doesn't touch orchestrator
    # code -- restarting the orchestrator for it would drop in-flight
    # requests for nothing.
    calls = _capture_systemctl(monkeypatch)

    records = restarter.restart_services_if_needed(
        {"id": "b2", "name": "todo-app"},
        {"deployed": True, "container": "aiapp-todo-app", "port": 32768},
    )

    assert calls == []
    assert records == []


def test_mixed_runtime_and_bookkeeping_change_restarts_for_the_runtime_part(monkeypatch):
    # The common real deploy: code + roadmap.json bookkeeping in one merge.
    calls = _capture_systemctl(monkeypatch)

    restarter.restart_services_if_needed(
        _BUILD, _merged_deploy(["core/api.py", "roadmap.json", "tests/test_api.py"])
    )

    assert calls == [("restart", restarter.API_SERVICE)]


def test_requirements_change_restarts_both(monkeypatch):
    calls = _capture_systemctl(monkeypatch)

    restarter.restart_services_if_needed(_BUILD, _merged_deploy(["requirements.txt"]))

    assert calls == [
        ("restart", restarter.API_SERVICE),
        ("restart", "--no-block", restarter.SCHEDULER_SERVICE),
    ]


# --- failure handling -------------------------------------------------------


def test_systemctl_failure_is_recorded_not_raised(monkeypatch):
    _capture_systemctl(monkeypatch, returncode=1, stderr="Failed to restart: access denied")
    result = _merged_deploy(["core/api.py"])

    records = restarter.restart_services_if_needed(_BUILD, result)

    assert records == [
        {
            "service": restarter.API_SERVICE,
            "restarted": False,
            "error": "Failed to restart: access denied",
        }
    ]
    assert result["service_restarts"] == records


def test_systemctl_exception_is_recorded_not_raised(monkeypatch):
    def boom(*args, timeout=60):
        raise FileNotFoundError("systemctl not found")

    monkeypatch.setattr(restarter, "_systemctl", boom)

    records = restarter.restart_services_if_needed(_BUILD, _merged_deploy(["core/api.py"]))

    assert records[0]["restarted"] is False
    assert "systemctl not found" in records[0]["error"]
