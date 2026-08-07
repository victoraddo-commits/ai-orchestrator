"""GitLab adapter — REST API v4 with token auth."""

import os

import httpx

from core.repo_registry.adapters.base import GitPlatformAdapter
from core.repo_registry.models import new_repository


DEFAULT_GITLAB_API = "https://gitlab.com/api/v4"


def _get_token():
    return os.getenv("GITLAB_TOKEN") or os.getenv("GITLAB_API_TOKEN")


def _get_base_url():
    return os.getenv("GITLAB_API_URL", DEFAULT_GITLAB_API).rstrip("/")


class GitLabAdapter(GitPlatformAdapter):

    platform_name = "gitlab"

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
        return {"PRIVATE-TOKEN": self._token, "User-Agent": "kai-software-factory"}

    def _normalize(self, gl_repo):
        return new_repository(
            platform="gitlab",
            name=gl_repo.get("path", ""),
            full_name=gl_repo.get("path_with_namespace", ""),
            description=gl_repo.get("description") or "",
            url=gl_repo.get("web_url", ""),
            clone_url=gl_repo.get("http_url_to_repo", ""),
            owner=(gl_repo.get("namespace") or {}).get("path", ""),
            default_branch=gl_repo.get("default_branch", "main"),
            language=gl_repo.get("language") or "",
            topics=gl_repo.get("topics") or [],
            stars=gl_repo.get("star_count", 0),
            forks=gl_repo.get("forks_count", 0),
            last_pushed=gl_repo.get("last_activity_at", ""),
            archived=gl_repo.get("archived", False),
            fork="forked_from_project" in gl_repo,
            private=gl_repo.get("visibility", "private") != "public",
        )

    def fetch_repositories(self, owner=None):
        repos = []
        page = 1
        per_page = 100

        while True:
            params = {"per_page": per_page, "page": page, "membership": "true", "order_by": "updated_at", "sort": "desc"}
            if owner:
                url = f"{self._base_url}/users/{owner}/projects"
            else:
                url = f"{self._base_url}/projects"
                params["owned"] = "true"

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
        encoded = f"{owner}/{repo_name}".replace("/", "%2F")
        url = f"{self._base_url}/projects/{encoded}"
        r = httpx.get(url, headers=self._headers(), timeout=self._timeout)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return self._normalize(r.json())
