import fcntl
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from core.remediation import (
    create_remediation,
    start_remediation,
    complete_remediation,
    register_rollback,
)


DEPLOY_ACTION = "deploy_build"


# 17A: only one merge into a live repo may proceed at a time -- concurrent
# deployers race on the same git HEAD otherwise (the exact bug the pre-17A
# single-in-flight-phase guard existed to prevent). File-based fcntl.flock
# matches core.memory_manager's convention: correct across separate
# processes (scheduler service + API), and per-open-file-description so it
# also excludes two threads in one process (each _run_deployment worker
# opens its own fd, so two flock() calls on separate fds inside one process
# still serialize). Blocking is deliberate -- a second DEPLOYING build's
# worker just waits its turn rather than being told to try again later.
#
# The lock file lives under core.memory.MEMORY_DIR, alongside the other
# *.lock files memory_manager already writes there. In production
# MEMORY_DIR is SELF_PROJECT_PATH/memory (gitignored), so the lock never
# leaks into a git diff; in tests conftest.py isolates MEMORY_DIR to a
# tmp_path, so the lock never pollutes a test's live-repo working tree.
def _merge_lock_path():
    # Imported lazily inside the function to keep this module usable at
    # import time regardless of whether core.memory has been imported yet
    # -- matches the lazy-import pattern _self_modifying_merge_targets()
    # already uses to break the roadmap_manager <-> deployment_manager
    # circular import.
    from core.memory import MEMORY_DIR
    return Path(MEMORY_DIR) / "live_repo_merge.lock"


@contextmanager
def _live_merge_lock():
    lock_path = _merge_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _slugify(name):
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "app"


def container_name_for(build):
    return f"aiapp-{_slugify(build['name'])}"


def _docker(*args, timeout=60):
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


def _container_exists(name):
    return _docker("inspect", name).returncode == 0


def _container_running(name):
    result = _docker("inspect", "-f", "{{.State.Running}}", name)
    return result.returncode == 0 and result.stdout.strip() == "true"


def _restart_count(name):
    result = _docker("inspect", "-f", "{{.RestartCount}}", name)
    return int(result.stdout.strip()) if result.returncode == 0 and result.stdout.strip().isdigit() else 0


def _assigned_port(name):
    result = _docker("port", name)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    match = re.search(r":(\d+)$", result.stdout.strip().splitlines()[0])
    return int(match.group(1)) if match else None


def _http_check(port):
    # Best-effort/informational only -- not every deployed container serves
    # plain HTTP on its exposed port, so this never fails the deployment by
    # itself (see verify_deployment: only container-running/crash-loop are
    # hard gates).
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
            return resp.status < 500
    except (urllib.error.URLError, OSError):
        return None


def has_dockerfile(project_path):
    return os.path.exists(os.path.join(project_path, "Dockerfile"))


def build_image(build):
    name = container_name_for(build)

    # project_path is user-controlled (POST /builds -> create_build).
    # abspath() plus a hard `--` before it (stopping docker/buildx flag
    # parsing) closes the argument-injection path a value like
    # "--privileged" or "-t evil:latest" would otherwise open.
    project_path = os.path.abspath(build["project_path"])

    if not os.path.isdir(project_path):
        return False, f"project_path does not exist or is not a directory: {project_path}"

    # Plain `docker build` fails under this LXC's AppArmor restrictions
    # (confirmed live: "apparmor failed to apply profile ... no such file or
    # directory" on any RUN step) -- buildx with these flags is the same
    # working pattern already used for this LXC's other Docker builds.
    result = _docker(
        "buildx", "build",
        "--allow", "security.insecure",
        "--security-opt", "apparmor=unconfined",
        "--load",
        "-t", f"{name}:latest",
        "--", project_path,
        timeout=600,
    )
    log = (result.stdout or "") + (result.stderr or "")
    return result.returncode == 0, log[-2000:]


def run_staging_container(build):
    name = container_name_for(build)
    staging_name = f"{name}-staging"

    _docker("rm", "-f", staging_name)

    result = _docker(
        "run", "-d", "--name", staging_name,
        "--security-opt", "apparmor=unconfined",
        "-P",
        f"{name}:latest",
    )
    return result.returncode == 0, staging_name, result.stderr


