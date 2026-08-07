import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import core.memory as memory
import core.law_documents as law_documents


@pytest.fixture(autouse=True)
def isolated_memory(tmp_path, monkeypatch):
    test_memory_dir = tmp_path / "memory"
    test_memory_dir.mkdir()

    monkeypatch.setenv("AI_ORCHESTRATOR_MEMORY_DIR", str(test_memory_dir))
    monkeypatch.setattr(memory, "MEMORY_DIR", test_memory_dir)

    yield test_memory_dir


@pytest.fixture(autouse=True)
def isolated_law_documents(tmp_path, monkeypatch):
    test_docs_dir = tmp_path / "law_documents"
    test_docs_dir.mkdir()

    monkeypatch.setenv("AI_ORCHESTRATOR_LAW_DOCUMENTS_DIR", str(test_docs_dir))
    monkeypatch.setattr(law_documents, "DOCUMENTS_DIR", test_docs_dir)


@pytest.fixture(autouse=True)
def isolated_cerebrum_feedback():
    """Reset cerebrum feedback store between tests to prevent state leakage."""
    try:
        import core.cerebrum.feedback as feedback
        feedback.reset_feedback_store()
    except (ImportError, AttributeError):
        pass
    yield
