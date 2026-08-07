"""Repository Registry data models and validation.

Schema versioned alongside the memory system. Each repository entry
stores enough metadata to power the dashboard's search/filter views
without requiring a live API call on every render."""

from datetime import datetime, timezone


REGISTRY_MEMORY_FILE = "repo_registry.json"

VALID_PLATFORMS = {"github", "gitlab", "gitea", "local"}

REPO_KEYS = {
    "id",
    "name",
    "full_name",
    "description",
    "url",
    "clone_url",
    "platform",
    "owner",
    "local_path",
    "default_branch",
    "language",
    "topics",
    "stars",
    "forks",
    "last_pushed",
    "last_synced",
    "created_at",
    "updated_at",
    "archived",
    "fork",
    "private",
}

REPO_ID_FIELDS = {
    "github": ("full_name", "url"),
    "gitlab": ("full_name", "url"),
    "gitea": ("full_name", "url"),
    "local": ("local_path",),
}


def new_repository(
    platform,
    name="",
    full_name="",
    description="",
    url="",
    clone_url="",
    owner="",
    local_path="",
    default_branch="main",
    language="",
    topics=None,
    stars=0,
    forks=0,
    last_pushed="",
    archived=False,
    fork=False,
    private=True,
):
    if platform not in VALID_PLATFORMS:
        raise ValueError(f"platform must be one of {sorted(VALID_PLATFORMS)}, got {platform!r}")

    now = datetime.now(timezone.utc).isoformat()
    return {
        "name": name,
        "full_name": full_name,
        "description": description,
        "url": url,
        "clone_url": clone_url,
        "platform": platform,
        "owner": owner,
        "local_path": local_path,
        "default_branch": default_branch,
        "language": language,
        "topics": topics or [],
        "stars": stars,
        "forks": forks,
        "last_pushed": last_pushed,
        "last_synced": now,
        "created_at": now,
        "updated_at": now,
        "archived": archived,
        "fork": fork,
        "private": private,
    }


def repo_identity_key(repo):
    """Return a stable identity key for deduplication across syncs.

    For remote repos the (platform, full_name) pair is unique (e.g.
    ``("github", "owner/repo")``). For local repos the local_path is
    the only guarantee of uniqueness."""
    platform = repo.get("platform", "")
    if platform == "local":
        return ("local", repo.get("local_path", ""))
    return (platform, repo.get("full_name", ""))