def verify_deployment(container_name, wait_seconds=3):
    time.sleep(wait_seconds)

    if not _container_running(container_name):
        logs = _docker("logs", "--tail", "50", container_name).stdout
        return {"healthy": False, "reason": "container is not running (exited or crashed)", "logs_tail": logs}

    restarts = _restart_count(container_name)
    if restarts > 0:
        return {"healthy": False, "reason": f"container restarted {restarts} times (crash loop)"}

    port = _assigned_port(container_name)
    http_ok = _http_check(port) if port else None

    return {"healthy": True, "port": port, "http_ok": http_ok}


def _demote_current_production(name):
    if _container_exists(name):
        _docker("rm", "-f", f"{name}-previous")
        _docker("stop", name)
        _docker("rename", name, f"{name}-previous")


def _promote_staging_to_production(build):
    name = container_name_for(build)
    staging_name = f"{name}-staging"
    _demote_current_production(name)
    _docker("rename", staging_name, name)
    return name


def _rollback_strategy(remediation):
    name = remediation["service"]
    previous = f"{name}-previous"

    _docker("rm", "-f", name)

    if _container_exists(previous):
        _docker("rename", previous, name)
        _docker("start", name)
        return {"rolled_back_to": "previous production container"}

    return {"rolled_back_to": None, "note": "no previous container existed -- production left undeployed"}


register_rollback(DEPLOY_ACTION, _rollback_strategy)


def _git_head_of(repo):
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True,
    ).stdout.strip()


