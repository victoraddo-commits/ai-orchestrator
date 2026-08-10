import os
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

import pytest

import core.test_gen.config as config
import core.test_gen.discovery as discovery
import core.test_gen.generator as generator
import core.test_gen.parser as parser
import core.test_gen.prompt as prompt
import core.test_gen.smoke as smoke
import core.test_gen.verifier as verifier
import core.test_gen.writer as writer


# ---------------------------------------------------------------------------
# parser — parse_file
# ---------------------------------------------------------------------------

def test_parse_file_extracts_functions(tmp_path):
    src = tmp_path / "example.py"
    src.write_text(
        "def add(a, b):\n"
        '    """Add two numbers."""\n'
        "    return a + b\n"
        "\n"
        "def greet(name='world'):\n"
        "    return f'hello {name}'\n"
    )

    info = parser.parse_file(str(src))

    assert info["path"] == str(src)
    assert len(info["functions"]) == 2
    assert info["functions"][0]["name"] == "add"
    assert info["functions"][0]["args"] == ["a", "b"]
    assert info["functions"][0]["has_docstring"] is True
    assert info["functions"][0]["async"] is False
    assert info["functions"][1]["name"] == "greet"
    assert info["functions"][1]["args"] == ["name"]
    assert info["functions"][1]["has_docstring"] is False


def test_parse_file_extracts_classes_and_methods(tmp_path):
    src = tmp_path / "klass.py"
    src.write_text(
        "class Calculator:\n"
        "    def __init__(self):\n"
        "        pass\n"
        "    def add(self, a, b):\n"
        "        return a + b\n"
        "    async def fetch(self, url):\n"
        '        """Fetch."""\n'
        "        pass\n"
    )

    info = parser.parse_file(str(src))

    assert len(info["classes"]) == 1
    cls = info["classes"][0]
    assert cls["name"] == "Calculator"
    assert len(cls["methods"]) == 3
    assert cls["methods"][0]["name"] == "__init__"
    assert cls["methods"][1]["name"] == "add"
    assert cls["methods"][1]["async"] is False
    assert cls["methods"][2]["name"] == "fetch"
    assert cls["methods"][2]["async"] is True
    assert cls["methods"][2]["has_docstring"] is True


def test_parse_file_extracts_imports(tmp_path):
    src = tmp_path / "mod.py"
    src.write_text(
        "import os\n"
        "from pathlib import Path\n"
        "from typing import List, Optional\n"
        "\n"
        "def foo():\n"
        "    pass\n"
    )

    info = parser.parse_file(str(src))

    assert len(info["imports"]) == 3


def test_parse_file_extracts_module_docstring(tmp_path):
    src = tmp_path / "mod.py"
    src.write_text(
        '"""This is a module docstring."""\n'
        "\n"
        "def foo():\n"
        "    pass\n"
    )

    info = parser.parse_file(str(src))

    assert info["module_docstring"] == "This is a module docstring."


def test_parse_file_skips_top_level_dunder(tmp_path):
    src = tmp_path / "mod.py"
    src.write_text(
        "def __custom_dunder__():\n"
        "    pass\n"
        "def regular_fn():\n"
        "    pass\n"
    )

    info = parser.parse_file(str(src))

    names = [f["name"] for f in info["functions"]]
    assert "regular_fn" in names
    assert "__custom_dunder__" not in names


def test_parse_file_includes_class_dunder_methods(tmp_path):
    src = tmp_path / "mod.py"
    src.write_text(
        "class Foo:\n"
        "    def __init__(self):\n"
        "        pass\n"
        "    def __repr__(self):\n"
        "        return 'x'\n"
        "    def __str__(self):\n"
        "        return 'y'\n"
    )

    info = parser.parse_file(str(src))

    methods = info["classes"][0]["methods"]
    names = [m["name"] for m in methods]
    assert "__init__" in names
    assert "__repr__" in names
    assert "__str__" in names


def test_parse_file_extracts_decorators(tmp_path):
    src = tmp_path / "mod.py"
    src.write_text(
        "from functools import lru_cache\n"
        "\n"
        "@lru_cache\n"
        "def cached_fn(x):\n"
        "    return x\n"
    )

    info = parser.parse_file(str(src))

    assert "lru_cache" in info["functions"][0]["decorators"][0]


