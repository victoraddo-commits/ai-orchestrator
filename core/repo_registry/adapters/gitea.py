"""Gitea adapter — REST API v1 with token auth."""

import os
import urllib.parse

import httpx

from core.repo_registry.adapters.base import GitPlatformAdapter
from core.repo_registry.models import new_repository


DEFAULT_GITEA_API = "https://gitea.com/api/v1"


def _get_token():
    return os.getenv("GITEA_TOKEN") or os.getenv("GITEA_API_TOKEN")


def _get_base_url():
    return os.getenv("GITEA_API_URL", DEFAULT_GITEA_API).rstrip("/")


class GiteaAdapter(GitPlatformAdapter):

    platform_name = "gitea"

    def __init__(self, token=None, base_url=None, timeout=30):
        self._token = token or _get_token()
        self._base_url = (base_url or _get_base_url()).rstrip("/")
        self._timeout = timeout

    def is_available(self):
        if not self._token:
            return False
        try:
            r = httpx.get(
                f"{self._base_url}/version",
                headers=self._headers(),
                timeout=min(self._timeout, 10),
            )
            return 200 <= r.status_code < 300
        except httpx.RequestError:
            return False

    def _headers(self):
        return {
            "Authorization": f"token {self._token}",
            "Accept": "application/json",
            "User-Agent": "kai-software-factory",
        }

    def _normalize(self, gt_repo):
        return new_repository(
            platform="gitea",
            name=gt_repo.get("name", ""),
            full_name=gt_repo.get("full_name", ""),
            description=gt_repo.get("description") or "",
            url=gt_repo.get("html_url", ""),
            clone_url=gt_repo.get("clone_url", ""),
            owner=(gt_repo.get("owner") or {}).get("login", ""),
            default_branch=gt_repo.get("default_branch", "main"),
            language=gt_repo.get("language") or "",
            topics=gt_repo.get("topics") or [],
            stars=gt_repo.get("stars_count", 0),
            forks=gt_repo.get("forks_count", 0),
            last_pushed=gt_repo.get("updated_at", ""),
            archived=gt_repo.get("archived", False),
            fork=gt_repo.get("fork", False),
            private=gt_repo.get("private", True),
        )

    def fetch_repositories(self, owner=None):
        repos = []
        page = 1
        limit = 100

        while True:
            url = f"{self._base_url}/user/repos"
            params = {"limit": limit, "page": page}
            if owner:
                url = f"{self._base_url}/users/{urllib.parse.quote(owner, safe='')}/repos"
                params = {"limit": limit, "page": page}

            r = httpx.get(url, headers=self._headers(), params=params, timeout=self._timeout)
            r.raise_for_status()
            data = r.json()

            if not data:
                break

            repos.extend(self._normalize(item) for item in data)

            if len(data) < limit:
                break
            page += 1

        return repos

    def fetch_repository(self, owner, repo_name):
        url = f"{self._base_url}/repos/{urllib.parse.quote(owner, safe='')}/{urllib.parse.quote(repo_name, safe='')}"
        r = httpx.get(url, headers=self._headers(), timeout=self._timeout)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return self._normalize(r.json())
