from core.repo_registry.adapters.base import GitPlatformAdapter
from core.repo_registry.adapters.github import GitHubAdapter
from core.repo_registry.adapters.gitlab import GitLabAdapter
from core.repo_registry.adapters.gitea import GiteaAdapter


def get_adapter(platform, **kwargs):
    """Return an adapter instance for the given platform.

    Args:
        platform: One of 'github', 'gitlab', 'gitea'.
        **kwargs: Passed through to the adapter constructor.

    Returns:
        GitPlatformAdapter or None if the platform is not recognised.
    """
    if platform == "github":
        return GitHubAdapter(**kwargs)
    if platform == "gitlab":
        return GitLabAdapter(**kwargs)
    if platform == "gitea":
        return GiteaAdapter(**kwargs)
    return None


__all__ = ["GitPlatformAdapter", "GitHubAdapter", "GitLabAdapter", "GiteaAdapter", "get_adapter"]