def _changed_files_of_merge(repo, merge_commit):
    # Every self-modifying merge is --no-ff, so the merge commit's first
    # parent is exactly the pre-merge live HEAD: diffing first-parent ->
    # merge commit lists precisely the files this deploy landed. Returns
    # None (unknown) if git can't answer, so downstream restart logic can
    # fail safe (restart) instead of silently running stale code.
    result = subprocess.run(
        ["git", "-C", str(repo), "diff", "--name-only", f"{merge_commit}^1", merge_commit],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _commit_dirty_working_tree(live_repo):
    # roadmap_engine.save_roadmap() writes roadmap.json directly to disk on
    # every phase status change (in_progress/completed/failed), with no git
    # commit -- the scheduler can leave the live repo's working tree dirty
    # at any moment a deploy happens to land. `git merge` refuses to run
    # against local changes it might overwrite and aborts outright.
    # Confirmed live 2026-07-29 (twice: 13G, then 13T) -- both builds did
    # genuinely correct work and passed every gate, only to fail here on an
    # unrelated concurrent roadmap.json write. Commit whatever's dirty
    # first so the merge always runs against a clean tree; nothing is lost
    # either way since these are exactly the bookkeeping writes that were
    # about to be committed by this same deploy's own merge commit anyway.
    status = subprocess.run(
        ["git", "-C", str(live_repo), "status", "--porcelain"], capture_output=True, text=True,
    )
    if not status.stdout.strip():
        return

    subprocess.run(["git", "-C", str(live_repo), "add", "-A"], capture_output=True, text=True)
    subprocess.run(
        ["git", "-C", str(live_repo), "commit", "-q", "-m", "Live bookkeeping: concurrent scheduler state updates"],
        capture_output=True, text=True,
    )


def _merge_branch_into_live_repo(live_repo, clone_path, branch, build_name):
    _commit_dirty_working_tree(live_repo)

    fetch = subprocess.run(
        ["git", "-C", str(live_repo), "fetch", "-q", clone_path, f"{branch}:{branch}"],
        capture_output=True, text=True,
    )
    if fetch.returncode != 0:
        return {"merged": False, "reason": f"Failed to fetch build branch: {fetch.stderr.strip()}"}

    merge = subprocess.run(
        ["git", "-C", str(live_repo), "merge", "--no-ff", "-m", f"Merge {branch}: {build_name}", branch],
        capture_output=True, text=True,
    )

    if merge.returncode != 0:
        subprocess.run(["git", "-C", str(live_repo), "merge", "--abort"], capture_output=True, text=True)
        subprocess.run(["git", "-C", str(live_repo), "branch", "-D", branch], capture_output=True, text=True)
        return {"merged": False, "reason": f"Merge conflict merging {branch}: {merge.stderr.strip()}"}

    subprocess.run(["git", "-C", str(live_repo), "branch", "-D", branch], capture_output=True, text=True)

    return {"merged": True, "merge_commit": _git_head_of(live_repo)}


def _self_modifying_merge_targets(build):
    # (live_repo, clone_path) pairs: each repo in the build's workspace is
    # merged back into its own live origin. A single-repo workspace (today's
    # unchanged common case) is one pair -- the workspace itself into
    # SELF_PROJECT_PATH. A dual-repo workspace (13Q: phases that touch the
    # CloudCLI plugin) adds the plugin clone into the plugin's own live repo.
    from core.roadmap_manager import (
        SELF_PROJECT_PATH,
        PLUGIN_PROJECT_PATH,
        ORCHESTRATOR_CLONE_DIRNAME,
        PLUGIN_CLONE_DIRNAME,
        is_dual_repo_workspace,
    )

    workspace = os.path.abspath(build["project_path"])

    if is_dual_repo_workspace(workspace):
        return [
            (str(SELF_PROJECT_PATH), os.path.join(workspace, ORCHESTRATOR_CLONE_DIRNAME)),
            (str(PLUGIN_PROJECT_PATH), os.path.join(workspace, PLUGIN_CLONE_DIRNAME)),
        ]

    return [(str(SELF_PROJECT_PATH), workspace)]


def _merge_self_modifying_build(build):
    # A self-modifying build's project_path is a disposable workspace of
    # clones of the live repo(s) (core.roadmap_manager._create_isolated_
    # self_clone), not a deployable app -- there's no Dockerfile and never
    # will be. "Deploying" it means landing its committed changes on each
    # live repo instead of building/running a container.
    #
    # 17A: the whole sequence -- stale-base sync, pre-merge retest, and
    # every (live_repo, clone) merge with its partial-failure rollback --
    # runs inside one exclusive flock on live_repo_merge.lock, so a second
    # concurrent DEPLOYING build's worker thread blocks here rather than
    # racing on the same live HEAD. The lock spans the entire dual-repo
    # atomic merge (not just the individual git-merge calls) so a build
    # can never observe the live repos in a mid-merge state.
    with _live_merge_lock():
        return _merge_self_modifying_build_locked(build)


def _merge_self_modifying_build_locked(build):
    # 17A: stale-base handling. Between when this build's clone was made
    # (or last generated against) and now, another self-modifying build may
    # have merged first and moved live HEAD. Sync each clone with its live
    # origin before merging: a conflict here means the two builds edited
    # incompatibly, fail immediately with a clear reason; a clean sync that
    # actually moved the clone's HEAD means the pre-merge test result was
    # taken against a now-outdated base, so re-run the test suite against
    # the current base before landing. If the sync didn't move HEAD (clone
    # was already current), pytest is skipped -- 300s of re-testing when
    # nothing changed is pure latency.
    from core.roadmap_manager import (
        self_build_repo_paths,
        _sync_clone_with_live_repo,
        _run_self_build_tests,
    )

    pre_merge_retest = {"skipped": "clone already current"}
    any_clone_moved = False

    for repo_path in self_build_repo_paths(build["project_path"]):
        pre_sync_head = _git_head_of(repo_path)
        synced, sync_error = _sync_clone_with_live_repo(repo_path)
        if not synced:
            return {
                "deployed": False,
                "reason": (
                    "Stale base: build branch conflicts with live HEAD "
                    "(another build merged first) -- " + (sync_error or "")
                ),
                "failed_repo": repo_path,
                "pre_merge_retest": {"skipped": "sync failed before retest"},
            }
        post_sync_head = _git_head_of(repo_path)
        if pre_sync_head != post_sync_head:
            any_clone_moved = True

    if any_clone_moved:
        retest = _run_self_build_tests(build["project_path"])
        pre_merge_retest = retest
        if not retest.get("passed"):
            return {
                "deployed": False,
                "reason": (
                    "Stale base: tests failed against latest live HEAD "
                    "after another build merged first -- "
                    + (retest.get("output") or "")
                ),
                "pre_merge_retest": retest,
            }

    branch = f"build-{build['id']}"
    targets = _self_modifying_merge_targets(build)

    merged = []  # (live_repo, pre_merge_head) -- for rollback on a later failure
    repo_merge_commits = {}

    for live_repo, clone_path in targets:
        # Commit any pre-existing dirty working-tree state (e.g. concurrent
        # roadmap.json writes by the scheduler) BEFORE capturing pre_merge_head
        # so that the rollback floor includes these bookkeeping changes rather
        # than silently discarding them on a git reset --hard later.
        _commit_dirty_working_tree(live_repo)
        pre_merge_head = _git_head_of(live_repo)
        result = _merge_branch_into_live_repo(live_repo, clone_path, branch, build["name"])

        if not result["merged"]:
            # Atomicity: a merge conflict or failure in either repo fails
            # the whole deploy. Any repo already merged is reset back to its
            # pre-merge HEAD so the live system is never left half-deployed.
            for merged_repo, previous_head in reversed(merged):
                subprocess.run(
                    ["git", "-C", merged_repo, "reset", "--hard", previous_head],
                    capture_output=True, text=True,
                )
            return {
                "deployed": False,
                "reason": f"{result['reason']} (repo: {live_repo})",
                "failed_repo": live_repo,
                "rolled_back_repos": [repo for repo, _ in merged],
                "pre_merge_retest": pre_merge_retest,
            }

        merged.append((live_repo, pre_merge_head))
        repo_merge_commits[live_repo] = result["merge_commit"]

    # Which files did this deploy actually land on the orchestrator's own
    # live repo? Drives the post-deploy service-restart decision (17C):
    # only the primary repo's changes matter here -- the plugin repo is
    # not code either orchestrator service imports. The plugin repo's own
    # changed files are derived separately by core.plugin_deployer (17H)
    # from "merged_repos" below, driving the CloudCLI bundle rebuild.
    primary_repo = targets[0][0]
    changed_files = _changed_files_of_merge(primary_repo, repo_merge_commits[primary_repo])

    return {
        "deployed": True,
        "merged_branch": branch,
        # Backward-compatible single value: the primary (orchestrator)
        # repo's merge commit, exactly what this key meant before 13Q.
        "merge_commit": repo_merge_commits[targets[0][0]],
        "merged_repos": repo_merge_commits,
        "changed_files": changed_files,
        "pre_merge_retest": pre_merge_retest,
    }


def deploy_build(build):
    from core.roadmap_manager import is_self_modifying

    if is_self_modifying(build["project_path"]):
        return _merge_self_modifying_build(build)

    name = container_name_for(build)

    if not has_dockerfile(build["project_path"]):
        return {"deployed": False, "reason": "No Dockerfile found in project -- cannot deploy"}

    remediation = create_remediation(
        approval_id=build["id"], trace_id=build["id"], action=DEPLOY_ACTION, service=name
    )
    start_remediation(remediation["id"], snapshot={"command": f"deploy {name}"})

    built, build_log = build_image(build)
    if not built:
        complete_remediation(remediation["id"], {"status": "failed", "error": "docker image build failed", "log": build_log})
        return {"deployed": False, "reason": "Docker image build failed", "log": build_log, "remediation_id": remediation["id"]}

    started, staging_name, run_err = run_staging_container(build)
    if not started:
        complete_remediation(remediation["id"], {"status": "failed", "error": f"failed to start staging container: {run_err}"})
        return {"deployed": False, "reason": f"Failed to start staging container: {run_err}", "remediation_id": remediation["id"]}

    verification = verify_deployment(staging_name)

    if not verification["healthy"]:
        _docker("rm", "-f", staging_name)
        complete_remediation(remediation["id"], {"status": "failed", "error": verification.get("reason")})
        return {
            "deployed": False,
            "reason": verification.get("reason"),
            "verification": verification,
            "remediation_id": remediation["id"],
        }

    production_name = _promote_staging_to_production(build)
    port = _assigned_port(production_name)

    complete_remediation(remediation["id"], {"status": "success", "container": production_name, "port": port})

    return {
        "deployed": True,
        "container": production_name,
        "port": port,
        "verification": verification,
        "remediation_id": remediation["id"],
    }
