import sys
from pathlib import Path

import pytest
from core.k3.cli import main, build_parser


class TestCLIParser:
    def test_run_requires_command(self):
        parser = build_parser()
        args = parser.parse_args(["run", "--workspace", "/tmp/ws", "make", "build"])
        assert args.command == "run"
        assert args.workspace == "/tmp/ws"
        assert args.persist == "discard"
        assert args.cmd == ["make", "build"]

    def test_run_default_workspace(self):
        parser = build_parser()
        args = parser.parse_args(["run", "echo", "hello"])
        assert args.workspace == "."

    def test_run_persist_options(self):
        parser = build_parser()
        for policy in ["discard", "report", "commit", "artifacts"]:
            args = parser.parse_args(["run", "-p", policy, "echo", "hi"])
            assert args.persist == policy

    def test_run_network_flag(self):
        parser = build_parser()
        args = parser.parse_args(["run", "-n", "echo", "hi"])
        assert args.network is True

    def test_run_timeout(self):
        parser = build_parser()
        args = parser.parse_args(["run", "-t", "600", "echo", "hi"])
        assert args.timeout == 600

    def test_run_memory(self):
        parser = build_parser()
        args = parser.parse_args(["run", "-m", "1g", "echo", "hi"])
        assert args.memory == "1g"

    def test_run_cpus(self):
        parser = build_parser()
        args = parser.parse_args(["run", "-c", "2.0", "echo", "hi"])
        assert args.cpus == "2.0"

    def test_run_artifacts(self):
        parser = build_parser()
        args = parser.parse_args(["run", "-a", "*.tar.gz", "*.zip", "-o", "/tmp/out", "--", "echo", "hi"])
        assert args.artifacts == ["*.tar.gz", "*.zip"]
        assert args.output == "/tmp/out"

    def test_run_env_vars(self):
        parser = build_parser()
        args = parser.parse_args(["run", "-e", "FOO=bar", "-e", "BAZ=qux", "echo", "hi"])
        assert args.env == ["FOO=bar", "BAZ=qux"]

    def test_version_command(self):
        parser = build_parser()
        args = parser.parse_args(["version"])
        assert args.command == "version"


class TestCLIExecution:
    def test_version(self):
        result = main(["version"])
        assert result == 0

    def test_run_no_command(self):
        result = main(["run"])
        assert result == 1

    def test_run_discard_basic(self, tmp_path, monkeypatch):
        import subprocess as sp

        class MockResult:
            returncode = 0
            stderr = ""
            stdout = "hello world"

        monkeypatch.setattr(sp, "run", lambda *a, **k: MockResult())

        ws = tmp_path / "workspace"
        ws.mkdir()

        result = main(["run", "-w", str(ws), "echo", "hello"])
        assert result == 0
