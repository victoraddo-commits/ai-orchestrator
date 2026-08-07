"""Sandbox Manager — Kai Software Factory V3.

Provides isolated execution environments for every build:

  - Separate Git worktree under ~/.ai-orchestrator/sandboxes/{build_id}/
  - Separate branch (build-{build_id})
  - Separate environment and logs
  - Prevents concurrent builds from sharing writable files

Replaces the inline clone logic previously scattered across
roadmap_manager._create_isolated_self_clone() and build_manager._ensure_repo().
"""

import os
import shutil
import subprocess
from pathlib import Path
from datetime import datetime, timezone

from core.logger import info

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SANDBOX_ROOT = Path.home() / ".ai-orchestrator" / "sandboxes"
SELF_PROJECT_PATH = Path(__file__).resolve().parent.parent
PLUGIN_PROJECT_PATH = Path("/project/src/ai-orchestrator-plugin")

# Fixed directory names inside a dual-repo sandbox
ORCHESTRATOR_CLONE_DIRNAME = "ai-orchestrator"
PLUGIN_CLONE_DIRNAME = "ai-orchestrator-plugin"


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

def get_sandbox_path(build_id: str) -> Path:
    """Return the sandbox directory path for a build."""
    return SANDBOX_ROOT / build_id


def create_sandbox(build_id: str, include_plugin: bool = False) -> str:
    """Create an isolated sandbox for a build.

    Returns the sandbox project path (str) usable as build['project_path'].

    For self-modifying builds, creates a disposable clone under the sandbox
    root. For dual-repo builds (include_plugin=True), clones both repos as
    siblings under one parent directory.
    """
    sandbox = get_sandbox_path(build_id)
    sandbox.mkdir(parents=True, exist_ok=True)
    branch = f"build-{build_id}"

    if include_plugin:
        return _create_dual_repo_sandbox(sandbox, build_id, branch)
    else:
        return _create_single_repo_sandbox(sandbox, build_id, branch)


def _create_single_repo_sandbox(sandbox: Path, build_id: str, branch: str) -> str:
    """Clone the orchestrator repo into a sandbox, create build branch."""
    orchestrator_dir = sandbox / ORCHESTRATOR_CLONE_DIRNAME

    _clone_repo(SELF_PROJECT_PATH, orchestrator_dir, branch)
    _init_branch(orchestrator_dir, branch)

    info(f"sandbox: created {build_id} at {orchestrator_dir} (branch={branch})")
    return str(orchestrator_dir)


def _create_dual_repo_sandbox(sandbox: Path, build_id: str, branch: str) -> str:
    """Clone both orchestrator and plugin repos as siblings."""
    orchestrator_dir = sandbox / ORCHESTRATOR_CLONE_DIRNAME
    plugin_dir = sandbox / PLUGIN_CLONE_DIRNAME

    # Clone orchestrator
    _clone_repo(SELF_PROJECT_PATH, orchestrator_dir, branch)
    _init_branch(orchestrator_dir, branch)

    # Clone plugin
    if PLUGIN_PROJECT_PATH.exists():
        _clone_repo(PLUGIN_PROJECT_PATH, plugin_dir, branch)
        _init_branch(plugin_dir, branch)

    info(f"sandbox: created dual-repo {build_id} at {sandbox} (branch={branch})")
    return str(sandbox)


def _clone_repo(source: Path, dest: Path, branch: str):
    """Clone source repo to dest, create build branch."""
    if not (source / ".git").exists():
        # Source is not a git repo — copy instead
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(source, dest)
        _init_branch(dest, branch)
        return

    if dest.exists():
        shutil.rmtree(dest)

    subprocess.run(
        ["git", "clone", "--quiet", str(source), str(dest)],
        capture_output=True, timeout=120,
    )


