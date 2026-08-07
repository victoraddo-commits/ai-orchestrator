import os
import stat
from pathlib import Path

import pytest
from core.k3.sandbox import WorkspaceSandbox
from core.k3.exceptions import K3Error


class TestWorkspaceSandboxInit:
    def test_creates_directory(self, tmp_path):
        sandbox = WorkspaceSandbox(base_dir=tmp_path)
        assert sandbox.root.exists()
        assert sandbox.root.is_dir()

    def test_directory_is_writable(self, tmp_path):
        sandbox = WorkspaceSandbox(base_dir=tmp_path)
        test_file = sandbox.root / "test.txt"
        test_file.write_text("hello")
        assert test_file.read_text() == "hello"

    def test_random_name_per_instance(self, tmp_path):
        a = WorkspaceSandbox(base_dir=tmp_path)
        b = WorkspaceSandbox(base_dir=tmp_path)
        assert a.root != b.root
        assert a.root.name != b.root.name

    def test_permissions_0700(self, tmp_path):
        sandbox = WorkspaceSandbox(base_dir=tmp_path)
        mode = os.stat(sandbox.root).st_mode & 0o777
        assert mode == 0o700

    def test_prefix_in_name(self, tmp_path):
        sandbox = WorkspaceSandbox(base_dir=tmp_path, prefix="build-")
        assert sandbox.root.name.startswith("build-")

    def test_default_prefix(self, tmp_path):
        sandbox = WorkspaceSandbox(base_dir=tmp_path)
        assert sandbox.root.name.startswith("k3-workspace-")

    def test_creates_designated_subdirs(self, tmp_path):
        sandbox = WorkspaceSandbox(base_dir=tmp_path, subdirs=["src", "build", "logs"])
        for name in ["src", "build", "logs"]:
            sub = sandbox.root / name
            assert sub.exists()
            assert sub.is_dir()
            mode = os.stat(sub).st_mode & 0o777
            assert mode == 0o700

    def test_artifact_dir_property(self, tmp_path):
        sandbox = WorkspaceSandbox(base_dir=tmp_path)
        assert sandbox.artifact_dir.exists()
        assert sandbox.artifact_dir.name == "artifacts"

    def test_log_dir_property(self, tmp_path):
        sandbox = WorkspaceSandbox(base_dir=tmp_path)
        assert sandbox.log_dir.exists()
        assert sandbox.log_dir.name == "logs"

    def test_work_dir_property(self, tmp_path):
        sandbox = WorkspaceSandbox(base_dir=tmp_path)
        assert sandbox.work_dir.exists()
        assert sandbox.work_dir.name == "work"

    def test_base_dir_does_not_exist_creates_it(self, tmp_path):
        base = tmp_path / "nested" / "sandboxes"
        sandbox = WorkspaceSandbox(base_dir=base)
        assert base.exists()
        assert sandbox.root.parent == base


class TestWorkspaceSandboxCopySource:
    def test_copy_source_copies_files(self, tmp_path):
        src = tmp_path / "source"
        src.mkdir()
        (src / "hello.py").write_text("print('hello')")
        (src / "config.yaml").write_text("key: value")
        sub = src / "sub"
        sub.mkdir()
        (sub / "nested.py").write_text("pass")

        sandbox = WorkspaceSandbox(base_dir=tmp_path, copy_source=src)
        assert (sandbox.root / "hello.py").exists()
        assert (sandbox.root / "config.yaml").exists()
        assert (sandbox.root / "sub" / "nested.py").exists()

    def test_copy_source_content_matches(self, tmp_path):
        src = tmp_path / "source"
        src.mkdir()
        (src / "hello.py").write_text("print('hello')")

        sandbox = WorkspaceSandbox(base_dir=tmp_path, copy_source=src)
        result = (sandbox.root / "hello.py").read_text()
        assert result == "print('hello')"

    def test_copy_source_skips_git_dir(self, tmp_path):
        src = tmp_path / "source"
        src.mkdir()
        gitdir = src / ".git"
        gitdir.mkdir()
        (gitdir / "config").write_text("fake")
        (src / "app.py").write_text("pass")

        sandbox = WorkspaceSandbox(base_dir=tmp_path, copy_source=src)
        assert (sandbox.root / "app.py").exists()
        assert not (sandbox.root / ".git").exists()

    def test_copy_source_none_does_not_copy(self, tmp_path):
        sandbox = WorkspaceSandbox(base_dir=tmp_path)
        files = list(sandbox.root.iterdir())
        assert len(files) >= 3