# ---------------------------------------------------------------------------
# parser — build_context
# ---------------------------------------------------------------------------

def test_build_context_includes_all_sections():
    infos = [
        {
            "path": "/src/utils.py",
            "module_docstring": "Utility functions.",
            "imports": ["import os", "from typing import List"],
            "functions": [
                {"name": "helper", "args": ["x"], "has_docstring": True, "async": False, "line": 5, "decorators": []},
            ],
            "classes": [],
        },
    ]

    ctx = parser.build_context(infos)

    assert "utils.py" in ctx
    assert "Utility functions." in ctx
    assert "import os" in ctx
    assert "from typing import List" in ctx
    assert "def helper(x):" in ctx


def test_build_context_handles_async_functions():
    infos = [
        {
            "path": "/src/async_mod.py",
            "module_docstring": None,
            "imports": [],
            "functions": [
                {"name": "fetch", "args": ["url"], "has_docstring": False, "async": True, "line": 3, "decorators": []},
            ],
            "classes": [],
        },
    ]

    ctx = parser.build_context(infos)

    assert "async def fetch(url):" in ctx


def test_build_context_handles_classes():
    infos = [
        {
            "path": "/src/service.py",
            "module_docstring": None,
            "imports": [],
            "functions": [],
            "classes": [
                {"name": "Service", "methods": [
                    {"name": "run", "args": ["self"], "has_docstring": True, "async": False, "line": 5, "decorators": []},
                ], "line": 2},
            ],
        },
    ]

    ctx = parser.build_context(infos)

    assert "class Service:" in ctx
    assert "    def run(self):" in ctx


# ---------------------------------------------------------------------------
# discovery — resolve_sources
# ---------------------------------------------------------------------------

def test_resolve_sources_finds_py_files_in_directory(tmp_path):
    (tmp_path / "a.py").write_text("pass")
    (tmp_path / "b.py").write_text("pass")
    (tmp_path / "c.txt").write_text("nope")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "cached.py").write_text("skip")

    results = discovery.resolve_sources([str(tmp_path)])

    paths = {p.name for p in results}
    assert "a.py" in paths
    assert "b.py" in paths
    assert "c.txt" not in paths
    assert "cached.py" not in paths


def test_resolve_sources_skips_init_files(tmp_path):
    (tmp_path / "__init__.py").write_text("")
    (tmp_path / "main.py").write_text("pass")

    results = discovery.resolve_sources([str(tmp_path)])

    paths = {p.name for p in results}
    assert "main.py" in paths
    assert "__init__.py" not in paths


def test_resolve_sources_skips_dot_dirs(tmp_path):
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "secret.py").write_text("pass")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config.py").write_text("pass")

    results = discovery.resolve_sources([str(tmp_path)])

    paths = {p.name for p in results}
    assert "secret.py" not in paths
    assert "config.py" not in paths


def test_resolve_sources_nonexistent_paths():
    results = discovery.resolve_sources(["/nonexistent/path"])
    assert results == []


def test_resolve_sources_empty_list():
    assert discovery.resolve_sources([]) == []


# ---------------------------------------------------------------------------
# writer
# ---------------------------------------------------------------------------

def test_discover_generated_tests_finds_test_files(tmp_path):
    (tmp_path / "test_utils.py").write_text("pass")
    (tmp_path / "test_models.py").write_text("pass")
    (tmp_path / "helpers.py").write_text("pass")
    (tmp_path / "__init__.py").write_text("")

    results = writer.discover_generated_tests(tmp_path)

    names = {p.name for p in results}
    assert "test_utils.py" in names
    assert "test_models.py" in names
    assert "helpers.py" not in names
    assert "__init__.py" not in names


def test_discover_generated_tests_empty_dir(tmp_path):
    results = writer.discover_generated_tests(tmp_path)
    assert results == []


def test_discover_generated_tests_nonexistent_dir():
    results = writer.discover_generated_tests(Path("/nonexistent"))
    assert results == []


def test_ensure_test_dir_creates(tmp_path):
    target = tmp_path / "nested" / "tests"
    writer.ensure_test_dir(target)
    assert target.is_dir()


def test_write_test_file_atomic(tmp_path):
    dst = tmp_path / "test_foo.py"
    writer.write_test_file(str(dst), "def test_pass(): pass")
    assert dst.read_text() == "def test_pass(): pass"


