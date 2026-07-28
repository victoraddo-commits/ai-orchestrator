import subprocess

import pytest

import core.deployment_manager as deploy_mgr
import core.roadmap_manager as roadmap_manager
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


def test_build_image_passes_context_path_after_a_bare_dash_dash(tmp_path, monkeypatch):
    # build["project_path"] is user-controlled (POST /builds). Without a
    # `--` separator before it, a value starting with `-` could be parsed
    # as a docker/buildx flag instead of the build context (argument
    # injection) -- this is the actual fix, not just a defensive nicety.
    captured = {}

    def fake_docker(*args, **kwargs):
        captured["args"] = args
        return _proc(returncode=0)

    monkeypatch.setattr(deploy_mgr, "_docker", fake_docker)

    deploy_mgr.build_image(_build(project_path=str(tmp_path)))

    args = captured["args"]
    assert "--" in args
    dash_dash_index = args.index("--")
    assert args[dash_dash_index + 1] == str(tmp_path)


def test_build_image_rejects_a_project_path_that_does_not_exist(monkeypatch):
    monkeypatch.setattr(
        deploy_mgr, "_docker",
        lambda *a, **k: pytest.fail("docker should not be invoked for a nonexistent project_path"),
    )

    built, message = deploy_mgr.build_image(_build(project_path="/no/such/path/at/all"))

    assert built is False
    assert "does not exist" in message


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


def _init_repo(path, monkeypatch=None):
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    (path / "README.md").write_text("live repo\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "initial"], check=True)


def test_deploy_build_merges_a_self_modifying_build_instead_of_requiring_a_dockerfile(tmp_path, monkeypatch):
    live_repo = tmp_path / "live"
    _init_repo(live_repo)

    workspace_root = tmp_path / "self-build-workspaces"
    monkeypatch.setattr(roadmap_manager, "SELF_PROJECT_PATH", live_repo)
    monkeypatch.setattr(roadmap_manager, "SELF_BUILD_WORKSPACE_ROOT", workspace_root)

    clone_dir = workspace_root / "clone1"
    subprocess.run(["git", "clone", "-q", str(live_repo), str(clone_dir)], check=True)
    subprocess.run(["git", "-C", str(clone_dir), "checkout", "-q", "-b", "build-selfbuild1"], check=True)
    (clone_dir / "new_feature.py").write_text("# generated by Kai\n")
    subprocess.run(["git", "-C", str(clone_dir), "add", "."], check=True)
    subprocess.run(["git", "-C", str(clone_dir), "commit", "-q", "-m", "implement phase"], check=True)

    build = {"id": "selfbuild1", "name": "13Z", "project_path": str(clone_dir)}

    result = deploy_mgr.deploy_build(build)

    assert result["deployed"] is True
    assert (live_repo / "new_feature.py").exists()


def test_deploy_build_reports_a_merge_conflict_for_a_self_modifying_build(tmp_path, monkeypatch):
    live_repo = tmp_path / "live"
    _init_repo(live_repo)

    workspace_root = tmp_path / "self-build-workspaces"
    monkeypatch.setattr(roadmap_manager, "SELF_PROJECT_PATH", live_repo)
    monkeypatch.setattr(roadmap_manager, "SELF_BUILD_WORKSPACE_ROOT", workspace_root)

    clone_dir = workspace_root / "clone2"
    subprocess.run(["git", "clone", "-q", str(live_repo), str(clone_dir)], check=True)
    subprocess.run(["git", "-C", str(clone_dir), "checkout", "-q", "-b", "build-selfbuild2"], check=True)
    (clone_dir / "README.md").write_text("clone changed this line\n")
    subprocess.run(["git", "-C", str(clone_dir), "commit", "-q", "-am", "conflicting change"], check=True)

    # A commit lands on the live repo after the clone was taken, touching the
    # same line -- exactly what a real merge conflict looks like.
    (live_repo / "README.md").write_text("live changed this line too\n")
    subprocess.run(["git", "-C", str(live_repo), "commit", "-q", "-am", "unrelated live change"], check=True)

    build = {"id": "selfbuild2", "name": "13Z", "project_path": str(clone_dir)}

    result = deploy_mgr.deploy_build(build)

    assert result["deployed"] is False
    assert "onflict" in result["reason"]

    # The live repo must be left clean, not sitting mid-merge.
    status = subprocess.run(
        ["git", "-C", str(live_repo), "status", "--porcelain"], capture_output=True, text=True
    )
    assert status.stdout.strip() == ""


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
