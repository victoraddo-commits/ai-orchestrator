"""Phase 19S: Repository Registry tests.

Tests cover:
- Data models (new_repository, repo_identity_key)
- Local discovery engine (scan directories, extract metadata)
- Registry backend (CRUD via memory system)
- Sync engine (platform sync, deduplication)
- API endpoints (list, stats, trigger sync)
- Orchestrator cycle integration (safe sync call)
"""

import json
import os
import subprocess

import pytest
import httpx

from core.repo_registry.models import (
    new_repository,
    repo_identity_key,
    VALID_PLATFORMS,
)
from core.repo_registry.registry import (
    load_registry,
    upsert_repository,
    get_repository,
    list_repositories,
    list_by_platform,
    list_local_repositories,
    remove_repository,
    get_registry_stats,
)
from core.repo_registry.local_discovery import (
    _find_git_roots,
    _extract_metadata,
    _extract_origin_url,
    _parse_git_url_parts,
    discover_local,
)
from core.repo_registry.adapters import get_adapter
from core.repo_registry.sync_engine import sync_all, sync_platform


# ── Data Model Tests ─────────────────────────────────────────────────────


class TestNewRepository:
    def test_creates_valid_repo_with_minimal_fields(self):
        repo = new_repository(platform="github", name="test-repo")
        assert repo["platform"] == "github"
        assert repo["name"] == "test-repo"
        assert repo["topics"] == []
        assert "last_synced" in repo
        assert "created_at" in repo

    def test_raises_on_invalid_platform(self):
        with pytest.raises(ValueError, match="platform must be one of"):
            new_repository(platform="bitbucket")

    def test_accepts_all_valid_platforms(self):
        for p in VALID_PLATFORMS:
            repo = new_repository(platform=p)
            assert repo["platform"] == p

    def test_sets_all_fields_when_provided(self):
        repo = new_repository(
            platform="github",
            name="kai",
            full_name="owner/kai",
            description="The orchestrator",
            url="https://github.com/owner/kai",
            clone_url="https://github.com/owner/kai.git",
            owner="owner",
            local_path="",
            default_branch="develop",
            language="Python",
            topics=["ai", "ops"],
            stars=42,
            forks=3,
            last_pushed="2026-08-01T00:00:00Z",
            archived=False,
            fork=False,
            private=False,
        )
        assert repo["name"] == "kai"
        assert repo["full_name"] == "owner/kai"
        assert repo["description"] == "The orchestrator"
        assert repo["url"] == "https://github.com/owner/kai"
        assert repo["clone_url"] == "https://github.com/owner/kai.git"
        assert repo["owner"] == "owner"
        assert repo["default_branch"] == "develop"
        assert repo["language"] == "Python"
        assert repo["topics"] == ["ai", "ops"]
        assert repo["stars"] == 42
        assert repo["forks"] == 3
        assert repo["private"] is False


class TestRepoIdentityKey:
    def test_remote_repo_identity_is_platform_plus_full_name(self):
        repo = new_repository(platform="github", full_name="owner/repo")
        assert repo_identity_key(repo) == ("github", "owner/repo")

        repo2 = new_repository(platform="gitlab", full_name="group/repo")
        assert repo_identity_key(repo2) == ("gitlab", "group/repo")

    def test_local_repo_identity_is_local_plus_path(self):
        repo = new_repository(platform="local", local_path="/home/user/project")
        assert repo_identity_key(repo) == ("local", "/home/user/project")

    def test_different_platforms_same_name_are_different_keys(self):
        gh = new_repository(platform="github", full_name="owner/repo")
        gl = new_repository(platform="gitlab", full_name="owner/repo")
        assert repo_identity_key(gh) != repo_identity_key(gl)


# ── Local Discovery Tests ─────────────────────────────────────────────────


class TestExtractOriginUrl:
    def test_https_origin(self):
        output = "origin\thttps://github.com/user/repo.git (fetch)\norigin\thttps://github.com/user/repo.git (push)\n"
        assert _extract_origin_url(output) == "https://github.com/user/repo.git"

    def test_ssh_origin(self):
        output = "origin\tgit@github.com:user/repo.git (fetch)\norigin\tgit@github.com:user/repo.git (push)\n"
        assert _extract_origin_url(output) == "git@github.com:user/repo.git"

    def test_no_origin(self):
        assert _extract_origin_url("") == ""
        assert _extract_origin_url("upstream\thttps://example.com/foo.git (fetch)") == ""

    def test_space_separated_remote(self):
        output = "origin https://github.com/user/repo.git (fetch)"
        assert _extract_origin_url(output) == "https://github.com/user/repo.git"