# ---------------------------------------------------------------------------
# prompt — build_generation_prompt
# ---------------------------------------------------------------------------

def test_build_generation_prompt_includes_source_context():
    infos = [
        {
            "path": "/src/main.py",
            "module_docstring": "Main module.",
            "imports": [],
            "functions": [{"name": "run", "args": [], "has_docstring": False, "async": False, "line": 2, "decorators": []}],
            "classes": [],
        },
    ]

    text = prompt.build_generation_prompt(infos, "/tmp/tests")

    assert "pytest-compatible tests" in text
    assert "main.py" in text
    assert "def run()" in text
    assert "/tmp/tests" in text


# ---------------------------------------------------------------------------
# generator
# ---------------------------------------------------------------------------

_FAKE_CODING_RESULT = {
    "success": True,
    "response_text": "Generated tests.",
    "files_changed": ["tests/test_utils.py"],
    "tool_errors": [],
    "cost": 0.005,
}


def test_generate_tests_delegates_to_provider(tmp_path):
    import core.ai_provider as ai_provider

    src = tmp_path / "utils.py"
    src.write_text("def add(a, b):\n    return a + b\n")
    test_dir = tmp_path / "tests"
    test_dir.mkdir()

    run_calls = []

    def fake_run_coding(project_path, instruction, timeout=None):
        run_calls.append({"project_path": project_path, "instruction": instruction, "timeout": timeout})
        return dict(_FAKE_CODING_RESULT)

    monkeypatch = mock.patch.dict(
        ai_provider._PROVIDERS,
        {"gpuai_minimax": {
            "run_coding_task": fake_run_coding,
            "available_fn": lambda: True,
            "kind": "cloud",
            "description": "test",
            "capabilities": ["coding_agent", "file_access"],
            "cost_tier": "paid",
            "enabled": True,
        }},
    )

    with monkeypatch:
        result = generator.generate_tests(
            source_files=[src],
            test_dir=test_dir,
            provider_name="gpuai_minimax",
        )

    assert result["success"] is True
    assert result["provider"] == "gpuai_minimax"
    assert result["cost"] == 0.005
    assert len(run_calls) == 1
    assert str(src) in run_calls[0]["instruction"]


def test_generate_tests_unknown_provider():
    result = generator.generate_tests(
        source_files=[Path("/fake.py")],
        test_dir=Path("/tmp"),
        provider_name="nonexistent_provider",
    )

    assert result["success"] is False
    assert "not registered" in result["tool_errors"][0]["content"]


def test_generate_tests_provider_not_available():
    import core.ai_provider as ai_provider

    monkeypatch = mock.patch.dict(
        ai_provider._PROVIDERS,
        {"offline_provider": {
            "run_coding_task": lambda *a, **kw: {},
            "available_fn": lambda: False,
            "kind": "cloud",
            "description": "test",
            "capabilities": [],
            "cost_tier": "paid",
            "enabled": True,
        }},
    )

    with monkeypatch:
        result = generator.generate_tests(
            source_files=[Path("/fake.py")],
            test_dir=Path("/tmp"),
            provider_name="offline_provider",
        )

    assert result["success"] is False
    assert "not available" in result["tool_errors"][0]["content"]


def test_generate_tests_provider_lacks_coding():
    import core.ai_provider as ai_provider

    monkeypatch = mock.patch.dict(
        ai_provider._PROVIDERS,
        {"text_only": {
            "run_coding_task": None,
            "run_text_task": lambda *a, **kw: "ok",
            "available_fn": lambda: True,
            "kind": "cloud",
            "description": "test",
            "capabilities": ["text_task"],
            "cost_tier": "free",
            "enabled": True,
        }},
    )

    with monkeypatch:
        result = generator.generate_tests(
            source_files=[Path("/fake.py")],
            test_dir=Path("/tmp"),
            provider_name="text_only",
        )

    assert result["success"] is False
    assert "coding_agent" in result["tool_errors"][0]["content"]


