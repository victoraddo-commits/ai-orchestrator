"""Recovery behaviors: provider-denied builds back off instead of dying;
GENERATING timeouts degrade the generating worker in the registry."""
import time
from unittest import mock

import pytest

import core.build_manager as bm
from core.workforce import registry


@pytest.fixture(autouse=True)
def clean_registry():
    registry._save_all({"schema_version": 1, "records": []})
    yield
    registry._save_all({"schema_version": 1, "records": []})


def _mkbuild(status="ARCHITECTURE_APPROVED"):
    b = bm.create_build("wf-test", "desc", "/tmp/wf-test-project")
    b = bm.get_build(b["id"])
    b["status"] = status
    bm._persist_build(b)
    return b


def test_nocapable_backoff_defers_build():
    b = _mkbuild()
    from core.workforce.gate import NoCapableWorkerError
    with mock.patch.object(bm, "_run_generation",
                           side_effect=NoCapableWorkerError(
                               "all denied", attempts=[])):
        # simulate what _advance_one_build does on the typed error
        try:
            raise NoCapableWorkerError("all denied", attempts=[])
        except NoCapableWorkerError as e:
            bm._handle_provider_exhaustion(b["id"], e)
    b2 = bm.get_build(b["id"])
    assert b2["status"] == "ARCHITECTURE_APPROVED"       # NOT failed
    assert b2["_retry_count"] == 1
    assert b2["_next_retry_at"] > time.time()             # deferred


def test_backoff_escapes_to_failed_after_five_retries():
    b = _mkbuild()
    bm._update(b["id"], lambda x: x.update({"_retry_count": 5}))
    from core.workforce.gate import NoCapableWorkerError
    bm._handle_provider_exhaustion(b["id"], NoCapableWorkerError("x", attempts=[]))
    b2 = bm.get_build(b["id"], include_terminal=True)  # FAILED is terminal
    assert b2["status"] == "FAILED"
    assert "workforce" in b2["failure_reason"].lower()


def test_generating_timeout_degrades_worker():
    registry.register(registry.WorkerRecord(
        worker_id="provider:someprov", kind="provider",
        capabilities=["generate"],
        permissions={"secrets": [], "network": [], "filesystem": []},
        limits={}))
    b = _mkbuild(status="GENERATING")
    bm._update(b["id"], lambda x: x.update({
        "generated_by": "someprov",
        "_v3_started_at": time.time() - (bm.GENERATING_TIMEOUT_SECONDS + 60),
    }))
    builds = bm.load_builds()
    events = bm._check_timeouts(builds)
    assert any(e["action"] == "timeout_failed" for e in events)
    rec = registry.get("provider:someprov")
    assert rec.status == "degraded"
    assert "timeout" in (rec.health["last_reason"] or "")
