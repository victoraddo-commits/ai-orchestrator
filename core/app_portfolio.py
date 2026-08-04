"""Phase 17I: Application portfolio awareness — Kai learns what each of your
other apps is and can propose maintenance for them.

Scans /project/src/ for applications, detects their type, tech stack, and
maintenance needs. Proposes upgrades, dependency updates, and security patches.
"""

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path


SRC_DIR = Path("/project/src")
IGNORE_DIRS = {"node_modules", "__pycache__", ".venv", ".git", "dist", "build"}


def _detect_type(app_dir):
    """Detect the project type from common files."""
    files = set(os.listdir(app_dir))
    types = []

    if "package.json" in files:
        types.append("nodejs")
    if "requirements.txt" in files or "setup.py" in files:
        types.append("python")
    if "go.mod" in files:
        types.append("go")
    if "Cargo.toml" in files:
        types.append("rust")
    if "Dockerfile" in files:
        types.append("docker")
    if "docker-compose.yml" in files:
        types.append("docker-compose")
    if "tsconfig.json" in files:
        types.append("typescript")
    if "vite.config" in str(files):
        types.append("vite")
    if "index.html" in files:
        types.append("web")
    if "bot.py" in files:
        types.append("telegram-bot")

    return types or ["unknown"]


def _check_git(app_dir):
    """Check git status for an application."""
    try:
        result = subprocess.run(
            ["git", "-C", str(app_dir), "log", "--oneline", "-5"],
            capture_output=True, text=True, timeout=10,
        )
        commits = result.stdout.strip().split("\n") if result.stdout.strip() else []

        # Check for unpushed commits
        result2 = subprocess.run(
            ["git", "-C", str(app_dir), "log", "--oneline", "origin/main..HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        unpushed = result2.stdout.strip().split("\n") if result2.stdout.strip() else []

        return {
            "recent_commits": [c[:80] for c in commits[:3]],
            "unpushed_commits": len(unpushed),
        }
    except Exception:
        return {"recent_commits": [], "unpushed_commits": 0}


def _check_dependencies(app_dir, app_type):
    """Check for outdated dependencies."""
    issues = []

    if "nodejs" in app_type or "typescript" in app_type:
        pkg = app_dir / "package.json"
        if pkg.exists():
            try:
                result = subprocess.run(
                    ["npm", "outdated", "--json"],
                    capture_output=True, text=True, timeout=30,
                    cwd=str(app_dir),
                )
                if result.stdout.strip():
                    outdated = json.loads(result.stdout)
                    count = len(outdated)
                    if count > 5:
                        issues.append(f"{count} npm packages outdated")
            except Exception:
                pass

    if "python" in app_type:
        req = app_dir / "requirements.txt"
        if req.exists():
            contents = req.read_text()
            if "==" in contents:
                issues.append("pinned Python dependencies (consider >= for minor updates)")

    return issues


def discover_apps():
    """Scan /project/src/ and build the application portfolio."""

    apps = {}
    if not SRC_DIR.is_dir():
        return apps

    for entry in sorted(SRC_DIR.iterdir()):
        if not entry.is_dir() or entry.name.startswith(".") or entry.name in IGNORE_DIRS:
            continue

        # Skip non-project directories (no recognizable project files)
        contents = list(entry.iterdir())
        if not contents:
            continue

        app_type = _detect_type(entry)
        git_info = _check_git(entry)
        deps_issues = _check_dependencies(entry, app_type)

        # Skip if it doesn't look like a project
        if app_type == ["unknown"] and not git_info.get("recent_commits"):
            continue

        apps[entry.name] = {
            "name": entry.name,
            "path": str(entry),
            "type": app_type,
            **git_info,
            "maintenance": deps_issues,
        }

    return apps


def propose_maintenance():
    """Propose maintenance actions across the portfolio."""
    apps = discover_apps()
    proposals = []

    for name, info in apps.items():
        if info.get("unpushed_commits", 0) > 0:
            proposals.append({
                "app": name,
                "action": "push_changes",
                "detail": f"{info['unpushed_commits']} unpushed commits",
            })
        if info.get("maintenance"):
            proposals.append({
                "app": name,
                "action": "update_deps",
                "detail": "; ".join(info["maintenance"]),
            })

    return proposals


def get_portfolio_report():
    """Full portfolio report for the dashboard."""
    return {
        "discovered_at": datetime.now(timezone.utc).isoformat(),
        "apps": discover_apps(),
        "maintenance_proposals": propose_maintenance(),
    }
