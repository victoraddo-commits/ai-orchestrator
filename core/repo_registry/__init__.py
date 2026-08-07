from core.repo_registry.models import new_repository, repo_identity_key, VALID_PLATFORMS
from core.repo_registry.registry import (
    load_registry,
    save_registry,
    upsert_repository,
    get_repository,
    list_repositories,
    list_by_platform,
    list_local_repositories,
    remove_repository,
    get_registry_stats,
)
from core.repo_registry.sync_engine import sync_all, sync_platform, discover_local

__all__ = [
    "RepositorySchema",
    "new_repository",
    "load_registry",
    "save_registry",
    "upsert_repository",
    "get_repository",
    "list_repositories",
    "list_by_platform",
    "list_local_repositories",
    "remove_repository",
    "get_registry_stats",
    "sync_all",
    "sync_platform",
    "discover_local",
]
