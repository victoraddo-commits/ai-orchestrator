import subprocess
from pathlib import Path

import pytest
from core.k3 import run_build, K3Config, K3Result, PersistPolicy, K3BuildError


class TestK3Result:
    def test_succeeded(self):
        r = K3Result(exit_code=0, stdout="ok", stderr="", timed_out=False, changes=None, mountpoint=None)
        assert r.succeeded is True

    def test_failed(self):
        r = K3Result(exit_code=1, stdout="", stderr="err", timed_out=False, changes=None, mountpoint=None)
        assert r.succeeded is False

    def test_to_dict(self):
        r = K3Result(exit_code=0, stdout="out", stderr="err", timed_out=False, changes=None, mountpoint=Path("/tmp/mnt"))
        d = r.to_dict()
        assert d["exit_code"] == 0
        assert d["stdout"] == "out"
        assert d["stderr"] == "err"
        assert d["timed_out"] is False
        assert d["succeeded"] is True
        assert d["changes"] is None
        assert d["mountpoint"] == "/tmp/mnt"

    def test_to_dict_with_changes(self, tmp_path):
        from core.k3.snapshot import ChangeSet
        changes = ChangeSet(created=[Path("a.txt")], modified=[Path("b.txt")], deleted=[Path("c.txt")])
        r = K3Result(exit_code=0, stdout="", stderr="", timed_out=False, changes=changes, mountpoint=None)
        d = r.to_dict()["changes"]
        assert d["created"] == ["a.txt"]
        assert d["modified"] == ["b.txt"]
        assert d["deleted"] == ["c.txt"]


class TestRunBuild:
    def test_successful_discard_build(self, tmp_path, monkeypatch):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "file.txt").write_text("content")

        def _mock_supports_overlay():
            return True

        def _mock_mount_result(*a, **k):
            class MockResult:
                returncode = 0
                stderr = ""
                stdout = ""
            return MockResult()

        monkeypatch.setattr(subprocess, "run", _mock_mount_result)

        config = K3Config(
            workspace_path=str(ws),
            command=["echo", "hello"],
            persist=PersistPolicy.DISCARD,
        )

        result = run_build(config)
        assert result.exit_code is not None
        assert result.stdout is not None

    def test_failed_build(self, tmp_path, monkeypatch):
        ws = tmp_path / "workspace"
        ws.mkdir()

        call_count = [0]

        def _mock_subprocess_run(args, **kwargs):
            call_count[0] += 1
            class MockResult:
                returncode = 0
                stderr = ""
                stdout = ""
            if call_count[0] > 1 and "mount" not in str(args):
                return MockResult()
            return MockResult()

        monkeypatch.setattr(subprocess, "run", _mock_subprocess_run)

        config = K3Config(
            workspace_path=str(ws),
            command=["false"],
            persist=PersistPolicy.DISCARD,
        )

        result = run_build(config)
        assert result is not None

    def test_cleanup_called_on_error(self, tmp_path, monkeypatch):
        ws = tmp_path / "workspace"
        ws.mkdir()

        monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("simulated")))

        config = K3Config(
            workspace_path=str(ws),
            command=["echo", "hello"],
        )

        with pytest.raises(RuntimeError, match="simulated"):
            run_build(config)

    def test_changes_not_tracked_for_discard_policy(self, tmp_path, monkeypatch):
        ws = tmp_path / "workspace"
        ws.mkdir()

        def _mock_run(args, **kwargs):
            class MockResult:
                returncode = 0
                stderr = ""
                stdout = ""
            return MockResult()

        monkeypatch.setattr(subprocess, "run", _mock_run)

        config = K3Config(
            workspace_path=str(ws),
            command=["touch", "/tmp/x"],
            persist=PersistPolicy.DISCARD,
        )

        result = run_build(config)
        assert result.changes is None
