import os
import re
import subprocess
import time
import urllib.error
import urllib.request

from core.remediation import (
    create_remediation,
    start_remediation,
    complete_remediation,
    register_rollback,
)


DEPLOY_ACTION = "deploy_build"


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
    branch = f"build-{build['id']}"
    targets = _self_modifying_merge_targets(build)

    merged = []  # (live_repo, pre_merge_head) -- for rollback on a later failure
    repo_merge_commits = {}

    for live_repo, clone_path in targets:
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
            }

        merged.append((live_repo, pre_merge_head))
        repo_merge_commits[live_repo] = result["merge_commit"]

    return {
        "deployed": True,
        "merged_branch": branch,
        # Backward-compatible single value: the primary (orchestrator)
        # repo's merge commit, exactly what this key meant before 13Q.
        "merge_commit": repo_merge_commits[targets[0][0]],
        "merged_repos": repo_merge_commits,
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
