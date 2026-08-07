"""Repository registry backend — persistence via the memory system.

All writes go through ``core.memory.update()``'s fcntl.flock critical
section, so concurrent scheduler ticks and dashboard reads never lose
each other's changes."""

from datetime import datetime, timezone

from core.memory import load, save, update
from core.repo_registry.models import REGISTRY_MEMORY_FILE, repo_identity_key


def _load_identity_map():
    """Return a stable ``{(platform, identity_key): index}`` map for fast
    deduplication lookups without a linear scan of the whole list."""
    data = load(REGISTRY_MEMORY_FILE)
    records = data.get("records", []) if isinstance(data, dict) else data
    identity_map = {}
    for i, repo in enumerate(records):
        identity_map[repo_identity_key(repo)] = i
    return identity_map, records


def load_registry():
    records = load(REGISTRY_MEMORY_FILE)
    if isinstance(records, dict):
        return records.get("records", [])
    return records


def save_registry(records):
    save(REGISTRY_MEMORY_FILE, records)


def upsert_repository(repo):
    """Insert or update a single repository in the registry.

    Identity is based on ``repo_identity_key()`` — same platform+full_name
    or same local_path means it is the same repository. On match the
    existing entry is replaced; otherwise a new entry is appended.

    Returns:
        int: The index of the stored entry.
    """
    key = repo_identity_key(repo)
    now = datetime.now(timezone.utc).isoformat()

    def mutate(data):
        data = data if isinstance(data, list) else data.get("records", [])
        for i, existing in enumerate(data):
            if repo_identity_key(existing) == key:
                merged = {**existing, **repo, "updated_at": now, "last_synced": now}
                merged["created_at"] = existing.get("created_at", now)
                data[i] = merged
                return data
        repo["updated_at"] = now
        repo["last_synced"] = now
        if not repo.get("created_at"):
            repo["created_at"] = now
        data.append(repo)
        return data

    update(REGISTRY_MEMORY_FILE, mutate)
    identity_map, records = _load_identity_map()
    return identity_map.get(key, -1)


def get_repository(key):
    """Get a repository by its identity key ``(platform, identity)``.

    Args:
        key: ``("github", "owner/repo")`` or ``("local", "/path/to/repo")``.

    Returns:
        dict or None
    """
    identity_map, records = _load_identity_map()
    idx = identity_map.get(key)
    if idx is not None and 0 <= idx < len(records):
        return records[idx]
    return None


def list_repositories():
    return load_registry()


def list_by_platform(platform):
    return [r for r in load_registry() if r.get("platform") == platform]


def list_local_repositories():
    return list_by_platform("local")


def remove_repository(key):
    """Remove a repository by its identity key."""
    identity_map, _ = _load_identity_map()
    idx = identity_map.get(key)
    if idx is None:
        return False

    def mutate(data):
        data = data if isinstance(data, list) else data.get("records", [])
        for i, existing in enumerate(data):
            if repo_identity_key(existing) == key:
                data.pop(i)
                return data
        return data

    update(REGISTRY_MEMORY_FILE, mutate)
    return True


def get_registry_stats():
    records = load_registry()
    platforms = {}
    for r in records:
        p = r.get("platform", "unknown")
        platforms[p] = platforms.get(p, 0) + 1
    return {
        "total": len(records),
        "by_platform": platforms,
        "last_synced": max(
            (r.get("last_synced", "") for r in records), default=""
        ),
    }
