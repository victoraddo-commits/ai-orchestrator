import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import core.memory as memory


@pytest.fixture(autouse=True)
def isolated_memory(tmp_path, monkeypatch):
    test_memory_dir = tmp_path / "memory"
    test_memory_dir.mkdir()

    monkeypatch.setenv("AI_ORCHESTRATOR_MEMORY_DIR", str(test_memory_dir))
    monkeypatch.setattr(memory, "MEMORY_DIR", test_memory_dir)

    yield test_memory_dir
