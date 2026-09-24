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
def disable_slow_local_providers(monkeypatch):
    """Disable every local provider for ALL tests by default.

    Local providers (kai_brain, kai_coder, kai_deep, llama_coder_cpu, local)
    have real run_text_task/run_coding_task/available_fn functions that
    connect to the VM104 ollama / VM112 llama.cpp fabric and would hang or slow
    every test that doesn't explicitly mock them. Tests that need a specific
    local provider enable it explicitly in their own setup."""
    import core.ai_provider as ai_provider
    for name, provider in ai_provider._PROVIDERS.items():
        if provider.get("kind") == "local":
            monkeypatch.setitem(provider, "available_fn", lambda: False)


@pytest.fixture(autouse=True)
def isolated_cerebrum_feedback():
    """Reset cerebrum feedback store between tests to prevent state leakage."""
    try:
        import core.cerebrum.feedback as feedback
        feedback.reset_feedback_store()
    except (ImportError, AttributeError):
        pass
    yield


@pytest.fixture(autouse=True)
def isolated_injection_metrics(tmp_path, monkeypatch):
    """Keep persisted prompt-injection counters out of the real memory/ dir."""
    monkeypatch.setenv("KAI_INJECTION_METRICS_DIR",
                       str(tmp_path / "injection_metrics"))
    yield


@pytest.fixture(autouse=True)
def disable_legal_gap_recording(monkeypatch):
    """Never let a test hit the live legal-brain gap queue.

    ``grounding.build_grounded_plan`` records UNGROUNDED questions as
    acquisition gaps when an ``asker`` is supplied. Tests exercise that path
    with the real bot call sites, so recording is disabled by default; the
    dedicated gap tests opt back in with ``KAI_LEGAL_GAP_RECORD=1``.
    """
    monkeypatch.setenv("KAI_LEGAL_GAP_RECORD", "0")
    yield


@pytest.fixture
def client():
    """FastAPI TestClient for API route tests."""
    from fastapi.testclient import TestClient
    from core.api import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def oidc_client_secret(monkeypatch):
    """Provide a dummy CLIENT_SECRET for tests that instantiate OIDCClient.

    The real OIDCClient class loads the secret at class definition time from
    KAI_ID_SECRET_FILE / KAI_ID_SECRET env vars.  This fixture ensures tests
    that call methods on OIDCClient (exchange_code, refresh_token, …) have a
    non-empty secret so the runtime guard inside those methods does not fire
    before the code under test is reached.
    """
    from core import oidc_client
    monkeypatch.setattr(oidc_client.OIDCClient, "CLIENT_SECRET", "test-secret-for-oidc")
