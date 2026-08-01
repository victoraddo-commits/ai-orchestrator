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


def test_self_modifying_merge_reports_which_files_it_changed(tmp_path, monkeypatch):
    # 17C: the post-deploy service-restart decision needs to know exactly
    # which files the merge landed on the live orchestrator repo -- a
    # --no-ff merge commit's first-parent diff is the authoritative answer.
    live_repo = tmp_path / "live"
    _init_repo(live_repo)

    workspace_root = tmp_path / "self-build-workspaces"
    monkeypatch.setattr(roadmap_manager, "SELF_PROJECT_PATH", live_repo)
    monkeypatch.setattr(roadmap_manager, "SELF_BUILD_WORKSPACE_ROOT", workspace_root)

    clone_dir = workspace_root / "clone_changed_files"
    subprocess.run(["git", "clone", "-q", str(live_repo), str(clone_dir)], check=True)
    subprocess.run(["git", "-C", str(clone_dir), "checkout", "-q", "-b", "build-cf1"], check=True)
    (clone_dir / "core").mkdir()
    (clone_dir / "core" / "api.py").write_text("# new route\n")
    (clone_dir / "roadmap.json").write_text("{}\n")
    subprocess.run(["git", "-C", str(clone_dir), "add", "."], check=True)
    subprocess.run(["git", "-C", str(clone_dir), "commit", "-q", "-m", "implement phase"], check=True)

    build = {"id": "cf1", "name": "17C", "project_path": str(clone_dir)}

    result = deploy_mgr.deploy_build(build)

    assert result["deployed"] is True
    assert sorted(result["changed_files"]) == ["core/api.py", "roadmap.json"]


def test_changed_files_of_merge_returns_none_when_git_cannot_answer(tmp_path):
    _init_repo(tmp_path / "live")

    assert deploy_mgr._changed_files_of_merge(tmp_path / "live", "not-a-commit") is None


def test_deploy_build_succeeds_despite_uncommitted_roadmap_json_changes(tmp_path, monkeypatch):
    # Confirmed live 2026-07-29 (twice: 13G, then 13T) -- roadmap_engine.
    # save_roadmap() writes roadmap.json directly with no git commit, so
    # the scheduler's own concurrent bookkeeping (marking a phase
    # in_progress/failed/completed) can leave the live repo's working tree
    # dirty at the exact moment a deploy tries to merge -- `git merge`
    # refuses to run and the whole deploy fails despite the build itself
    # being entirely correct. The dirty roadmap.json must be committed
    # automatically so the merge proceeds.
    live_repo = tmp_path / "live"
    _init_repo(live_repo)

    workspace_root = tmp_path / "self-build-workspaces"
    monkeypatch.setattr(roadmap_manager, "SELF_PROJECT_PATH", live_repo)
    monkeypatch.setattr(roadmap_manager, "SELF_BUILD_WORKSPACE_ROOT", workspace_root)

    clone_dir = workspace_root / "clone_dirty"
    subprocess.run(["git", "clone", "-q", str(live_repo), str(clone_dir)], check=True)
    subprocess.run(["git", "-C", str(clone_dir), "checkout", "-q", "-b", "build-dirtytest"], check=True)
    (clone_dir / "new_feature.py").write_text("# generated by Kai\n")
    subprocess.run(["git", "-C", str(clone_dir), "add", "."], check=True)
    subprocess.run(["git", "-C", str(clone_dir), "commit", "-q", "-m", "implement phase"], check=True)

    # Simulate the scheduler's own concurrent, uncommitted write to the live
    # repo's roadmap.json -- the exact condition that broke 13G/13T.
    (live_repo / "roadmap.json").write_text('{"phases": []}')

    build = {"id": "dirtytest", "name": "13T", "project_path": str(clone_dir)}

    result = deploy_mgr.deploy_build(build)

    assert result["deployed"] is True
    assert (live_repo / "new_feature.py").exists()
    # The dirty roadmap.json write was preserved (committed), not discarded.
    assert (live_repo / "roadmap.json").read_text() == '{"phases": []}'
    status = subprocess.run(
        ["git", "-C", str(live_repo), "status", "--porcelain"], capture_output=True, text=True
    )
    assert status.stdout.strip() == ""


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


def _head(repo):
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()


def _clean(repo):
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True
    )
    return status.stdout.strip() == ""