def test_generate_tests_uses_default_timeout(tmp_path):
    import core.ai_provider as ai_provider

    src = tmp_path / "mod.py"
    src.write_text("def f(): pass\n")
    test_dir = tmp_path / "tests"
    test_dir.mkdir()

    run_calls = []

    def fake_run(project_path, instruction, timeout=None):
        run_calls.append(timeout)
        return dict(_FAKE_CODING_RESULT)

    monkeypatch = mock.patch.dict(
        ai_provider._PROVIDERS,
        {"gpuai_minimax": {
            "run_coding_task": fake_run,
            "available_fn": lambda: True,
            "kind": "cloud",
            "description": "test",
            "capabilities": ["coding_agent"],
            "cost_tier": "paid",
            "enabled": True,
        }},
    )

    with monkeypatch:
        generator.generate_tests(
            source_files=[src], test_dir=test_dir, provider_name="gpuai_minimax",
        )

    assert run_calls[0] == config.DEFAULT_TIMEOUT


def test_generate_tests_auto_detects_project_path(monkeypatch, tmp_path):
    import core.ai_provider as ai_provider

    src = tmp_path / "src" / "main.py"
    src.parent.mkdir()
    src.write_text("def f(): pass\n")
    test_dir = tmp_path / "tests"
    test_dir.mkdir()

    run_calls = []

    def fake_run(project_path, instruction, timeout=None):
        run_calls.append({"project_path": project_path, "instruction": instruction, "timeout": timeout})
        return dict(_FAKE_CODING_RESULT)

    monkeypatch = mock.patch.dict(
        ai_provider._PROVIDERS,
        {"gpuai_minimax": {
            "run_coding_task": fake_run,
            "available_fn": lambda: True,
            "kind": "cloud",
            "description": "test",
            "capabilities": ["coding_agent"],
            "cost_tier": "paid",
            "enabled": True,
        }},
    )

    with monkeypatch:
        generator.generate_tests(
            source_files=[src], test_dir=test_dir, provider_name="gpuai_minimax",
        )

    assert run_calls[0]["project_path"] is not None


# ---------------------------------------------------------------------------
# verifier — run_tests
# ---------------------------------------------------------------------------

def test_run_tests_empty_dir_returns_failure():
    with tempfile.TemporaryDirectory() as td:
        result = verifier.run_tests(Path(td))
        assert result["passed"] is False
        assert "No generated test files found" in result["stderr"]


def test_run_tests_delegates_to_pytest(monkeypatch):
    fake_result = subprocess.CompletedProcess(
        args=["pytest"], returncode=0, stdout="2 passed\n", stderr="",
    )

    def fake_run(cmd, capture_output, text, timeout, cwd):
        assert cmd[1] == "-m"
        assert cmd[2] == "pytest"
        return fake_result

    monkeypatch.setattr(subprocess, "run", fake_run)

    with tempfile.TemporaryDirectory() as td:
        test_dir = Path(td)
        (test_dir / "test_x.py").write_text("def test_x(): pass")
        result = verifier.run_tests(test_dir)

    assert result["passed"] is True
    assert result["returncode"] == 0
    assert "test_x.py" in [Path(f).name for f in result["test_files_checked"]]


def test_run_tests_failed_run(monkeypatch):
    fake_result = subprocess.CompletedProcess(
        args=["pytest"], returncode=1, stdout="1 failed\n", stderr="FAILED",
    )

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)

    with tempfile.TemporaryDirectory() as td:
        test_dir = Path(td)
        (test_dir / "test_fail.py").write_text("def test_fail(): assert False")
        result = verifier.run_tests(test_dir)

    assert result["passed"] is False
    assert result["returncode"] == 1


def test_run_tests_uses_default_cwd(monkeypatch):
    cwd_captured = {}

    def fake_run(cmd, capture_output, text, timeout, cwd):
        cwd_captured["cwd"] = cwd
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with tempfile.TemporaryDirectory() as td:
        test_dir = Path(td) / "nested" / "tests"
        test_dir.mkdir(parents=True)
        (test_dir / "test_x.py").write_text("def test_x(): pass")
        verifier.run_tests(test_dir)

    # default cwd should be parent.parent of test_dir
    assert str(Path(td)) == cwd_captured["cwd"]


# ---------------------------------------------------------------------------
# smoke
# ---------------------------------------------------------------------------

def test_smoke_fixture_is_valid_python():
    import ast
    ast.parse(smoke.SMOKE_FIXTURE)


def test_write_smoke_fixture_writes_file(tmp_path):
    target = tmp_path / "fixture" / "math_utils.py"
    smoke.write_smoke_fixture(target)
    assert target.read_text() == smoke.SMOKE_FIXTURE


