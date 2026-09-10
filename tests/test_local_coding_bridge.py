"""Tests for core.local_coding_bridge — the local text-to-code harness."""

import os
import subprocess

import pytest

import core.local_coding_bridge as lb


# --- parse_file_blocks ------------------------------------------------------

class TestParseFileBlocks:
    def test_extracts_path_fenced_blocks(self):
        text = (
            "Here's the implementation.\n\n"
            "```src/main.py\n"
            "print('hello')\n"
            "```\n\n"
            "```README.md\n"
            "# Demo\n"
            "```\n"
        )
        files = lb.parse_file_blocks(text)

        assert dict(files) == {
            "src/main.py": "print('hello')\n",
            "README.md": "# Demo\n",
        }

    def test_skips_language_tagged_blocks(self):
        text = "```python\nx = 1\n```\n"
        assert lb.parse_file_blocks(text) == []

    def test_handles_tilde_fences(self):
        text = "~~~config.ini\n[a]\nb=1\n~~~\n"
        assert lb.parse_file_blocks(text) == [("config.ini", "[a]\nb=1\n")]

    def test_empty_and_none_return_empty(self):
        assert lb.parse_file_blocks("") == []
        assert lb.parse_file_blocks(None) == []

    def test_unclosed_fence_is_not_parsed(self):
        text = "```src/a.py\nprint(1)\n"
        assert lb.parse_file_blocks(text) == []

    def test_special_filename_without_extension(self):
        text = "```Dockerfile\nFROM alpine\n```\n"
        assert lb.parse_file_blocks(text) == [("Dockerfile", "FROM alpine\n")]


# --- _looks_like_path -------------------------------------------------------

class TestLooksLikePath:
    def test_nested_path(self):
        assert lb._looks_like_path("src/main.py") is True

    def test_known_extension(self):
        assert lb._looks_like_path("app.py") is True
        assert lb._looks_like_path("data.json") is True

    def test_language_tag_is_not_a_path(self):
        assert lb._looks_like_path("python") is False
        assert lb._looks_like_path("bash") is False

    def test_special_filename(self):
        assert lb._looks_like_path("Dockerfile") is True
        assert lb._looks_like_path("Makefile") is True

    def test_blank_is_not_a_path(self):
        assert lb._looks_like_path("") is False
        assert lb._looks_like_path(None) is False


# --- _resolve_target --------------------------------------------------------

class TestResolveTarget:
    def test_resolves_nested_path(self, tmp_path):
        target = lb._resolve_target(str(tmp_path), "src/main.py")
        assert target == os.path.join(str(tmp_path), "src", "main.py")

    def test_rejects_path_traversal(self, tmp_path):
        assert lb._resolve_target(str(tmp_path), "../escape.py") is None
        assert lb._resolve_target(str(tmp_path), "a/../../escape.py") is None

    def test_rejects_absolute_path(self, tmp_path):
        assert lb._resolve_target(str(tmp_path), "/etc/passwd") is None

    def test_rejects_empty(self, tmp_path):
        assert lb._resolve_target(str(tmp_path), "") is None


# --- _commit_message --------------------------------------------------------

class TestCommitMessage:
    def test_uses_first_line(self):
        assert lb._commit_message("build the thing\nmore detail") == "build the thing"

    def test_truncates_long_line(self):
        msg = lb._commit_message("x" * 200)
        assert len(msg) <= 70

    def test_fallback_for_empty(self):
        assert lb._commit_message("") == "Kai: implement changes"


# --- _strip_reasoning_block -------------------------------------------------

class TestStripReasoningBlock:
    def test_strips_everything_through_the_final_think_tag(self):
        text = (
            "1. Analyze the request ...\n"
            "10. Generate output.\n"
            "</think>\n"
            "```main.py\nprint('hi')\n```\n"
        )
        assert lb._strip_reasoning_block(text) == "```main.py\nprint('hi')\n```"

    def test_strips_glued_closing_tag_that_precedes_the_real_fence(self):
        # GLM-4.7-Flash leaks its chain-of-thought and glues </think> onto the
        # real opening fence on the same line (confirmed live 2026-09-10).
        text = (
            "reasoning prose\n"
            "    ```</think>```main.py\n"
            "print('hi')\n"
            "```\n"
        )
        assert lb._strip_reasoning_block(text) == "```main.py\nprint('hi')\n```"

    def test_no_think_tag_returns_text_unchanged(self):
        text = "```main.py\nprint('hi')\n```\n"
        assert lb._strip_reasoning_block(text) == text

    def test_empty_and_none(self):
        assert lb._strip_reasoning_block("") == ""
        assert lb._strip_reasoning_block(None) is None


