import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ---------------------------------------------------------------------------
# Prevent KLAUS background scheduler/workers from starting during tests.
# core/api.py calls start_klaus_scheduler() at module level, which spawns
# APScheduler + discovery/ingestion daemon threads that make real HTTP
# requests to Ghana government sites — hanging the test suite on timeouts.
# ---------------------------------------------------------------------------
import unittest.mock as _um

_DUMMY_SCHEDULER = _um.MagicMock()
_DUMMY_SCHEDULER.running = False  # so ``if not _scheduler.running:`` still enters

_scheduler_stub = _um.patch('core.klaus.scheduler._scheduler', _DUMMY_SCHEDULER)
_scheduler_stub.start()

_start_scheduler_stub = _um.patch('core.klaus.scheduler.start_scheduler', lambda: None)
_start_scheduler_stub.start()

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