def _make_dual_workspace(tmp_path, monkeypatch, build_id):
    """Two live repos + a dual-repo workspace holding a clone of each, with
    the build branch checked out in both -- the exact layout
    roadmap_manager._create_isolated_self_clone(include_plugin=True) plus
    build_manager._ensure_repo produce."""
    live_orchestrator = tmp_path / "live-orchestrator"
    _init_repo(live_orchestrator)
    live_plugin = tmp_path / "live-plugin"
    _init_repo(live_plugin)

    workspace_root = tmp_path / "self-build-workspaces"
    monkeypatch.setattr(roadmap_manager, "SELF_PROJECT_PATH", live_orchestrator)
    monkeypatch.setattr(roadmap_manager, "PLUGIN_PROJECT_PATH", live_plugin)
    monkeypatch.setattr(roadmap_manager, "SELF_BUILD_WORKSPACE_ROOT", workspace_root)

    workspace = workspace_root / "dualws1"
    workspace.mkdir(parents=True)
    orchestrator_clone = workspace / "ai-orchestrator"
    plugin_clone = workspace / "ai-orchestrator-plugin"
    subprocess.run(["git", "clone", "-q", str(live_orchestrator), str(orchestrator_clone)], check=True)
    subprocess.run(["git", "clone", "-q", str(live_plugin), str(plugin_clone)], check=True)

    branch = f"build-{build_id}"
    for clone in (orchestrator_clone, plugin_clone):
        subprocess.run(["git", "-C", str(clone), "checkout", "-q", "-b", branch], check=True)

    return live_orchestrator, live_plugin, orchestrator_clone, plugin_clone, workspace


def _commit_all(clone, message):
    subprocess.run(["git", "-C", str(clone), "add", "."], check=True)
    subprocess.run(["git", "-C", str(clone), "commit", "-q", "-m", message], check=True)


def test_deploy_build_merges_both_repos_of_a_dual_workspace(tmp_path, monkeypatch):
    live_orchestrator, live_plugin, orchestrator_clone, plugin_clone, workspace = (
        _make_dual_workspace(tmp_path, monkeypatch, "dual1")
    )

    (orchestrator_clone / "backend_feature.py").write_text("# backend half\n")
    _commit_all(orchestrator_clone, "backend half")
    (plugin_clone / "frontend_feature.ts").write_text("// frontend half\n")
    _commit_all(plugin_clone, "frontend half")

    build = {"id": "dual1", "name": "13G", "project_path": str(workspace)}

    result = deploy_mgr.deploy_build(build)

    assert result["deployed"] is True
    # Each repo's branch landed on its own live origin.
    assert (live_orchestrator / "backend_feature.py").exists()
    assert (live_plugin / "frontend_feature.ts").exists()
    assert str(live_orchestrator) in result["merged_repos"]
    assert str(live_plugin) in result["merged_repos"]
    # Backward-compatible single merge_commit: the orchestrator repo's HEAD.
    assert result["merge_commit"] == _head(live_orchestrator)


def test_deploy_build_rolls_back_the_first_repo_when_the_second_repo_fails(tmp_path, monkeypatch):
    # Atomicity: a conflict in the plugin repo must fail the whole deploy
    # and un-apply the already-merged orchestrator half -- never a partial
    # deploy where the backend landed but the frontend didn't.
    #
    # 17A: this test's conflict now surfaces as a "Stale base" error caught
    # by the pre-merge sync inside the merge lock (both live and clone
    # diverged on README.md's same line -- exactly the "another build
    # merged first" scenario that sync-then-retest exists for), not by the
    # git-merge step itself. The atomicity guarantee is still preserved:
    # neither live repo is touched, because the sync-then-merge sequence
    # aborts before any merge into live happens. failed_repo therefore
    # points at the clone whose sync conflicted, and rolled_back_repos is
    # empty (nothing to roll back). The defense-in-depth atomic rollback
    # of a merge-step failure is separately covered by a real merge failure
    # in _merge_branch_into_live_repo (e.g. a fetch failure).
    live_orchestrator, live_plugin, orchestrator_clone, plugin_clone, workspace = (
        _make_dual_workspace(tmp_path, monkeypatch, "dual2")
    )

    (orchestrator_clone / "backend_feature.py").write_text("# backend half\n")
    _commit_all(orchestrator_clone, "backend half")

    (plugin_clone / "README.md").write_text("clone changed this line\n")
    _commit_all(plugin_clone, "conflicting frontend change")
    # The live plugin repo moves on the same line after the clone was taken
    # -- a genuine merge conflict in the second repo only.
    (live_plugin / "README.md").write_text("live changed this line too\n")
    subprocess.run(["git", "-C", str(live_plugin), "commit", "-q", "-am", "live plugin change"], check=True)

    orchestrator_head_before = _head(live_orchestrator)
    plugin_head_before = _head(live_plugin)

    build = {"id": "dual2", "name": "13G", "project_path": str(workspace)}

    result = deploy_mgr.deploy_build(build)

    assert result["deployed"] is False
    assert "Stale base" in result["reason"]
    assert result["failed_repo"] == str(plugin_clone)

    # Neither live repo was touched -- the abort happened before any merge
    # into live could land, so there is nothing to roll back.
    assert _head(live_orchestrator) == orchestrator_head_before
    assert _head(live_plugin) == plugin_head_before
    assert not (live_orchestrator / "backend_feature.py").exists()

    # Neither live repo is left mid-merge or dirty.
    assert _clean(live_orchestrator)
    assert _clean(live_plugin)