# --- run_coding_task --------------------------------------------------------

class TestRunCodingTask:
    def _fake_generate(self, response_text):
        class Resp:
            def raise_for_status(self):
                pass
            def json(self):
                return {"response": response_text}
        return Resp()

    def test_writes_files_and_commits(self, tmp_path, monkeypatch):
        import requests

        def fake_post(url, json=None, timeout=None):
            return self._fake_generate(
                "```app.py\nprint('hi')\n```\n"
            )
        monkeypatch.setattr(requests, "post", fake_post)

        # git commit succeeds
        captured = {}

        def fake_run(args, **kwargs):
            cwd = kwargs.get("cwd")
            if args[:2] == ["git", "add"]:
                return subprocess.CompletedProcess(args, 0, "", "")
            if args[:2] == ["git", "commit"]:
                captured["message"] = args[args.index("-m") + 1]
                return subprocess.CompletedProcess(args, 0, "", "")
            if args[:2] == ["git", "rev-parse"]:
                return subprocess.CompletedProcess(args, 0, "abc123\n", "")
            return subprocess.CompletedProcess(args, 0, "", "")
        monkeypatch.setattr(lb.subprocess, "run", fake_run)

        result = lb.run_coding_task(str(tmp_path), "write a hello script")

        assert result["success"] is True
        assert result["files_changed"] == ["app.py"]
        assert (tmp_path / "app.py").read_text() == "print('hi')\n"
        assert result["commits"] == [{"sha": "abc123", "message": "write a hello script"}]

    def test_no_files_is_a_failure_not_an_exception(self, tmp_path, monkeypatch):
        import requests

        def fake_post(url, json=None, timeout=None):
            return self._fake_generate("I have analyzed the problem but have no files.")
        monkeypatch.setattr(requests, "post", fake_post)

        result = lb.run_coding_task(str(tmp_path), "do a thing")

        assert result["success"] is False
        assert result["files_changed"] == []
        assert result["tool_errors"]

    def test_model_unreachable_raises(self, tmp_path, monkeypatch):
        import requests

        def fake_post(url, json=None, timeout=None):
            raise requests.exceptions.ConnectionError("boom")
        monkeypatch.setattr(requests, "post", fake_post)

        with pytest.raises(RuntimeError, match="call failed"):
            lb.run_coding_task(str(tmp_path), "do a thing")

    def test_path_traversal_is_rejected(self, tmp_path, monkeypatch):
        import requests

        def fake_post(url, json=None, timeout=None):
            return self._fake_generate(
                "```../evil.py\nprint('bad')\n```\n"
                "```ok.py\nprint('ok')\n```\n"
            )
        monkeypatch.setattr(requests, "post", fake_post)
        monkeypatch.setattr(lb.subprocess, "run",
                           lambda args, **kw: subprocess.CompletedProcess(args, 0, "", ""))

        result = lb.run_coding_task(str(tmp_path), "do a thing")

        assert result["success"] is True
        assert result["files_changed"] == ["ok.py"]
        assert not (tmp_path.parent / "evil.py").exists()

    def test_strips_reasoning_model_think_leak_before_parsing(self, tmp_path, monkeypatch):
        import requests

        def fake_post(url, json=None, timeout=None):
            return self._fake_generate(
                "1. Analyze the request ...\n"
                "    ```</think>```app.py\n"
                "print('hi')\n"
                "```\n"
            )
        monkeypatch.setattr(requests, "post", fake_post)
        monkeypatch.setattr(lb.subprocess, "run",
                           lambda args, **kw: subprocess.CompletedProcess(args, 0, "", ""))

        result = lb.run_coding_task(str(tmp_path), "write a hello script")

        assert result["success"] is True
        assert result["files_changed"] == ["app.py"]
        assert (tmp_path / "app.py").read_text() == "print('hi')\n"


# --- provider wiring --------------------------------------------------------

class TestProviderWiring:
    def test_kai_coder_and_kai_brain_have_coding_capability(self):
        import core.ai_provider as ai_provider

        assert ai_provider.get_provider("kai_coder")["run_coding_task"] is not None
        assert ai_provider.get_provider("kai_brain")["run_coding_task"] is not None

    def test_coding_role_points_at_local_coding_providers(self):
        from core.ai.ai_router import ROLE_PROVIDERS

        assert ROLE_PROVIDERS["coding"] == ["kai_coder", "kai_brain"]
