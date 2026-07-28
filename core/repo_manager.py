import os
import subprocess


def _run(args, cwd):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)


def _is_git_repo(path):
    return _run(["rev-parse", "--is-inside-work-tree"], cwd=path).returncode == 0


def _has_commits(path):
    return _run(["rev-parse", "HEAD"], cwd=path).returncode == 0


def _current_branch(path):
    result = _run(["branch", "--show-current"], cwd=path)
    return result.stdout.strip() or None


def _branch_exists(path, branch):
    return _run(["rev-parse", "--verify", branch], cwd=path).returncode == 0


def _checkout_branch(path, branch):
    if _branch_exists(path, branch):
        _run(["checkout", "-q", branch], cwd=path)
    else:
        _run(["checkout", "-q", "-b", branch], cwd=path)


def create_local_repo(path, branch=None):
    """Ensures a git repository exists on disk at `path`, ready for
    core.coding_bridge.run_coding_task() to work in. Idempotent and
    non-destructive: an already-initialized repo (or existing files in a
    plain directory) is never touched beyond what's explicitly requested
    (a branch checkout), since this runs automatically from the scheduler
    and must never risk losing someone's existing work."""

    created_directory = not os.path.exists(path)
    os.makedirs(path, exist_ok=True)

    initialized_git = False
    if not _is_git_repo(path):
        _run(["init", "-q"], cwd=path)
        initialized_git = True

    initial_commit_made = False
    if not _has_commits(path):
        _run(["add", "-A"], cwd=path)
        _run(["commit", "-q", "--allow-empty", "-m", "Initial commit"], cwd=path)
        initial_commit_made = True

    if branch:
        _checkout_branch(path, branch)

    return {
        "path": path,
        "created_directory": created_directory,
        "initialized_git": initialized_git,
        "initial_commit_made": initial_commit_made,
        "branch": _current_branch(path),
    }