class TestWorkspaceSandboxCleanup:
    def test_cleanup_removes_root(self, tmp_path):
        sandbox = WorkspaceSandbox(base_dir=tmp_path)
        root_path = sandbox.root
        assert root_path.exists()
        sandbox.cleanup()
        assert not root_path.exists()

    def test_double_cleanup_is_idempotent(self, tmp_path):
        sandbox = WorkspaceSandbox(base_dir=tmp_path)
        sandbox.cleanup()
        sandbox.cleanup()

    def test_cleanup_all_removes_base_dir(self, tmp_path):
        base = tmp_path / "isolated"
        sandbox = WorkspaceSandbox(base_dir=base)
        assert base.exists()
        sandbox.cleanup_all()
        assert not base.exists()

    def test_context_manager_cleans_up(self, tmp_path):
        sandbox = WorkspaceSandbox(base_dir=tmp_path)
        root_path = sandbox.root
        with sandbox:
            assert root_path.exists()
        assert not root_path.exists()

    def test_context_manager_cleans_up_on_exception(self, tmp_path):
        sandbox = WorkspaceSandbox(base_dir=tmp_path)
        root_path = sandbox.root
        try:
            with sandbox:
                raise ValueError("boom")
        except ValueError:
            pass
        assert not root_path.exists()

    def test_shred_wipes_files_before_removal(self, tmp_path):
        src = tmp_path / "source"
        src.mkdir()
        secret = src / "secret.txt"
        secret.write_text("super-secret-api-key-12345")

        sandbox = WorkspaceSandbox(base_dir=tmp_path, copy_source=src)
        secret_path = sandbox.root / "secret.txt"

        sandbox.cleanup(shred=True)
        assert not secret_path.exists()
        assert not sandbox.root.exists()

    def test_cleanup_with_write_protected_files(self, tmp_path):
        sandbox = WorkspaceSandbox(base_dir=tmp_path)
        protected = sandbox.root / "protected.txt"
        protected.write_text("data")
        os.chmod(protected, 0o444)
        sandbox.cleanup()
        assert not sandbox.root.exists()


class TestWorkspaceSandboxLock:
    def test_lock_file_is_created(self, tmp_path):
        sandbox = WorkspaceSandbox(base_dir=tmp_path)
        assert sandbox.is_locked()
        assert sandbox.lock_path.exists()

    def test_cleanup_removes_lock(self, tmp_path):
        sandbox = WorkspaceSandbox(base_dir=tmp_path)
        lock_path = sandbox.lock_path
        assert lock_path.exists()
        sandbox.cleanup()
        assert not lock_path.exists()

    def test_lock_contains_pid(self, tmp_path):
        sandbox = WorkspaceSandbox(base_dir=tmp_path)
        content = sandbox.lock_path.read_text().strip()
        assert content == str(os.getpid())


class TestWorkspaceSandboxAsDict:
    def test_to_dict(self, tmp_path):
        sandbox = WorkspaceSandbox(base_dir=tmp_path)
        d = sandbox.to_dict()
        assert d["root"] == str(sandbox.root)
        assert d["work_dir"] == str(sandbox.work_dir)
        assert d["artifact_dir"] == str(sandbox.artifact_dir)
        assert d["log_dir"] == str(sandbox.log_dir)
        assert d["locked"] is True
        assert d["pid"] == os.getpid()

    def test_repr(self, tmp_path):
        sandbox = WorkspaceSandbox(base_dir=tmp_path)
        r = repr(sandbox)
        assert sandbox.root.name in r
        assert "WorkspaceSandbox" in r
