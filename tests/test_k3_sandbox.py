import os
import shutil
import subprocess
from pathlib import Path

import pytest
from core.k3.sandbox import SandboxRuntime, SandboxResult, SandboxUnavailable, sandbox_available
from core.k3.config import NetworkPolicy


def _mock_completed_process(returncode=0, stdout="ok", stderr=""):
    class MockResult:
        def __init__(self):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr
    return MockResult()


class TestSandboxResult:
    def test_success(self):
        r = SandboxResult(exit_code=0, stdout="ok", stderr="", timed_out=False)
        assert r.succeeded is True

    def test_failure(self):
        r = SandboxResult(exit_code=1, stdout="", stderr="err", timed_out=False)
        assert r.succeeded is False

    def test_timed_out(self):
        r = SandboxResult(exit_code=None, stdout="", stderr="", timed_out=True)
        assert r.succeeded is False

    def test_to_dict(self):
        r = SandboxResult(exit_code=0, stdout="out", stderr="err", timed_out=False)
        d = r.to_dict()
        assert d["exit_code"] == 0
        assert d["stdout"] == "out"
        assert d["stderr"] == "err"
        assert d["timed_out"] is False
        assert d["succeeded"] is True


class TestSandboxRuntime:
    def test_init_defaults(self):
        runtime = SandboxRuntime("/tmp/mnt")
        assert runtime.mountpoint == Path("/tmp/mnt")
        assert runtime.network == NetworkPolicy.NONE
        assert runtime.env == {}

    def test_init_with_env(self):
        runtime = SandboxRuntime("/tmp/mnt", env={"FOO": "bar"})
        assert runtime.env == {"FOO": "bar"}

    def test_run_with_bwrap(self, tmp_path, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/bwrap" if x == "bwrap" else None)
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _mock_completed_process())
        runtime = SandboxRuntime(str(tmp_path), network=NetworkPolicy.HOST)
        result = runtime.run(["echo", "hello"])
        assert result.exit_code == 0
        assert result.stdout == "ok"
        assert result.timed_out is False

    def test_run_with_bwrap_no_network(self, tmp_path, monkeypatch):
        captured_args = []
        def _capture(args, **kwargs):
            captured_args.append(args)
            return _mock_completed_process()
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/bwrap" if x == "bwrap" else None)
        monkeypatch.setattr(subprocess, "run", _capture)
        runtime = SandboxRuntime(str(tmp_path), network=NetworkPolicy.NONE)
        runtime.run(["echo", "hello"])
        assert "--unshare-net" in captured_args[0]

    def test_run_with_bwrap_env_vars(self, tmp_path, monkeypatch):
        captured_args = []
        def _capture(args, **kwargs):
            captured_args.append(args)
            return _mock_completed_process()
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/bwrap" if x == "bwrap" else None)
        monkeypatch.setattr(subprocess, "run", _capture)
        runtime = SandboxRuntime(str(tmp_path), env={"FOO": "bar"})
        runtime.run(["echo", "hello"])
        flat = " ".join(str(a) for a in captured_args[0])
        assert "--setenv" in flat
        assert "FOO" in flat
        assert "bar" in flat

    def test_run_with_bwrap_limits(self, tmp_path, monkeypatch):
        captured_args = []
        def _capture(args, **kwargs):
            captured_args.append(args)
            return _mock_completed_process()
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/bwrap" if x == "bwrap" else None)
        monkeypatch.setattr(subprocess, "run", _capture)
        runtime = SandboxRuntime(str(tmp_path), memory_limit="512m", cpu_limit="2.0")
        runtime.run(["echo", "hello"])
        flat = " ".join(str(a) for a in captured_args[0])
        assert "K3_MEMORY_LIMIT" in flat
        assert "512m" in flat
        assert "K3_CPU_LIMIT" in flat
        assert "2.0" in flat

    def test_run_without_bwrap_falls_back(self, tmp_path, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda x: None)
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _mock_completed_process())
        runtime = SandboxRuntime(str(tmp_path))
        result = runtime.run(["echo", "hello"])
        assert result.exit_code == 0

    def test_run_with_bwrap_nonzero_exit(self, tmp_path, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/bwrap" if x == "bwrap" else None)
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _mock_completed_process(returncode=2, stdout="", stderr="fail"))
        runtime = SandboxRuntime(str(tmp_path))
        result = runtime.run(["false"])
        assert result.exit_code == 2
        assert result.stderr == "fail"
        assert result.succeeded is False


class TestSandboxAvailable:
    def test_bwrap_available(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/bwrap" if x == "bwrap" else None)
        assert sandbox_available() is True

    def test_unshare_available(self, monkeypatch):
        def _which(x):
            if x == "bwrap":
                return None
            if x == "unshare":
                return "/usr/bin/unshare"
            return None
        monkeypatch.setattr(shutil, "which", _which)
        assert sandbox_available() is True

    def test_nothing_available(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda x: None)
        assert sandbox_available() is False


class TestSandboxResultDelegation:
    def test_click_output_wired_correctly(self, tmp_path, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda x: None)
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _mock_completed_process(stdout="hello world"))
        runtime = SandboxRuntime(str(tmp_path))
        result = runtime.run(["echo", "hello"])
        assert result.stdout == "hello world"
        assert result.exit_code == 0
