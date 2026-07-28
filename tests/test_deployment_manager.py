import subprocess

import pytest

import core.deployment_manager as deploy_mgr
from core.remediation import load_remediations


def _proc(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _build(name="Todo App", project_path="/tmp/proj"):
    return {"id": "b1", "name": name, "project_path": project_path}


def test_container_name_for_slugifies_the_build_name():
    assert deploy_mgr.container_name_for(_build(name="My Cool App!!")) == "aiapp-my-cool-app"


def test_container_name_for_is_stable_for_the_same_name():
    a = deploy_mgr.container_name_for(_build(name="Todo App"))
    b = deploy_mgr.container_name_for(_build(name="Todo App"))
    assert a == b


def test_deploy_build_fails_cleanly_without_a_dockerfile(tmp_path):
    result = deploy_mgr.deploy_build(_build(project_path=str(tmp_path)))

    assert result["deployed"] is False
    assert "Dockerfile" in result["reason"]


def test_deploy_build_fails_cleanly_when_image_build_fails(tmp_path, monkeypatch):
    (tmp_path / "Dockerfile").write_text("FROM scratch")

    monkeypatch.setattr(
        deploy_mgr, "_docker",
        lambda *a, **k: _proc(returncode=1, stderr="build error: bad Dockerfile"),
    )

    result = deploy_mgr.deploy_build(_build(project_path=str(tmp_path)))

    assert result["deployed"] is False
    assert "build" in result["reason"].lower()

    remediations = load_remediations()
    assert len(remediations) == 1
    assert remediations[0]["status"] == "failed"


def test_deploy_build_rolls_back_on_failed_verification(tmp_path, monkeypatch):
    (tmp_path / "Dockerfile").write_text("FROM scratch")

    calls = []

    def fake_docker(*args, **kwargs):
        calls.append(args)
        if args[0] == "build":
            return _proc(returncode=0)
        if args[0] == "run":
            return _proc(returncode=0)
        if args[0] == "inspect" and "{{.State.Running}}" in args:
            return _proc(returncode=0, stdout="false")
        if args[0] == "logs":
            return _proc(returncode=0, stdout="app crashed on boot")
        return _proc(returncode=0)

    monkeypatch.setattr(deploy_mgr, "_docker", fake_docker)
    monkeypatch.setattr(deploy_mgr.time, "sleep", lambda *_: None)

    result = deploy_mgr.deploy_build(_build(project_path=str(tmp_path)))

    assert result["deployed"] is False
    assert "not running" in result["reason"] or "crash" in result["reason"]

    remove_calls = [c for c in calls if c[0] == "rm"]
    assert remove_calls, "expected the failed staging container to be cleaned up"


def test_deploy_build_promotes_staging_on_success(tmp_path, monkeypatch):
    (tmp_path / "Dockerfile").write_text("FROM scratch")

    docker_calls = []

    def fake_docker(*args, **kwargs):
        docker_calls.append(args)
        if args[0] == "inspect" and "{{.State.Running}}" in args:
            return _proc(returncode=0, stdout="true")
        if args[0] == "inspect" and "{{.RestartCount}}" in args:
            return _proc(returncode=0, stdout="0")
        if args[0] == "inspect":  # existence check
            return _proc(returncode=1)  # no pre-existing production container
        if args[0] == "port":
            return _proc(returncode=0, stdout="8000/tcp -> 0.0.0.0:32768")
        return _proc(returncode=0)

    monkeypatch.setattr(deploy_mgr, "_docker", fake_docker)
    monkeypatch.setattr(deploy_mgr.time, "sleep", lambda *_: None)
    monkeypatch.setattr(deploy_mgr, "_http_check", lambda port: None)

    result = deploy_mgr.deploy_build(_build(project_path=str(tmp_path)))

    assert result["deployed"] is True
    assert result["container"] == "aiapp-todo-app"
    assert result["port"] == 32768

    rename_calls = [c for c in docker_calls if c[0] == "rename"]
    assert any(c[1].endswith("-staging") for c in rename_calls)

    remediations = load_remediations()
    assert remediations[-1]["status"] == "completed"


def test_rollback_strategy_restores_previous_container_when_one_exists(monkeypatch):
    calls = []

    def fake_docker(*args, **kwargs):
        calls.append(args)
        if args[0] == "inspect":  # existence check for "-previous"
            return _proc(returncode=0)
        return _proc(returncode=0)

    monkeypatch.setattr(deploy_mgr, "_docker", fake_docker)

    result = deploy_mgr._rollback_strategy({"service": "aiapp-todo-app"})

    assert result["rolled_back_to"] == "previous production container"
    rename_calls = [c for c in calls if c[0] == "rename"]
    assert ("rename", "aiapp-todo-app-previous", "aiapp-todo-app") in rename_calls


def test_rollback_strategy_reports_no_previous_container(monkeypatch):
    monkeypatch.setattr(deploy_mgr, "_docker", lambda *a, **k: _proc(returncode=1))

    result = deploy_mgr._rollback_strategy({"service": "aiapp-todo-app"})

    assert result["rolled_back_to"] is None


@pytest.mark.integration
def test_deploy_build_real_end_to_end(tmp_path):
    (tmp_path / "Dockerfile").write_text(
        "FROM python:3.12-slim\n"
        "RUN echo 'hello from deployed app' > /index.html\n"
        "EXPOSE 8000\n"
        "CMD [\"python3\", \"-m\", \"http.server\", \"8000\"]\n"
    )

    build = {"id": "real1", "name": "Deploy Test App", "project_path": str(tmp_path)}

    try:
        result = deploy_mgr.deploy_build(build)

        assert result["deployed"] is True
        assert result["port"] is not None

        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{result['port']}/", timeout=5) as resp:
            body = resp.read().decode()
        assert "hello from deployed app" in body
    finally:
        name = deploy_mgr.container_name_for(build)
        subprocess.run(["docker", "rm", "-f", name, f"{name}-staging", f"{name}-previous"],
                        capture_output=True)
        subprocess.run(["docker", "rmi", "-f", f"{name}:latest"], capture_output=True)
