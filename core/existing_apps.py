"""Phase 17L: Ship approved changes to EXISTING applications.

Extends the gated build pipeline to deploy code to existing applications
in /project/src/ (it-manager, proxdash, portfolio, door-bridge, etc.).
Reuses the existing deployment_manager for containerized apps and adds
git-pull + restart for non-containerized projects.
"""

import json
import os
import subprocess
from pathlib import Path
from datetime import datetime, timezone


SRC_DIR = Path("/project/src")
KNOWN_APPS = {
    "it-manager": {"type": "container", "compose": "docker-compose.yml"},
    "proxdash": {"type": "container", "frontend": "proxdash-frontend", "backend": "proxdash-backend"},
    "portfolio": {"type": "container"},
    "door-bridge": {"type": "container"},
    "pulse": {"type": "container"},
    "airdrop-hunter": {"type": "container"},
    "code-server": {"type": "container"},
    "susu": {"type": "python", "entry": "telegram/bot.py"},
}


def list_apps():
    """List all known applications with their current status."""
    apps = {}
    for name, config in KNOWN_APPS.items():
        app_dir = SRC_DIR / name
        app = {
            "name": name,
            "type": config.get("type", "unknown"),
            "path": str(app_dir),
            "exists": app_dir.is_dir(),
        }

        if app_dir.is_dir():
            # Check git status
            try:
                result = subprocess.run(
                    ["git", "-C", str(app_dir), "log", "--oneline", "-3"],
                    capture_output=True, text=True, timeout=10,
                )
                app["recent_commits"] = result.stdout.strip().split("\n") if result.returncode == 0 else []
            except Exception:
                app["recent_commits"] = []

            # Check if running (container)
            if config.get("type") == "container":
                try:
                    result = subprocess.run(
                        ["docker", "ps", "--filter", f"name={name}", "--format", "{{.Status}}"],
                        capture_output=True, text=True, timeout=5,
                    )
                    app["running"] = bool(result.stdout.strip())
                    app["status"] = result.stdout.strip()[:50] if result.stdout.strip() else "not running"
                except Exception:
                    app["running"] = False
                    app["status"] = "docker unavailable"

            # Check service status for non-container
            if config.get("type") == "python":
                try:
                    result = subprocess.run(
                        ["systemctl", "is-active", f"{name}.service"],
                        capture_output=True, text=True, timeout=5,
                    )
                    app["running"] = result.stdout.strip() == "active"
                    app["status"] = result.stdout.strip()
                except Exception:
                    app["running"] = False
                    app["status"] = "unknown"

        apps[name] = app
    return apps


def deploy_to_app(app_name, source_branch, operator="auto-review"):
    """Deploy changes to an existing application.  Uses Docker Compose
    for containerized apps, git pull + restart for others."""

    config = KNOWN_APPS.get(app_name)
    if not config:
        return {"error": f"Unknown app: {app_name}"}

    app_dir = SRC_DIR / app_name
    if not app_dir.is_dir():
        return {"error": f"App directory not found: {app_dir}"}

    result = {
        "app": app_name,
        "type": config.get("type"),
        "operator": operator,
        "deployed_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        # Pull latest
        subprocess.run(
            ["git", "-C", str(app_dir), "pull", "origin", source_branch],
            capture_output=True, text=True, timeout=30,
        )

        if config.get("type") == "container":
            # Docker Compose restart
            compose_file = config.get("compose", "docker-compose.yml")
            subprocess.run(
                ["docker", "compose", "-f", str(app_dir / compose_file), "up", "-d", "--force-recreate"],
                capture_output=True, text=True, timeout=120,
            )
            result["method"] = "docker-compose up -d"
        else:
            # Systemd restart
            subprocess.run(
                ["systemctl", "restart", f"{app_name}.service"],
                capture_output=True, text=True, timeout=30,
            )
            result["method"] = "systemctl restart"

        result["success"] = True
    except Exception as e:
        result["success"] = False
        result["error"] = str(e)

    return result