def test_deploy_build_rollback_preserves_pre_existing_uncommitted_changes(tmp_path, monkeypatch):
    # 17A: this test's conflict now surfaces via the pre-merge stale-base
    # sync, aborting before any merge into live. Uncommitted live-repo
    # state must still survive intact -- the whole point of committing
    # the working tree before any merge attempt is that data is never lost
    # to a `git reset --hard` on a merge-step failure. Since 17A aborts
    # before the merge step, the pre-existing content simply stays where
    # it was (still uncommitted, or already committed if a prior operation
    # committed it -- either is fine, only the content matters).
    live_orchestrator, live_plugin, orchestrator_clone, plugin_clone, workspace = (
        _make_dual_workspace(tmp_path, monkeypatch, "dual2b")
    )

    (orchestrator_clone / "backend_feature.py").write_text("# backend half\n")
    _commit_all(orchestrator_clone, "backend half")

    (plugin_clone / "README.md").write_text("clone changed this line\n")
    _commit_all(plugin_clone, "conflicting frontend change")
    (live_plugin / "README.md").write_text("live changed this line too\n")
    subprocess.run(["git", "-C", str(live_plugin), "commit", "-q", "-am", "live plugin change"], check=True)

    # Pre-existing uncommitted change in the live orchestrator repo -- the
    # scheduler's own concurrent roadmap.json write that must survive the
    # deploy attempt regardless of how it fails.
    (live_orchestrator / "roadmap.json").write_text('{"phases": [{"id": "14B"}]}')
    pre_existing_content = (live_orchestrator / "roadmap.json").read_text()

    build = {"id": "dual2b", "name": "14B", "project_path": str(workspace)}

    result = deploy_mgr.deploy_build(build)

    assert result["deployed"] is False
    assert "Stale base" in result["reason"]
    assert result["failed_repo"] == str(plugin_clone)

    # The backend merge never happened -- new file must not exist on live.
    assert not (live_orchestrator / "backend_feature.py").exists()

    # The pre-existing uncommitted roadmap.json change must survive intact.
    assert (live_orchestrator / "roadmap.json").exists()
    assert (live_orchestrator / "roadmap.json").read_text() == pre_existing_content

    # 17A: aborting before the merge step means this write may still be
    # sitting as untracked/uncommitted, exactly as the operator left it --
    # no data destruction from a `git reset --hard`. Content preservation
    # (asserted above) is the real requirement; whether the file is
    # untracked, unstaged, staged, or already committed is implementation
    # detail as long as nothing was lost.

    # The plugin repo must not be mid-merge.
    assert _clean(live_plugin)


def test_deploy_build_fails_whole_deploy_when_the_first_repo_fails(tmp_path, monkeypatch):
    live_orchestrator, live_plugin, orchestrator_clone, plugin_clone, workspace = (
        _make_dual_workspace(tmp_path, monkeypatch, "dual3")
    )

    (orchestrator_clone / "README.md").write_text("clone changed this line\n")
    _commit_all(orchestrator_clone, "conflicting backend change")
    (live_orchestrator / "README.md").write_text("live changed this line too\n")
    subprocess.run(["git", "-C", str(live_orchestrator), "commit", "-q", "-am", "live change"], check=True)

    (plugin_clone / "frontend_feature.ts").write_text("// frontend half\n")
    _commit_all(plugin_clone, "frontend half")

    plugin_head_before = _head(live_plugin)
    orchestrator_head_before = _head(live_orchestrator)

    build = {"id": "dual3", "name": "13G", "project_path": str(workspace)}

    result = deploy_mgr.deploy_build(build)

    assert result["deployed"] is False
    # 17A: caught by the pre-merge stale-base sync on the orchestrator
    # clone (it and live diverged on the same line -- exactly the "another
    # build merged first" case). failed_repo therefore points at the
    # orchestrator clone, and neither live repo is touched.
    assert result["failed_repo"] == str(orchestrator_clone)
    assert "Stale base" in result["reason"]

    # Neither live repo was touched -- no partial apply from the other side.
    assert _head(live_orchestrator) == orchestrator_head_before
    assert _head(live_plugin) == plugin_head_before
    assert not (live_plugin / "frontend_feature.ts").exists()
    assert _clean(live_orchestrator)
    assert _clean(live_plugin)


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
