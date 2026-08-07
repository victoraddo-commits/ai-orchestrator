"""Synchronisation engine — polls Git platforms and local filesystem.

Runs on each orchestrator cycle (300s by default). Each platform sync
is wrapped in a broad ``try/except`` so a single platform's outage
never blocks the others."""

from datetime import datetime, timezone

from core.repo_registry.adapters import get_adapter
from core.repo_registry.registry import upsert_repository
from core.repo_registry.local_discovery import discover_local
from core.repo_registry.models import repo_identity_key

REMOTE_PLATFORMS = ("github", "gitlab", "gitea")


def _safe_sync_platform(platform, adapter):
    start = datetime.now(timezone.utc).isoformat()
    try:
        repos = adapter.fetch_repositories()
        for repo in repos:
            upsert_repository(repo)
        return {"platform": platform, "status": "ok", "fetched": len(repos), "started": start}
    except Exception as e:
        return {"platform": platform, "status": "failed", "error": str(e), "started": start}


def sync_platform(platform):
    """Synchronise one platform's repositories into the registry.

    Returns:
        dict: ``{"platform": ..., "status": "ok"|"failed", "fetched": int, "error": str}``
    """
    adapter = get_adapter(platform)
    if adapter is None:
        return {"platform": platform, "status": "failed", "error": f"unknown platform {platform!r}", "fetched": 0}
    if not adapter.is_available():
        return {"platform": platform, "status": "skipped", "error": "adapter not available", "fetched": 0}
    return _safe_sync_platform(platform, adapter)


def sync_all():
    """Synchronise all configured remote platforms and local filesystem.

    Returns:
        dict: ``{"results": [...], "local": {...}, "started": str}``
    """
    results = []
    for platform in REMOTE_PLATFORMS:
        results.append(sync_platform(platform))

    local_result = {"platform": "local", "status": "ok", "fetched": 0, "started": datetime.now(timezone.utc).isoformat()}
    try:
        local_repos = discover_local()
        for repo in local_repos:
            upsert_repository(repo)
        local_result["fetched"] = len(local_repos)
    except Exception as e:
        local_result["status"] = "failed"
        local_result["error"] = str(e)

    return {
        "results": results,
        "local": local_result,
        "started": datetime.now(timezone.utc).isoformat(),
    }