def test_run_smoke_full_pipeline(monkeypatch, tmp_path):
    import shutil
    import core.ai_provider as ai_provider

    run_calls = []
    fake_gen_result = {
        "success": True,
        "response_text": "",
        "files_changed": ["tests/test_math_utils.py"],
        "tool_errors": [],
        "cost": 0.003,
    }

    def fake_run_coding(project_path, instruction, timeout=None):
        run_calls.append(instruction)
        # Write a dummy test file so the verifier finds something
        test_file = Path(project_path) / "tests" / "test_math_utils.py"
        test_file.parent.mkdir(exist_ok=True)
        test_file.write_text("def test_add():\n    from fixture_src.math_utils import add\n    assert add(1, 2) == 3\n")
        return dict(fake_gen_result)

    # Mock subprocess.run for the verifier
    fake_subprocess = subprocess.CompletedProcess(
        args=["pytest"], returncode=0, stdout="1 passed\n", stderr="",
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_subprocess)

    monkeypatch = mock.patch.dict(
        ai_provider._PROVIDERS,
        {"gpuai_minimax": {
            "run_coding_task": fake_run_coding,
            "available_fn": lambda: True,
            "kind": "cloud",
            "description": "test",
            "capabilities": ["coding_agent"],
            "cost_tier": "paid",
            "enabled": True,
        }},
    )

    with monkeypatch:
        result = smoke.run_smoke(provider_name="gpuai_minimax")

    assert result["passed"] is True
    assert result["generation_result"]["success"] is True
    assert result["generation_result"]["cost"] == 0.003
    assert result["verification_result"]["passed"] is True


def test_run_smoke_generation_failure(monkeypatch):
    import core.ai_provider as ai_provider

    monkeypatch = mock.patch.dict(
        ai_provider._PROVIDERS,
        {"gpuai_minimax": {
            "run_coding_task": lambda *a, **kw: {"success": False, "response_text": "", "files_changed": [], "tool_errors": [{"tool": None, "content": "quota exceeded"}], "cost": None},
            "available_fn": lambda: True,
            "kind": "cloud",
            "description": "test",
            "capabilities": ["coding_agent"],
            "cost_tier": "paid",
            "enabled": True,
        }},
    )

    with monkeypatch:
        result = smoke.run_smoke(provider_name="gpuai_minimax")

    assert result["passed"] is False
    assert result["generation_result"]["success"] is False
    assert result["verification_result"] is None


def test_run_smoke_keep_output(monkeypatch, tmp_path):
    import shutil
    import core.ai_provider as ai_provider

    fake_gen = {"success": True, "response_text": "", "files_changed": ["tests/test_math_utils.py"], "tool_errors": [], "cost": 0.0}

    def fake_run(project_path, instruction, timeout=None):
        (Path(project_path) / "tests" / "test_math_utils.py").write_text("def test_pass(): pass")
        return dict(fake_gen)

    provider_patch = mock.patch.dict(
        ai_provider._PROVIDERS,
        {"gpuai_minimax": {"run_coding_task": fake_run, "available_fn": lambda: True, "kind": "cloud", "description": "t", "capabilities": ["coding_agent"], "cost_tier": "paid", "enabled": True}},
    )

    fake_subprocess = subprocess.CompletedProcess(args=["pytest"], returncode=0, stdout="1 passed\n", stderr="")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_subprocess)

    rmtree_calls = []
    monkeypatch.setattr(shutil, "rmtree", lambda p, **kw: rmtree_calls.append(str(p)))

    with provider_patch:
        result_no_keep = smoke.run_smoke(provider_name="gpuai_minimax", keep_output=False)
        assert result_no_keep["passed"] is True
        assert len(rmtree_calls) == 1

    with provider_patch:
        result_keep = smoke.run_smoke(provider_name="gpuai_minimax", keep_output=True)
        assert result_keep["passed"] is True
        assert len(rmtree_calls) == 1


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

def test_config_defaults():
    assert config.DEFAULT_CODING_PROVIDER in ("gpuai_minimax", "qwen4_coding")
    assert isinstance(config.DEFAULT_TIMEOUT, int)
    assert config.DEFAULT_TIMEOUT > 0
    assert config.DEFAULT_TEST_RUNNER == "pytest"
    assert config.DEFAULT_TEST_FLAGS