class TestParseGitUrlParts:
    def test_https_github(self):
        r = _parse_git_url_parts("https://github.com/owner/repo.git")
        assert r == {"owner": "owner", "name": "repo", "full_name": "owner/repo"}

    def test_ssh_github(self):
        r = _parse_git_url_parts("git@github.com:owner/repo.git")
        assert r == {"owner": "owner", "name": "repo", "full_name": "owner/repo"}

    def test_https_gitlab(self):
        r = _parse_git_url_parts("https://gitlab.com/group/subgroup/project.git")
        assert r == {"owner": "subgroup", "name": "project", "full_name": "subgroup/project"}

    def test_gitea_custom_domain(self):
        r = _parse_git_url_parts("https://git.example.com/user/myrepo.git")
        assert r == {"owner": "user", "name": "myrepo", "full_name": "user/myrepo"}

    def test_no_dot_git_suffix(self):
        r = _parse_git_url_parts("https://github.com/owner/repo")
        assert r == {"owner": "owner", "name": "repo", "full_name": "owner/repo"}


class TestFindGitRoots:
    def test_finds_git_dir_in_root(self, tmp_path):
        git_dir = tmp_path / "my-project"
        git_dir.mkdir()
        (git_dir / ".git").mkdir()
        roots = list(_find_git_roots(str(tmp_path)))
        assert git_dir in roots

    def test_finds_multiple_git_dirs(self, tmp_path):
        for name in ("proj-a", "proj-b", "proj-c"):
            d = tmp_path / name
            d.mkdir()
            (d / ".git").mkdir()

        roots = list(_find_git_roots(str(tmp_path)))
        assert len(roots) == 3

    def test_skips_non_git_dirs(self, tmp_path):
        (tmp_path / "just-a-folder").mkdir()
        roots = list(_find_git_roots(str(tmp_path)))
        assert len(roots) == 0

    def test_skips_venv_and_node_modules(self, tmp_path):
        d = tmp_path / "project"
        d.mkdir()
        (d / ".git").mkdir()
        (d / ".venv").mkdir()
        (d / "node_modules").mkdir()
        (d / ".venv" / "submodule").mkdir()
        (d / ".venv" / "submodule" / ".git").mkdir()

        roots = list(_find_git_roots(str(tmp_path)))
        assert len(roots) == 1
        assert roots[0] == d

    def test_nonexistent_path_returns_empty(self, tmp_path):
        roots = list(_find_git_roots(str(tmp_path / "does-not-exist")))
        assert len(roots) == 0


class TestDiscoverLocal:
    def test_discovers_git_repos_in_scan_paths(self, tmp_path, monkeypatch):
        d = tmp_path / "cool-project"
        d.mkdir()
        (d / ".git").mkdir()

        monkeypatch.setenv("REPO_SCAN_PATHS", str(tmp_path))
        repos = discover_local()
        names = [r["name"] for r in repos]
        assert "cool-project" in names

    def test_deduplicates_across_overlapping_paths(self, tmp_path, monkeypatch):
        d = tmp_path / "project-a"
        d.mkdir()
        (d / ".git").mkdir()

        monkeypatch.setenv("REPO_SCAN_PATHS", f"{tmp_path}:{d}")
        repos = discover_local()
        assert len(repos) == 1

    def test_extracts_real_git_metadata(self, tmp_path, monkeypatch):
        d = tmp_path / "myapp"
        d.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=str(d), check=True)
        (d / "README.md").write_text("# My App")
        subprocess.run(["git", "add", "README.md"], cwd=str(d), check=True)
        subprocess.run(
            ["git", "-c", "user.name=test", "-c", "user.email=test@test.com",
             "commit", "-q", "-m", "initial"], cwd=str(d), check=True,
        )

        monkeypatch.setenv("REPO_SCAN_PATHS", str(tmp_path))
        repos = discover_local()
        assert len(repos) >= 1
        repo = repos[0]
        assert repo["name"] == "myapp"
        assert repo["platform"] == "local"
        assert repo["local_path"] == str(d.resolve())
        assert repo["last_pushed"] != ""
        assert repo["default_branch"] != ""


