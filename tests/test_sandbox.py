import subprocess

import pytest

import core.sandbox as sandbox


def test_docker_run_args_mounts_project_path_read_write():
    args = sandbox._docker_run_args("/proj", "npm test", image="node:22-bookworm")

    assert "-v" in args
    assert f"{__import__('os').path.abspath('/proj')}:/workspace:rw" in args


def test_docker_run_args_defaults_to_network_enabled_for_installs():
    args = sandbox._docker_run_args("/proj", "npm install")

    idx = args.index("--network")
    assert args[idx + 1] == "bridge"


def test_docker_run_args_can_disable_network():
    args = sandbox._docker_run_args("/proj", "pytest", network=False)

    idx = args.index("--network")
    assert args[idx + 1] == "none"


def test_docker_run_args_applies_resource_limits():
    args = sandbox._docker_run_args("/proj", "pytest", memory="256m", cpus="0.5")

    assert "--memory" in args
    assert args[args.index("--memory") + 1] == "256m"
    assert "--cpus" in args
    assert args[args.index("--cpus") + 1] == "0.5"
    assert "--pids-limit" in args


def test_docker_run_args_is_disposable_and_read_only_root():
    args = sandbox._docker_run_args("/proj", "pytest")

    assert "--rm" in args
    assert "--read-only" in args
    assert "--tmpfs" in args


def test_docker_run_args_uses_apapmor_unconfined_per_lxc_convention():
    args = sandbox._docker_run_args("/proj", "pytest")

    idx = args.index("--security-opt")
    assert args[idx + 1] == "apparmor=unconfined"


def test_docker_run_args_runs_command_via_sh_lc():
    args = sandbox._docker_run_args("/proj", "npm test", image="node:22-bookworm")

    assert args[-4:] == ["node:22-bookworm", "sh", "-lc", "npm test"]


def test_run_in_sandbox_returns_structured_result_on_success(monkeypatch):
    monkeypatch.setattr(sandbox, "sandbox_available", lambda: True)

    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok\n", stderr="")
    monkeypatch.setattr(sandbox.subprocess, "run", lambda *a, **k: completed)

    result = sandbox.run_in_sandbox("/proj", "echo ok")

    assert result["exit_code"] == 0
    assert result["stdout"] == "ok\n"
    assert result["timed_out"] is False


def test_run_in_sandbox_reports_non_zero_exit(monkeypatch):
    monkeypatch.setattr(sandbox, "sandbox_available", lambda: True)

    completed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="fail")
    monkeypatch.setattr(sandbox.subprocess, "run", lambda *a, **k: completed)

    result = sandbox.run_in_sandbox("/proj", "false")

    assert result["exit_code"] == 1
    assert result["stderr"] == "fail"


def test_run_in_sandbox_reports_timeout_without_raising(monkeypatch):
    monkeypatch.setattr(sandbox, "sandbox_available", lambda: True)

    def raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="docker", timeout=5)

    monkeypatch.setattr(sandbox.subprocess, "run", raise_timeout)

    result = sandbox.run_in_sandbox("/proj", "sleep 999", timeout=5)

    assert result["timed_out"] is True
    assert result["exit_code"] is None


def test_run_in_sandbox_raises_clear_error_when_docker_unavailable(monkeypatch):
    monkeypatch.setattr(sandbox, "sandbox_available", lambda: False)

    with pytest.raises(sandbox.SandboxUnavailable):
        sandbox.run_in_sandbox("/proj", "echo hi")


def test_sandbox_available_reflects_docker_info_success(monkeypatch):
    monkeypatch.setattr(
        sandbox.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=0),
    )

    assert sandbox.sandbox_available() is True


def test_sandbox_available_false_when_docker_info_fails(monkeypatch):
    monkeypatch.setattr(
        sandbox.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=1),
    )

    assert sandbox.sandbox_available() is False


def test_sandbox_available_false_when_docker_binary_missing(monkeypatch):
    def raise_not_found(*a, **k):
        raise FileNotFoundError()

    monkeypatch.setattr(sandbox.subprocess, "run", raise_not_found)

    assert sandbox.sandbox_available() is False


@pytest.mark.integration
def test_run_in_sandbox_actually_runs_a_real_container(tmp_path):
    # Real Docker, not mocked -- proves the sandbox genuinely isolates and
    # executes, not just that we constructed the right argv.
    result = sandbox.run_in_sandbox(
        str(tmp_path),
        "echo hello-from-sandbox",
        image="alpine:latest",
        network=False,
        memory="64m",
        cpus="0.25",
        timeout=60,
    )

    assert result["exit_code"] == 0
    assert "hello-from-sandbox" in result["stdout"]
    assert result["timed_out"] is False


@pytest.mark.integration
def test_run_in_sandbox_cannot_see_the_host_filesystem(tmp_path):
    result = sandbox.run_in_sandbox(
        str(tmp_path),
        "ls /root 2>&1; echo EXIT:$?",
        image="alpine:latest",
        network=False,
        memory="64m",
        cpus="0.25",
        timeout=60,
    )

    # /root inside the container is the container's own empty /root, not the
    # host's -- this assertion would fail if the host filesystem leaked in.
    assert ".ai-orchestrator" not in result["stdout"]


@pytest.mark.integration
def test_run_in_sandbox_project_path_is_writable(tmp_path):
    result = sandbox.run_in_sandbox(
        str(tmp_path),
        "echo written > /workspace/output.txt && cat /workspace/output.txt",
        image="alpine:latest",
        network=False,
        memory="64m",
        cpus="0.25",
        timeout=60,
    )

    assert result["exit_code"] == 0
    assert (tmp_path / "output.txt").read_text().strip() == "written"