def _init_branch(repo_path: Path, branch: str):
    """Create and checkout the build branch in repo."""
    if not shutil.which("git"):
        return

    try:
        # Create branch (ignore if it already exists)
        result = subprocess.run(
            ["git", "-C", str(repo_path), "checkout", "-b", branch],
            capture_output=True, timeout=30,
        )
        if result.returncode != 0:
            # Branch may already exist — try checkout
            subprocess.run(
                ["git", "-C", str(repo_path), "checkout", branch],
                capture_output=True, timeout=30,
            )
    except Exception:
        pass


def init_git_if_needed(repo_path: str, branch: str = None):
    """Ensure a directory is a git repo with the given branch checked out.

    For non-self-modifying builds (plain directories), initializes git
    and creates the build branch.
    """
    path = Path(repo_path)

    if (path / ".git").exists():
        # Already a git repo — just ensure branch
        if branch and shutil.which("git"):
            _init_branch(path, branch)
        return

    if not shutil.which("git"):
        return

    # Initialize fresh git repo
    subprocess.run(
        ["git", "-C", str(path), "init"],
        capture_output=True, timeout=30,
    )

    # Create initial commit so branch creation works
    subprocess.run(
        ["git", "-C", str(path), "add", "-A"],
        capture_output=True, timeout=30,
    )
    subprocess.run(
        ["git", "-C", str(path), "commit", "--allow-empty", "-m", "Initial commit (sandbox)"],
        capture_output=True, timeout=30,
    )

    if branch:
        _init_branch(path, branch)


def cleanup_sandbox(build_id: str):
    """Remove a build's sandbox after completion or failure."""
    sandbox = get_sandbox_path(build_id)

    if not sandbox.exists():
        return

    try:
        shutil.rmtree(sandbox)
        info(f"sandbox: cleaned up {build_id}")
    except OSError as e:
        info(f"sandbox: cleanup {build_id} failed: {e}")


def self_build_repo_paths(project_path: str) -> list:
    """Return all actual git repo paths inside a sandbox project path.

    For a dual-repo sandbox (parent dir with ai-orchestrator + plugin
    siblings), returns both repo paths. For a single-repo sandbox,
    returns [project_path]. For non-sandbox paths, returns [project_path].
    """
    path = Path(project_path)
    orchestrator = path / ORCHESTRATOR_CLONE_DIRNAME
    plugin = path / PLUGIN_CLONE_DIRNAME

    if orchestrator.exists() and plugin.exists():
        return [str(orchestrator), str(plugin)]

    return [str(path)]


def is_dual_repo_workspace(project_path: str) -> bool:
    """Check if project_path is a dual-repo sandbox parent directory."""
    path = Path(project_path)
    return (
        (path / ORCHESTRATOR_CLONE_DIRNAME).exists() and
        (path / PLUGIN_CLONE_DIRNAME).exists()
    )


def get_build_branch(build_id: str) -> str:
    """Return the standard branch name for a build."""
    return f"build-{build_id}"


def list_sandboxes() -> list:
    """List all active sandboxes."""
    if not SANDBOX_ROOT.exists():
        return []

    result = []
    for entry in sorted(SANDBOX_ROOT.iterdir()):
        if entry.is_dir():
            size = _dir_size(entry)
            result.append({
                "build_id": entry.name,
                "path": str(entry),
                "size_bytes": size,
            })
    return result


def _dir_size(path: Path) -> int:
    """Approximate directory size in bytes."""
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                total += entry.stat().st_size
    except OSError:
        pass
    return total


def cleanup_old_sandboxes(max_age_hours: int = 24):
    """Remove sandboxes older than max_age_hours. Called periodically."""
    if not SANDBOX_ROOT.exists():
        return

    cutoff = datetime.now(timezone.utc).timestamp() - (max_age_hours * 3600)

    for entry in SANDBOX_ROOT.iterdir():
        if not entry.is_dir():
            continue
        try:
            mtime = entry.stat().st_mtime
            if mtime < cutoff:
                shutil.rmtree(entry)
                info(f"sandbox: cleaned up stale {entry.name}")
        except OSError:
            pass
