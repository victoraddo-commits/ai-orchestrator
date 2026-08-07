"""Local repository discovery engine.

Scans configured directories for ``.git`` directories, extracts
metadata via ``git`` CLI, and normalises results into the unified
repository schema."""

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from core.repo_registry.models import new_repository


DEFAULT_SCAN_PATHS = "/opt/repos:/project/src"


def _scan_paths():
    env_val = os.getenv("REPO_SCAN_PATHS", DEFAULT_SCAN_PATHS)
    return [p.strip() for p in env_val.split(":") if p.strip()]


def _find_git_roots(search_path):
    """Walk ``search_path`` and yield one Path per ``.git`` directory found.

    Only yields the directory that *contains* ``.git``, i.e. the repo root.
    Skips common virtual-environment and cache directories.
    """
    skip = {".venv", "__pycache__", "node_modules", ".git", "dist", "build", ".cache"}
    root = Path(search_path)
    if not root.is_dir():
        return

    for dirpath, dirnames, _ in os.walk(str(root), followlinks=False):
        head = os.path.basename(dirpath)
        if head in skip:
            dirnames.clear()
            continue

        for d in list(dirnames):
            if d in skip or d.startswith(".") and d != ".git":
                dirnames.remove(d)

        if (Path(dirpath) / ".git").is_dir():
            yield Path(dirpath)
            dirnames.clear()


def _run_git(repo_root, *args):
    try:
        r = subprocess.run(
            ["git", "-C", str(repo_root)] + list(args),
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip() or r.stderr.strip()
    except (subprocess.TimeoutExpired, OSError):
        return ""


def _extract_metadata(git_root):
    """Extract repository metadata from a local git directory."""
    remotes_raw = _run_git(git_root, "remote", "-v")
    clone_url = _extract_origin_url(remotes_raw)

    full_name = git_root.name
    owner = "local"

    if clone_url:
        parsed = _parse_git_url_parts(clone_url)
        if parsed:
            full_name = parsed["full_name"]
            owner = parsed["owner"]

    return new_repository(
        platform="local",
        name=git_root.name,
        full_name=full_name,
        description="",
        url="",
        clone_url=clone_url,
        owner=owner,
        local_path=str(git_root.resolve()),
        default_branch=_run_git(git_root, "rev-parse", "--abbrev-ref", "HEAD") or "main",
        language="",
        topics=[],
        stars=0,
        forks=0,
        last_pushed=_get_last_commit_date(git_root),
        archived=False,
        fork=False,
        private=True,
    )


def _extract_origin_url(remotes_output):
    """Extract the origin URL from ``git remote -v`` output."""
    for line in remotes_output.splitlines():
        if line.startswith("origin\t") or line.startswith("origin "):
            parts = line.split()
            if len(parts) >= 2:
                return parts[1]
    return ""


def _parse_git_url_parts(url):
    """Extract owner/repo from a git remote URL.

    Handles:
        https://github.com/owner/repo.git
        git@github.com:owner/repo.git
        https://gitlab.com/owner/repo
        https://gitea.example.com/owner/repo.git
    """
    url = url.removesuffix(".git")
    if "@" in url and ":" in url:
        _, _, tail = url.partition("@")
        _, _, tail = tail.partition(":")
        parts = tail.strip("/").split("/")
    else:
        parts = url.split("/")
        if len(parts) >= 5:
            parts = parts[-2:]
        elif len(parts) >= 2:
            parts = parts[-2:]
        else:
            return None

    if len(parts) >= 2:
        return {"owner": parts[-2], "name": parts[-1], "full_name": f"{parts[-2]}/{parts[-1]}"}
    return None


def _get_last_commit_date(git_root):
    try:
        r = subprocess.run(
            ["git", "-C", str(git_root), "log", "-1", "--format=%cI"],
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        return ""


def discover_local():
    """Walk all configured scan paths and discover local git repositories.

    Returns:
        list[dict]: Normalised repository entries.
    """
    seen = set()
    repos = []

    for search_path in _scan_paths():
        for repo_root in _find_git_roots(search_path):
            resolved = str(repo_root.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            repos.append(_extract_metadata(repo_root))

    return repos