# ── Registry Backend Tests ────────────────────────────────────────────────


class TestRegistryCRUD:
    def test_empty_registry_returns_empty_list(self):
        repos = list_repositories()
        assert repos == []

    def test_upsert_adds_new_repo(self):
        repo = new_repository(platform="github", name="hello", full_name="gh/hello")
        upsert_repository(repo)
        repos = list_repositories()
        assert len(repos) == 1
        assert repos[0]["full_name"] == "gh/hello"

    def test_upsert_updates_existing_repo(self):
        repo1 = new_repository(platform="github", name="hello", full_name="gh/hello", description="old")
        upsert_repository(repo1)

        repo2 = new_repository(platform="github", name="hello", full_name="gh/hello", description="new")
        upsert_repository(repo2)

        repos = list_repositories()
        assert len(repos) == 1
        assert repos[0]["description"] == "new"

    def test_upsert_preserves_created_at_on_update(self):
        repo1 = new_repository(platform="github", name="hello", full_name="gh/hello")
        upsert_repository(repo1)
        first = list_repositories()[0]
        created = first["created_at"]

        repo2 = new_repository(platform="github", name="hello", full_name="gh/hello", description="changed")
        upsert_repository(repo2)
        second = list_repositories()[0]
        assert second["created_at"] == created
        assert second["description"] == "changed"
        assert second["updated_at"] != created

    def test_upsert_allows_multiple_platforms_same_name(self):
        gh = new_repository(platform="github", name="x", full_name="a/x")
        gl = new_repository(platform="gitlab", name="x", full_name="b/x")
        upsert_repository(gh)
        upsert_repository(gl)
        repos = list_repositories()
        assert len(repos) == 2

    def test_get_repository_by_key(self):
        repo = new_repository(platform="github", name="target", full_name="owner/target")
        upsert_repository(repo)

        found = get_repository(("github", "owner/target"))
        assert found is not None
        assert found["full_name"] == "owner/target"

    def test_get_repository_missing_returns_none(self):
        found = get_repository(("github", "does/not-exist"))
        assert found is None

    def test_list_by_platform(self):
        upsert_repository(new_repository(platform="github", name="a", full_name="x/a"))
        upsert_repository(new_repository(platform="github", name="b", full_name="x/b"))
        upsert_repository(new_repository(platform="gitlab", name="c", full_name="x/c"))

        assert len(list_by_platform("github")) == 2
        assert len(list_by_platform("gitlab")) == 1
        assert len(list_by_platform("local")) == 0

    def test_list_local_repositories(self):
        upsert_repository(new_repository(platform="local", local_path="/a"))
        upsert_repository(new_repository(platform="github", name="x", full_name="x/x"))
        assert len(list_local_repositories()) == 1

    def test_remove_repository(self):
        repo = new_repository(platform="github", name="rm-me", full_name="x/rm-me")
        upsert_repository(repo)
        assert len(list_repositories()) == 1

        removed = remove_repository(("github", "x/rm-me"))
        assert removed is True
        assert len(list_repositories()) == 0

    def test_remove_nonexistent_returns_false(self):
        assert remove_repository(("github", "nope/nope")) is False

    def test_get_registry_stats(self):
        upsert_repository(new_repository(platform="github", name="a", full_name="x/a"))
        upsert_repository(new_repository(platform="github", name="b", full_name="x/b"))
        upsert_repository(new_repository(platform="gitlab", name="c", full_name="x/c"))

        stats = get_registry_stats()
        assert stats["total"] == 3
        assert stats["by_platform"] == {"github": 2, "gitlab": 1}


# ── Adapter Tests ─────────────────────────────────────────────────────────


