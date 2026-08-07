"""Abstract adapter interface for Git platform integrations.

Each adapter normalises its platform's API response into the unified
repository schema defined in ``models.py``."""

from abc import ABC, abstractmethod


class GitPlatformAdapter(ABC):

    @property
    @abstractmethod
    def platform_name(self):
        """Return the platform identifier (e.g. 'github', 'gitlab', 'gitea')."""

    @abstractmethod
    def is_available(self):
        """Return True when the adapter has valid credentials and
        can reach the platform's API."""

    @abstractmethod
    def fetch_repositories(self, owner=None):
        """Fetch all accessible repositories for the authenticated user
        (optionally scoped to a specific ``owner``), normalised into
        ``models.RepositorySchema`` dicts.

        Returns:
            list[dict]: Normalised repository entries.
        """

    @abstractmethod
    def fetch_repository(self, owner, repo_name):
        """Fetch a single repository's details.

        Args:
            owner: The repository owner (user or org).
            repo_name: The repository name.

        Returns:
            dict or None: Normalised repository entry, or None if not found.
        """
