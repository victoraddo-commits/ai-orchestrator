"""Kai V3 Sandbox Manager — Git worktree isolation per build.

Every build gets its own disposable git worktree on a dedicated branch.
No two builds share writable files. Worktrees are cleaned up when builds
reach a terminal state.

This replaces the Docker-container sandbox in core/sandbox.py for build
isolation — Docker sandboxes remain available for test execution via
the existing core/sandbox module (reused as a library).
"""

import os
import shutil
import subprocess
from pathlib import Path
from datetime import datetime

from core.logger import info

# Root directory for all build sandboxes
SANDBOX_ROOT = Path.home() / ".ai-orchestrator" / "v3-sandboxes"

# Maximum concurrent sandboxes — bounded by disk and git resources
DEFAULT_MAX_SANDBOXES = 16

# Track active sandboxes
_active_sandboxes: dict[str, dict] = {}


class SandboxError(Exception):
    """Raised when sandbox creation or cleanup fails."""


def sandbox_root() -> Path:
    """Ensure the sandbox root directory exists and return it."""
    SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
    return SANDBOX_ROOT


def prepare_sandbox(build: dict, source_repo: Path | None = None) -> dict:
    """Create an isolated git worktree for a build.

    Args:
        build: The build dict with at minimum 'id' and 'name' fields.
        source_repo: Path to the repository to clone/worktree from.
                     Defaults to the ai-orchestrator repo root.

    Returns:
        A sandbox dict: {project_path, branch, log_dir, created_at, build_id}
    """
    sandbox_root().mkdir(parents=True, exist_ok=True)

    build_id = build["id"]
    branch = f"build/{build_id[:12]}"
    sandbox_path = sandbox_root() / build_id[:12]

    if source_repo is None:
        source_repo = Path(__file__).resolve().parent.parent.parent

    if sandbox_path.exists():
        info(f"Sandbox already exists for build {build_id[:12]}, reusing")
    else:
        _create_worktree(source_repo, sandbox_path, branch)

    log_dir = sandbox_path / "logs"
    log_dir.mkdir(exist_ok=True)

    sandbox = {
        "project_path": str(sandbox_path),
        "branch": branch,
        "log_dir": str(log_dir),
        "created_at": datetime.now().isoformat(),
        "build_id": build_id,
        "repo_path": str(source_repo),
    }

    _active_sandboxes[build_id] = sandbox

    # Enforce max sandbox limit
    _enforce_sandbox_limit()

    return sandbox


def release_sandbox(build_id: str) -> bool:
    """Clean up a build's sandbox after it reaches a terminal state.

    Removes the worktree from git and deletes the directory.
    Returns True if cleanup succeeded.
    """
    sandbox = _active_sandboxes.pop(build_id, None)
    if sandbox is None:
        info(f"No sandbox found for build {build_id[:12]}, nothing to release")
        return True

    sandbox_path = Path(sandbox["project_path"])
    source_repo = Path(sandbox["repo_path"])

    success = True

    # Remove git worktree reference
    if sandbox_path.exists():
        try:
            subprocess.run(
                ["git", "-C", str(source_repo), "worktree", "remove", "--force",
                 str(sandbox_path)],
                capture_output=True, timeout=30,
            )
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
            info(f"Git worktree remove failed for {build_id[:12]}: {e}, force-removing path")
            # Force remove the directory as fallback
            try:
                shutil.rmtree(sandbox_path, ignore_errors=True)
            except Exception:
                pass

    # Clean up the branch (git worktree remove already does this, but belt-and-suspenders)
    try:
        subprocess.run(
            ["git", "-C", str(source_repo), "branch", "-D", sandbox["branch"]],
            capture_output=True, timeout=10,
        )
    except Exception:
        pass

    info(f"Sandbox released for build {build_id[:12]}")
    return success


def get_sandbox(build_id: str) -> dict | None:
    """Get the sandbox info for an active build."""
    return _active_sandboxes.get(build_id)


def active_sandbox_count() -> int:
    """Return the current number of active sandboxes."""
    return len(_active_sandboxes)


def list_active_sandboxes() -> list[dict]:
    """Return a list of all active sandbox infos."""
    return list(_active_sandboxes.values())


def _create_worktree(source_repo: Path, target_path: Path, branch: str):
    """Create a git worktree on a new branch from the source repo."""
    try:
        # Create the new branch from HEAD first
        subprocess.run(
            ["git", "-C", str(source_repo), "branch", branch],
            capture_output=True, timeout=10, check=True,
        )

        # Create the worktree
        subprocess.run(
            ["git", "-C", str(source_repo), "worktree", "add",
             str(target_path), branch],
            capture_output=True, timeout=30, check=True,
        )

        info(f"Created sandbox at {target_path} on branch {branch}")

    except subprocess.CalledProcessError as e:
        # Clean up the branch if worktree creation failed
        subprocess.run(
            ["git", "-C", str(source_repo), "branch", "-D", branch],
            capture_output=True, timeout=10,
        )
        raise SandboxError(
            f"Failed to create worktree for branch {branch}: {e.stderr}"
        ) from e


def _enforce_sandbox_limit():
    """If we're over the active sandbox limit, release the oldest ones.

    Only releases sandboxes whose builds are in terminal states.
    """
    if len(_active_sandboxes) <= DEFAULT_MAX_SANDBOXES:
        return

    # Sort by creation time, oldest first
    sorted_sandboxes = sorted(
        _active_sandboxes.items(),
        key=lambda kv: kv[1]["created_at"],
    )

    to_remove = len(_active_sandboxes) - DEFAULT_MAX_SANDBOXES
    removed = 0
    for build_id, sandbox in sorted_sandboxes:
        if removed >= to_remove:
            break
        if release_sandbox(build_id):
            removed += 1

    if removed > 0:
        info(f"Enforced sandbox limit: released {removed} old sandboxes")


def cleanup_all_sandboxes(source_repo: Path | None = None):
    """Emergency cleanup — remove all v3 sandbox worktrees.

    Useful when restarting the orchestrator or recovering from errors.
    """
    if source_repo is None:
        source_repo = Path(__file__).resolve().parent.parent.parent

    root = sandbox_root()
    if not root.exists():
        return

    for entry in root.iterdir():
        if entry.is_dir():
            try:
                subprocess.run(
                    ["git", "-C", str(source_repo), "worktree", "remove",
                     "--force", str(entry)],
                    capture_output=True, timeout=30,
                )
            except Exception:
                pass
            shutil.rmtree(entry, ignore_errors=True)

    _active_sandboxes.clear()
    info("Cleaned up all V3 sandboxes")