class TestGetAdapter:
    def test_returns_adapter_for_valid_platforms(self):
        for p in ("github", "gitlab", "gitea"):
            adapter = get_adapter(p)
            assert adapter is not None
            assert adapter.platform_name == p

    def test_returns_none_for_unknown_platform(self):
        assert get_adapter("bitbucket") is None
        assert get_adapter("") is None

    def test_github_adapter_requires_token(self):
        adapter = get_adapter("github", token=None)
        assert adapter.platform_name == "github"
        assert adapter.is_available() is False

    def test_github_adapter_available_with_token_and_unreachable_endpoint(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
        adapter = get_adapter("github")
        assert adapter.platform_name == "github"

    def test_gitlab_adapter_requires_token(self):
        adapter = get_adapter("gitlab", token=None)
        assert adapter.is_available() is False

    def test_gitea_adapter_requires_token(self):
        adapter = get_adapter("gitea", token=None)
        assert adapter.is_available() is False

    def test_headers_contain_auth(self):
        adapter = get_adapter("github", token="ghp_test123")
        h = adapter._headers()
        assert "Authorization" in h
        assert "ghp_test123" in h["Authorization"]


# ── Sync Engine Tests ─────────────────────────────────────────────────────


class TestSyncIntegration:
    def test_sync_platform_unknown_returns_failed(self):
        result = sync_platform("bitbucket")
        assert result["platform"] == "bitbucket"
        assert result["status"] == "failed"

    def test_sync_platform_unavailable_returns_skipped(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        result = sync_platform("github")
        assert result["platform"] == "github"
        assert result["status"] == "skipped"

    def test_sync_all_runs_all_platforms_and_local(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)
        monkeypatch.delenv("GITEA_TOKEN", raising=False)
        monkeypatch.setenv("REPO_SCAN_PATHS", "")

        result = sync_all()
        assert "results" in result
        assert "local" in result
        assert len(result["results"]) == 3

        for r in result["results"]:
            assert r["platform"] in ("github", "gitlab", "gitea")
            assert r["status"] in ("skipped", "ok", "failed")


# ── Orchestrator Cycle Integration Test ───────────────────────────────────


class TestOrchestratorCycleIntegration:
    def test_safe_repo_sync_in_cycle(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)
        monkeypatch.delenv("GITEA_TOKEN", raising=False)
        monkeypatch.setenv("REPO_SCAN_PATHS", "")

        from core.orchestrator_cycle import run_cycle
        result = run_cycle()
        assert result is not None
        # Cycle must complete even if sync fails/skips
        assert "state" in result
        assert "builds" in result


# ── API Endpoint Tests ────────────────────────────────────────────────────

class TestRepoRegistryAPI:
    @pytest.fixture(autouse=True)
    def _clear_registry(self, isolated_memory):
        from core.repo_registry.registry import REGISTRY_MEMORY_FILE
        from core.memory import save
        save(REGISTRY_MEMORY_FILE, [])

    def _client(self):
        from core.api import app
        from fastapi.testclient import TestClient
        return TestClient(app)

    def test_get_all_repos_empty(self):
        client = self._client()
        r = client.get("/api/repos")
        assert r.status_code == 200
        assert r.json() == {"repos": []}

    def test_get_all_repos_with_data(self):
        upsert_repository(new_repository(platform="github", name="hello", full_name="x/hello"))
        client = self._client()
        r = client.get("/api/repos")
        assert r.status_code == 200
        assert len(r.json()["repos"]) == 1

    def test_filter_by_platform(self):
        upsert_repository(new_repository(platform="github", name="a", full_name="x/a"))
        upsert_repository(new_repository(platform="gitlab", name="b", full_name="x/b"))
        client = self._client()
        r = client.get("/api/repos?platform=github")
        assert r.status_code == 200
        assert len(r.json()["repos"]) == 1
        assert r.json()["repos"][0]["platform"] == "github"

    def test_get_local_repos(self):
        upsert_repository(new_repository(platform="local", local_path="/a"))
        upsert_repository(new_repository(platform="local", local_path="/b"))
        client = self._client()
        r = client.get("/api/repos/local")
        assert r.status_code == 200
        assert len(r.json()["repos"]) == 2

    def test_get_repo_stats(self):
        upsert_repository(new_repository(platform="github", name="a", full_name="x/a"))
        client = self._client()
        r = client.get("/api/repos/stats")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 1
        assert data["by_platform"]["github"] == 1

    def test_trigger_sync_readonly_fails(self):
        client = self._client()
        r = client.post("/api/repos/sync")
        assert r.status_code in (401, 403)
