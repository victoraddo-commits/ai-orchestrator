"""17H: after a successful self-modifying merge that lands plugin source,
the CloudCLI bundle must actually be rebuilt, copied to the directory
CloudCLI serves from, and cloudcli.service restarted -- otherwise the
change is silently invisible (confirmed live 2026-07-30: 17B's chat panel
merged cleanly into the live plugin repo while CloudCLI kept serving a
dist/ bundle dated 2026-07-29 until a human ran npm run build + cp)."""

import subprocess

import core.plugin_deployer as deployer


PLUGIN_REPO = "/project/src/ai-orchestrator-plugin"


def _proc(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _setup(monkeypatch, tmp_path, plugin_changed_files, npm_returncode=0,
           npm_stderr="", systemctl_returncode=0, systemctl_stderr="",
           dist_artifacts=("index.js", "index.js.map", "server.js", "server.js.map")):
    """Wire the module's seams to a fake plugin repo and fake commands.

    Returns (state, plugin_repo, serve_dir) where state records every npm
    build and systemctl invocation.
    """
    plugin_repo = tmp_path / "plugin-live"
    (plugin_repo / "dist").mkdir(parents=True)
    serve_dir = tmp_path / "cloudcli-serves-from" / "dist"

    state = {"npm_builds": [], "systemctl": []}

    def fake_npm_build(repo, timeout=deployer.NPM_BUILD_TIMEOUT):
        state["npm_builds"].append(str(repo))
        if npm_returncode == 0:
            # A successful tsc writes the compiled bundle into dist/.
            for name in dist_artifacts:
                (plugin_repo / "dist" / name).write_text(f"compiled {name}")
            # tsc also emits declarations -- they are not part of the
            # served bundle and must not be copied.
            (plugin_repo / "dist" / "index.d.ts").write_text("declare {}")
            (plugin_repo / "dist" / "index.d.ts.map").write_text("{}")
        return _proc(returncode=npm_returncode, stderr=npm_stderr)

    def fake_systemctl(*args, timeout=60):
        state["systemctl"].append(args)
        return _proc(returncode=systemctl_returncode, stderr=systemctl_stderr)

    monkeypatch.setattr(deployer, "_plugin_repo_path", lambda: plugin_repo)
    monkeypatch.setattr(deployer, "_npm_build", fake_npm_build)
    monkeypatch.setattr(deployer, "_systemctl", fake_systemctl)
    monkeypatch.setattr(deployer, "CLOUDCLI_PLUGIN_DIST", serve_dir)
    monkeypatch.setattr(
        deployer, "_changed_files_of_merge", lambda repo, commit: plugin_changed_files
    )

    return state, plugin_repo, serve_dir


def _dual_repo_deploy(plugin_repo, orchestrator_files=("roadmap.json",)):
    return {
        "deployed": True,
        "merged_branch": "build-b1",
        "merge_commit": "abc123",
        "merged_repos": {
            "/project/src/ai-orchestrator": "abc123",
            str(plugin_repo): "def456",
        },
        "changed_files": list(orchestrator_files),
    }


_BUILD = {"id": "b1", "name": "17H"}


# --- completion criterion 1: plugin merge -> build + copy + restart ---------


def test_plugin_source_merge_rebuilds_copies_and_restarts_cloudcli(monkeypatch, tmp_path):
    state, plugin_repo, serve_dir = _setup(monkeypatch, tmp_path, ["src/index.ts"])
    result = _dual_repo_deploy(plugin_repo)

    record = deployer.redeploy_plugin_if_needed(_BUILD, result)

    assert state["npm_builds"] == [str(plugin_repo)]
    assert state["systemctl"] == [("restart", deployer.CLOUDCLI_SERVICE)]
    assert record["rebuilt"] is True
    assert record["deployed"] is True
    assert record["restarted"] is True
    # The compiled bundle actually reached the directory CloudCLI serves.
    assert sorted(p.name for p in serve_dir.iterdir()) == [
        "index.js", "index.js.map", "server.js", "server.js.map",
    ]
    assert (serve_dir / "index.js").read_text() == "compiled index.js"
    # Declarations are build byproducts, not part of the served bundle.
    assert not (serve_dir / "index.d.ts").exists()
    # Observability: the outcome is recorded on the deploy result.
    assert result["plugin_redeploy"] == record


def test_redeploy_replaces_a_stale_previously_deployed_bundle(monkeypatch, tmp_path):
    state, plugin_repo, serve_dir = _setup(monkeypatch, tmp_path, ["src/index.ts"])
    serve_dir.mkdir(parents=True)
    (serve_dir / "index.js").write_text("stale bundle from 2026-07-29")

    deployer.redeploy_plugin_if_needed(_BUILD, _dual_repo_deploy(plugin_repo))

    assert (serve_dir / "index.js").read_text() == "compiled index.js"


# --- completion criterion 2: backend-only merge -> no rebuild, no restart ---


def test_orchestrator_only_merge_never_touches_the_plugin(monkeypatch, tmp_path):
    state, plugin_repo, serve_dir = _setup(monkeypatch, tmp_path, ["src/index.ts"])
    # A single-repo self-modifying merge: only the orchestrator repo merged.
    result = {
        "deployed": True,
        "merged_branch": "build-b1",
        "merge_commit": "abc123",
        "merged_repos": {"/project/src/ai-orchestrator": "abc123"},
        "changed_files": ["core/api.py"],
    }

    record = deployer.redeploy_plugin_if_needed(_BUILD, result)

    assert record is None
    assert state["npm_builds"] == []
    assert state["systemctl"] == []
    assert not serve_dir.exists()
    assert "plugin_redeploy" not in result


def test_dual_repo_merge_with_no_bundle_source_changes_skips_rebuild(monkeypatch, tmp_path):
    # The plugin repo WAS merged, but only non-bundle files changed
    # (README, plugin tests) -- restarting CloudCLI would drop its open
    # sessions for nothing.
    state, plugin_repo, serve_dir = _setup(
        monkeypatch, tmp_path, ["README.md", "tests/chat-panel.test.mjs"]
    )
    result = _dual_repo_deploy(plugin_repo)

    record = deployer.redeploy_plugin_if_needed(_BUILD, result)

    assert state["npm_builds"] == []
    assert state["systemctl"] == []
    assert record["rebuilt"] is False
    assert record["restarted"] is False
    assert result["plugin_redeploy"] == record


def test_failed_deploy_never_rebuilds(monkeypatch, tmp_path):
    state, _, _ = _setup(monkeypatch, tmp_path, ["src/index.ts"])
    failed = {"deployed": False, "reason": "Merge conflict merging build-b1"}

    record = deployer.redeploy_plugin_if_needed(_BUILD, failed)

    assert record is None
    assert state["npm_builds"] == []
    assert state["systemctl"] == []
    assert "plugin_redeploy" not in failed


def test_container_deploy_never_rebuilds(monkeypatch, tmp_path):
    state, _, _ = _setup(monkeypatch, tmp_path, ["src/index.ts"])

    record = deployer.redeploy_plugin_if_needed(
        {"id": "b2", "name": "todo-app"},
        {"deployed": True, "container": "aiapp-todo-app", "port": 32768},
    )

    assert record is None
    assert state["npm_builds"] == []
    assert state["systemctl"] == []


# --- completion criterion 3: build failure leaves the old bundle intact -----


def test_failed_npm_build_is_logged_and_leaves_previous_bundle_untouched(monkeypatch, tmp_path):
    state, plugin_repo, serve_dir = _setup(
        monkeypatch, tmp_path, ["src/index.ts"],
        npm_returncode=2, npm_stderr="src/index.ts(41,3): error TS2304: Cannot find name 'foo'.",
    )
    serve_dir.mkdir(parents=True)
    (serve_dir / "index.js").write_text("previously deployed, still good")
    (serve_dir / "index.js.map").write_text("previous map")
    result = _dual_repo_deploy(plugin_repo)

    record = deployer.redeploy_plugin_if_needed(_BUILD, result)

    # The failure is recorded, not raised, and visible on the deploy result.
    assert record["rebuilt"] is False
    assert record["deployed"] is False
    assert record["restarted"] is False
    assert "TS2304" in record["error"]
    assert result["plugin_redeploy"] == record
    # CloudCLI was NOT restarted onto a broken/stale state...
    assert state["systemctl"] == []
    # ...and the previously deployed bundle is byte-for-byte untouched.
    assert (serve_dir / "index.js").read_text() == "previously deployed, still good"
    assert (serve_dir / "index.js.map").read_text() == "previous map"


def test_npm_build_exception_is_recorded_not_raised(monkeypatch, tmp_path):
    state, plugin_repo, serve_dir = _setup(monkeypatch, tmp_path, ["src/index.ts"])

    def boom(repo, timeout=deployer.NPM_BUILD_TIMEOUT):
        raise FileNotFoundError("npm not found")

    monkeypatch.setattr(deployer, "_npm_build", boom)

    record = deployer.redeploy_plugin_if_needed(_BUILD, _dual_repo_deploy(plugin_repo))

    assert record["rebuilt"] is False
    assert record["deployed"] is False
    assert "npm not found" in record["error"]
    assert state["systemctl"] == []
    assert not serve_dir.exists()


def test_build_that_produces_no_artifacts_does_not_restart_cloudcli(monkeypatch, tmp_path):
    # rc=0 but nothing landed in dist/ -- deploying nothing would leave the
    # served dir stale while claiming success; record the error instead.
    state, plugin_repo, serve_dir = _setup(
        monkeypatch, tmp_path, ["src/index.ts"], dist_artifacts=(),
    )
    # This fake "successful" build emits only declarations, no .js bundle.
    monkeypatch.setattr(
        deployer, "_npm_build",
        lambda repo, timeout=deployer.NPM_BUILD_TIMEOUT: _proc(returncode=0),
    )

    record = deployer.redeploy_plugin_if_needed(_BUILD, _dual_repo_deploy(plugin_repo))

    assert record["rebuilt"] is True
    assert record["deployed"] is False
    assert "no .js artifacts" in record["error"]
    assert state["systemctl"] == []


def test_cloudcli_restart_failure_is_recorded_not_raised(monkeypatch, tmp_path):
    state, plugin_repo, serve_dir = _setup(
        monkeypatch, tmp_path, ["src/index.ts"],
        systemctl_returncode=1, systemctl_stderr="Failed to restart: access denied",
    )

    record = deployer.redeploy_plugin_if_needed(_BUILD, _dual_repo_deploy(plugin_repo))

    # The bundle itself deployed fine; only the restart failed, and that
    # is surfaced rather than swallowed.
    assert record["rebuilt"] is True
    assert record["deployed"] is True
    assert record["restarted"] is False
    assert "access denied" in record["error"]
    assert (serve_dir / "index.js").exists()


# --- fail-safe scoping (mirrors 17C) ----------------------------------------


def test_unknown_plugin_changed_files_conservatively_rebuilds(monkeypatch, tmp_path):
    # git couldn't tell us what the plugin merge changed -- a stale bundle
    # is a silent failure, an extra rebuild+restart is a blip.
    state, plugin_repo, serve_dir = _setup(monkeypatch, tmp_path, None)

    record = deployer.redeploy_plugin_if_needed(_BUILD, _dual_repo_deploy(plugin_repo))

    assert state["npm_builds"] == [str(plugin_repo)]
    assert record["deployed"] is True
    assert state["systemctl"] == [("restart", deployer.CLOUDCLI_SERVICE)]


def test_build_config_changes_also_invalidate_the_bundle():
    assert deployer.plugin_needs_rebuild(["tsconfig.json"]) is True
    assert deployer.plugin_needs_rebuild(["package.json"]) is True
    assert deployer.plugin_needs_rebuild(["src/server.ts", "roadmap-notes.md"]) is True
    assert deployer.plugin_needs_rebuild(["README.md", "docs/x.md", "tests/a.test.mjs"]) is False
    assert deployer.plugin_needs_rebuild([]) is False
