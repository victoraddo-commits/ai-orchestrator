"""Tests for the Kai Directives docs surface + completed workflow.

Two additions to the directives service (same auth as everything else):

* ``/api/directives/docs`` — list/download files under the repo ``docs/``
  folder. Path-safe: every resolved path must stay inside ``docs/``.
* ``/api/directives/{id}/complete`` — mark a directive complete **only after**
  ``verify_completion()`` passes. On success the file is copied to
  ``directives/completed/`` (with a backup kept), verified, then removed from
  the active folder. On failure the service refuses (HTTP 409) and reports the
  unmet requirements instead of silently completing.

All of it reuses the existing directives auth (Duo session, or the machine
token from loopback only).
"""
from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.directives_api as da  # noqa: E402

TOKEN = "test-machine-token"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A throwaway repo: docs/ (+ nested) and directives/ (active only)."""
    repo = tmp_path / "repo"
    docs = repo / "docs"
    (docs / "audit").mkdir(parents=True)
    (docs / "audit" / "KAI_AUDIT.md").write_text("# Audit\n\nbody\n", encoding="utf-8")
    (docs / "readme.txt").write_text("hello docs\n", encoding="utf-8")
    (docs / "empty.txt").write_text("", encoding="utf-8")

    directives = repo / "directives"
    directives.mkdir()

    monkeypatch.setattr(da, "REPO_ROOT", repo)
    monkeypatch.setattr(da, "DOCS_DIR", docs)
    monkeypatch.setattr(da, "DIR", directives)
    monkeypatch.setattr(da, "COMPLETED_DIR", directives / "completed")
    monkeypatch.setattr(da, "BACKUP_DIR", directives / ".completed-backup")
    monkeypatch.setattr(da, "META_FILE", directives / "completed" / ".meta.json")
    monkeypatch.setattr(da, "_token", lambda: TOKEN)
    return repo


@pytest.fixture
def client(env):
    return TestClient(da.app, client=("127.0.0.1", 50000))


def _auth():
    return {"X-Directive-Token": TOKEN}


def _write_directive(env, name: str, text: str) -> Path:
    path = env / "directives" / name
    path.write_text(text, encoding="utf-8")
    return path


# ── docs: auth ──────────────────────────────────────────────────────────────


def test_docs_list_requires_auth(client):
    assert client.get("/api/directives/docs").status_code == 401


def test_docs_download_requires_auth(client):
    assert client.get("/api/directives/docs/readme.txt").status_code == 401


def test_docs_list_accepts_machine_token(client):
    r = client.get("/api/directives/docs", headers=_auth())
    assert r.status_code == 200
    assert "docs" in r.json()


# ── docs: discovery + metadata ──────────────────────────────────────────────


def test_docs_list_includes_nested_files_with_metadata(client):
    rows = client.get("/api/directives/docs", headers=_auth()).json()["docs"]
    by_path = {r["path"]: r for r in rows}
    assert set(by_path) == {"audit/KAI_AUDIT.md", "readme.txt", "empty.txt"}
    row = by_path["audit/KAI_AUDIT.md"]
    assert set(row) == {"path", "name", "size", "mtime"}
    assert row["name"] == "KAI_AUDIT.md"
    assert row["size"] > 0
    assert row["mtime"].endswith("Z")


def test_docs_list_skips_symlink_escape(client, env, tmp_path):
    secret = tmp_path / "outside.txt"
    secret.write_text("secret\n", encoding="utf-8")
    (env / "docs" / "escape.txt").symlink_to(secret)
    rows = client.get("/api/directives/docs", headers=_auth()).json()["docs"]
    assert "escape.txt" not in {r["path"] for r in rows}


# ── docs: download ──────────────────────────────────────────────────────────


def test_docs_download_returns_bytes_and_attachment(client):
    r = client.get("/api/directives/docs/readme.txt", headers=_auth())
    assert r.status_code == 200
    assert r.content == b"hello docs\n"
    assert "attachment" in r.headers.get("content-disposition", "")


def test_docs_download_nested_file(client):
    r = client.get("/api/directives/docs/audit/KAI_AUDIT.md", headers=_auth())
    assert r.status_code == 200
    assert r.text.startswith("# Audit")


@pytest.mark.parametrize("rel", [
    "../readme.txt",
    "../../etc/passwd",
    "audit/../../readme.txt",
])
def test_docs_resolve_rejects_traversal(rel):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        da._resolve_doc(rel)
    assert exc.value.status_code == 404


def test_docs_resolve_rejects_absolute_path():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        da._resolve_doc("/etc/passwd")
    assert exc.value.status_code == 404


def test_docs_download_traversal_endpoint_is_404(client):
    r = client.get("/api/directives/docs/%2e%2e%2freadme.txt", headers=_auth())
    assert r.status_code == 404


def test_docs_download_symlink_escape_is_404(client, env, tmp_path):
    secret = tmp_path / "outside-secret.txt"
    secret.write_text("secret\n", encoding="utf-8")
    (env / "docs" / "escape.txt").symlink_to(secret)
    r = client.get("/api/directives/docs/escape.txt", headers=_auth())
    assert r.status_code == 404


# ── verify_completion ───────────────────────────────────────────────────────


def test_verify_refuses_unchecked_checklist(env):
    p = _write_directive(env, "unchecked.md",
                         "# Do the thing\n\n- [x] done step\n- [ ] pending step\n")
    verdict = da.verify_completion(p)
    assert verdict["passed"] is False
    assert verdict["unmet"] == 1
    assert any(c["status"] == "unmet" for c in verdict["checks"])


def test_verify_passes_when_all_checklist_checked(env):
    p = _write_directive(env, "checked.md", "# Done\n\n- [x] one\n- [X] two\n")
    verdict = da.verify_completion(p)
    assert verdict["passed"] is True
    assert verdict["unmet"] == 0


def test_verify_refuses_when_no_criteria(env):
    p = _write_directive(env, "vague.md", "# Just a vague directive\n\nDo good things.\n")
    verdict = da.verify_completion(p)
    assert verdict["passed"] is False
    assert verdict["met"] == 0
    assert "no machine-checkable" in verdict["reason"].lower()


def test_verify_file_exists_probe_passes(env):
    p = _write_directive(env, "probe.md",
                         "# Prove it\n\n```verify\nfile_exists: docs/readme.txt\n```\n")
    verdict = da.verify_completion(p)
    assert verdict["passed"] is True
    assert verdict["checks"][0]["status"] == "met"


def test_verify_file_exists_probe_missing_is_unmet(env):
    p = _write_directive(env, "probe-missing.md",
                         "# Prove it\n\n```verify\nfile_exists: docs/nope.txt\n```\n")
    verdict = da.verify_completion(p)
    assert verdict["passed"] is False
    assert verdict["checks"][0]["status"] == "unmet"


def test_verify_probe_cannot_escape_repo(env):
    p = _write_directive(env, "probe-escape.md",
                         "# Prove it\n\n```verify\nfile_exists: ../outside.txt\n```\n")
    verdict = da.verify_completion(p)
    assert verdict["passed"] is False


def test_verify_http_probe_rejects_non_loopback(env, monkeypatch):
    called = {"n": 0}

    def fake_get(url):  # pragma: no cover - must not be called
        called["n"] += 1
        return 200

    monkeypatch.setattr(da, "_http_get", fake_get)
    p = _write_directive(env, "probe-ssrf.md",
                         "# Prove it\n\n```verify\nhttp_ok: http://example.com/\n```\n")
    verdict = da.verify_completion(p)
    assert verdict["passed"] is False
    assert called["n"] == 0


def test_verify_http_probe_loopback(env, monkeypatch):
    monkeypatch.setattr(da, "_http_get", lambda url: 200)
    p = _write_directive(env, "probe-http.md",
                         "# Prove it\n\n```verify\nhttp_ok: http://127.0.0.1:8099/health\n```\n")
    assert da.verify_completion(p)["passed"] is True


# ── complete: refusal ───────────────────────────────────────────────────────


def test_complete_requires_auth(client, env):
    _write_directive(env, "x.md", "# x\n")
    assert client.post("/api/directives/x.md/complete").status_code == 401


def test_complete_refuses_unmet_and_does_not_move(client, env):
    path = _write_directive(env, "unmet.md", "# Unmet\n\n- [ ] not done\n")
    r = client.post("/api/directives/unmet.md/complete", headers=_auth(),
                    json={"by": "tester"})
    assert r.status_code == 409
    assert r.json()["detail"]["passed"] is False
    assert path.exists()
    assert not (env / "directives" / "completed" / "unmet.md").exists()


def test_complete_refuses_unknown_directive(client):
    r = client.post("/api/directives/ghost.md/complete", headers=_auth(), json={})
    assert r.status_code == 404


# ── complete: success / move ────────────────────────────────────────────────


def _completable(env, name="good.md"):
    body = ("# Completable\n\n"
            "```verify\nfile_exists: docs/readme.txt\n```\n")
    return _write_directive(env, name, body)


def test_complete_moves_file_and_records_metadata(client, env):
    path = _completable(env)
    original = path.read_bytes()
    digest = hashlib.sha256(original).hexdigest()

    r = client.post("/api/directives/good.md/complete", headers=_auth(),
                    json={"by": "tester"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["completed"]["name"] == "good.md"
    assert body["completed"]["sha256"] == digest

    moved = env / "directives" / "completed" / "good.md"
    assert moved.is_file()
    assert moved.read_bytes() == original
    assert not path.exists()

    backup = env / "directives" / ".completed-backup" / "good.md"
    assert backup.is_file()
    assert backup.read_bytes() == original

    meta = json.loads((env / "directives" / "completed" / ".meta.json").read_text())
    entry = meta["good.md"]
    assert entry["completed_at"].endswith("Z")
    assert entry["by"] == "tester"
    assert entry["verdict"]["passed"] is True
    assert entry["sha256"] == digest


# ── completed listing + active separation ───────────────────────────────────


def test_completed_list_requires_auth(client):
    assert client.get("/api/directives/completed").status_code == 401


def test_completed_list_shows_moved_directive(client, env):
    _completable(env)
    client.post("/api/directives/good.md/complete", headers=_auth(),
                json={"by": "tester"})
    rows = client.get("/api/directives/completed", headers=_auth()).json()["completed"]
    assert {r["name"] for r in rows} == {"good.md"}
    row = rows[0]
    assert row["by"] == "tester"
    assert row["verdict"]["passed"] is True


def test_active_list_separates_completed(client, env):
    _completable(env)
    _write_directive(env, "active.md", "# still active\n")
    client.post("/api/directives/good.md/complete", headers=_auth(),
                json={"by": "tester"})

    payload = client.get("/api/directives", headers=_auth()).json()
    active = {r["name"] for r in payload["directives"]}
    completed = {r["name"] for r in payload["completed"]}
    assert active == {"active.md"}
    assert completed == {"good.md"}


# ── CC proxy wiring ─────────────────────────────────────────────────────────


def test_cc_directives_routes_registered():
    from core import cc_extra_routes as cc

    paths = {r.path for r in cc.cc_extra_router.routes}
    assert "/api/directives/completed" in paths
    assert "/api/directives/complete/{directive_id}" in paths
    assert "/api/directives/docs/list" in paths
    assert "/api/directives/docs/download/{rel_path:path}" in paths


def test_cc_completed_proxy_forwards_to_service(monkeypatch):
    from core import cc_extra_routes as cc

    captured = {}

    class FakeResp:
        headers = {"Content-Type": "application/json"}

        def __init__(self, body):
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["token"] = req.get_header("X-directive-token")
        return FakeResp(json.dumps({"completed": []}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(cc, "_directives_token", lambda: "tok-123")

    cc.directives_completed()
    assert captured["url"].endswith("/api/directives/completed")
    assert captured["token"] == "tok-123"

    cc.directives_docs_list()
    assert captured["url"].endswith("/api/directives/docs")
