import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from core.k3.overlay import OverlayManager, OverlayUnavailable, overlay_available, _supports_overlay


def _mock_run_with_returncode(returncode, stderr=""):
    class MockResult:
        def __init__(self):
            self.returncode = returncode
            self.stderr = stderr
            self.stdout = ""
    return lambda *a, **k: MockResult()


def _mock_run_success():
    return _mock_run_with_returncode(0)


class TestSupportsOverlay:
    def test_overlay_present(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", _mock_run_success())
        assert _supports_overlay() is True

    def test_overlay_absent(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", _mock_run_with_returncode(1))
        assert _supports_overlay() is False

    def test_grep_not_found(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
        assert _supports_overlay() is False


class TestOverlayManager:
    def test_init(self):
        mgr = OverlayManager("/tmp/ws")
        assert mgr.workspace_path == Path("/tmp/ws")
        assert mgr._mounted is False

    def test_mount_raises_on_missing_workspace(self, tmp_path):
        mgr = OverlayManager("/nonexistent/path")
        with pytest.raises(ValueError, match="does not exist"):
            mgr.mount()

    def test_mount_raises_on_file_not_dir(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("hi")
        mgr = OverlayManager(str(f))
        with pytest.raises(ValueError, match="not a directory"):
            mgr.mount()

    def test_mount_creates_dirs(self, tmp_path, monkeypatch):
        monkeypatch.setattr(subprocess, "run", _mock_run_success())
        ws = tmp_path / "workspace"
        ws.mkdir()
        mgr = OverlayManager(str(ws))
        mgr.mount()
        assert mgr._mounted is True
        assert mgr._upperdir.exists()
        assert mgr._workdir.exists()
        assert mgr._mountpoint.exists()
        mgr.cleanup()

    def test_mount_raises_overlay_unavailable(self, tmp_path, monkeypatch):
        call_count = [0]

        def _mock_run(args, **kwargs):
            call_count[0] += 1
            class MockResult:
                returncode = 0
                stderr = ""
                stdout = ""
            if call_count[0] >= 2:
                MockResult.returncode = 1
                MockResult.stderr = "permission denied"
            return MockResult()

        monkeypatch.setattr(subprocess, "run", _mock_run)
        ws = tmp_path / "workspace"
        ws.mkdir()
        mgr = OverlayManager(str(ws))
        with pytest.raises(OverlayUnavailable, match="Failed to mount overlay"):
            mgr.mount()
        mgr.cleanup()

    def test_mount_idempotent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(subprocess, "run", _mock_run_success())
        ws = tmp_path / "workspace"
        ws.mkdir()
        mgr = OverlayManager(str(ws))
        mgr.mount()
        mgr.mount()
        assert mgr._mounted is True
        mgr.cleanup()

    def test_cleanup_removes_tmpdir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(subprocess, "run", _mock_run_success())
        ws = tmp_path / "workspace"
        ws.mkdir()
        mgr = OverlayManager(str(ws))
        mgr.mount()
        tmpdir = mgr._tmpdir
        assert tmpdir.exists()
        mgr.cleanup()
        assert not tmpdir.exists()
        assert mgr._mounted is False

    def test_cleanup_handles_umount_failure(self, tmp_path, monkeypatch):
        monkeypatch.setattr(subprocess, "run", _mock_run_success())
        ws = tmp_path / "workspace"
        ws.mkdir()
        mgr = OverlayManager(str(ws))
        mgr.mount()
        mgr._mounted = False
        errors = mgr.cleanup()
        assert errors == []


class TestOverlayAvailable:
    def test_overlay_available(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", _mock_run_success())
        assert overlay_available() is True

    def test_overlay_not_available(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", _mock_run_with_returncode(1))
        assert overlay_available() is False
