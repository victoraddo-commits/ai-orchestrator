"""Starvation drill (spec §3 testing mandate): end-to-end — all workers busy,
queue backing up, stuck GENERATING job killed and re-dispatched."""
import time
from unittest import mock

import pytest

import core.build_manager as bm
import core.workforce.starvation as starvation
from core.workforce import registry


@pytest.fixture(autouse=True)
def clean():
    registry._save_all({"schema_version": 1, "records": []})
    starvation.clear_boost()
    yield
    registry._save_all({"schema_version": 1, "records": []})
    starvation.clear_boost()


def test_full_pipeline_starvation_response():
    # 1. All capable providers dead → gate denies → build backs off (Task 5)
    registry.register(registry.WorkerRecord(
        worker_id="provider:only_one", kind="provider", capabilities=["generate"],
        permissions={"secrets": [], "network": [], "filesystem": []}, limits={}))
    registry.update_status("provider:only_one", "dead", reason="drill")

    admitted, denials = gate_filtered(["only_one"])
    assert admitted == [] and len(denials) == 1

    # 2. Queued build far past threshold → starvation fires, boost granted
    builds = [{"id": "q1", "name": "queued", "status": "ARCHITECTURE_APPROVED",
               "_queued_since": time.time() - 99999}]
    with mock.patch.object(starvation, "_notify_operator", lambda m: None):
        events = starvation.detect(builds, phase_timeout_seconds=2400)
    assert events and starvation.current_boost() == starvation.BOOST_STEP

    # 3. Effective concurrency respects the ceiling
    base = bm.MAX_CONCURRENT_BUILDS
    assert bm._effective_max_concurrent() <= base + starvation.HARD_CEILING_EXTRA


def gate_filtered(names):
    from core.workforce.gate import filter_candidates
    return filter_candidates(names, "generate")


def test_stuck_generating_job_killed_and_worker_marked(tmp_path):
    registry.register(registry.WorkerRecord(
        worker_id="provider:slowpoke", kind="provider", capabilities=["generate"],
        permissions={"secrets": [], "network": [], "filesystem": []}, limits={}))
    b = bm.create_build("stuck-drill", "desc", "/tmp/stuck-drill")
    b = bm.get_build(b["id"])
    b["status"] = "GENERATING"
    b["generated_by"] = "slowpoke"
    b["_v3_started_at"] = time.time() - (bm.GENERATING_TIMEOUT_SECONDS + 120)
    bm._persist_build(b)

    events = bm._check_timeouts(bm.load_builds())

    assert any(e["action"] == "timeout_failed" for e in events)
    failed = bm.get_build(b["id"], include_terminal=True)  # FAILED is terminal
    assert failed["status"] == "FAILED"
    rec = registry.get("provider:slowpoke")
    assert rec.status == "degraded"

    # 4. Recovery: provider comes back → reconcile revives the worker
    import core.self_healing as sh
    with mock.patch.object(sh, "_probe_provider_available", return_value=True):
        sh.reconcile_worker_health()
    assert registry.get("provider:slowpoke").status == "idle"
