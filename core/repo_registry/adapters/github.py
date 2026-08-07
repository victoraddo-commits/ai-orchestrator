"""GitHub adapter — REST API v3 with token auth."""

import os

import httpx

from core.repo_registry.adapters.base import GitPlatformAdapter
from core.repo_registry.models import new_repository


DEFAULT_GITHUB_API = "https://api.github.com"


def _get_token():
    return os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")


def _get_base_url():
    return os.getenv("GITHUB_API_URL", DEFAULT_GITHUB_API).rstrip("/")


class GitHubAdapter(GitPlatformAdapter):

    platform_name = "github"

    def __init__(self, token=None, base_url=None, timeout=30):
        self._token = token or _get_token()
        self._base_url = (base_url or _get_base_url()).rstrip("/")
        self._timeout = timeout

    def is_available(self):
        if not self._token:
            return False
        try:
            r = httpx.get(
                f"{self._base_url}/rate_limit",
                headers=self._headers(),
                timeout=min(self._timeout, 10),
            )
            return r.status_code == 200
        except httpx.RequestError:
            return False

    def _headers(self):
        return {
            "Authorization": f"token {self._token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "kai-software-factory",
        }

    def _normalize(self, gh_repo):
        return new_repository(
            platform="github",
            name=gh_repo.get("name", ""),
            full_name=gh_repo.get("full_name", ""),
            description=gh_repo.get("description") or "",
            url=gh_repo.get("html_url", ""),
            clone_url=gh_repo.get("clone_url", ""),
            owner=(gh_repo.get("owner") or {}).get("login", ""),
            default_branch=gh_repo.get("default_branch", "main"),
            language=gh_repo.get("language") or "",
            topics=gh_repo.get("topics") or [],
            stars=gh_repo.get("stargazers_count", 0),
            forks=gh_repo.get("forks_count", 0),
            last_pushed=gh_repo.get("pushed_at", ""),
            archived=gh_repo.get("archived", False),
            fork=gh_repo.get("fork", False),
            private=gh_repo.get("private", True),
        )

    def fetch_repositories(self, owner=None):
        repos = []
        page = 1
        per_page = 100

        while True:
            url = f"{self._base_url}/user/repos"
            params = {"per_page": per_page, "page": page, "affiliation": "owner,organization_member", "sort": "updated"}
            if owner:
                url = f"{self._base_url}/users/{owner}/repos"
                params = {"per_page": per_page, "page": page, "sort": "updated"}

            r = httpx.get(url, headers=self._headers(), params=params, timeout=self._timeout)
            r.raise_for_status()
            data = r.json()

            if not data:
                break

            repos.extend(self._normalize(item) for item in data)

            if len(data) < per_page:
                break
            page += 1

        return repos

    def fetch_repository(self, owner, repo_name):
        url = f"{self._base_url}/repos/{owner}/{repo_name}"
        r = httpx.get(url, headers=self._headers(), timeout=self._timeout)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return self._normalize(r.json())
