"""self_healing revives recovered workers and escalates persistently dead ones."""
from unittest import mock

from core.workforce import registry
import core.self_healing as sh


def _reset():
    registry._save_all({"schema_version": 1, "records": []})


def test_dead_worker_with_healthy_provider_revives():
    _reset()
    registry.register(registry.WorkerRecord(
        worker_id="provider:p1", kind="provider", capabilities=["generate"],
        permissions={"secrets": [], "network": [], "filesystem": []}, limits={}))
    registry.update_status("provider:p1", "dead", reason="test")
    # provider reports available again
    with mock.patch.object(sh, "_probe_provider_available", return_value=True):
        sh.reconcile_worker_health()
    assert registry.get("provider:p1").status == "idle"


def test_dead_worker_still_down_escalates_once():
    _reset()
    registry.register(registry.WorkerRecord(
        worker_id="provider:p2", kind="provider", capabilities=["generate"],
        permissions={"secrets": [], "network": [], "filesystem": []}, limits={}))
    registry.update_status("provider:p2", "dead", reason="test")
    sent = []
    with mock.patch.object(sh, "_probe_provider_available", return_value=False), \
         mock.patch.object(sh, "_notify_operator",
                           side_effect=lambda msg: sent.append(msg)):
        sh.reconcile_worker_health()
        sh.reconcile_worker_health()   # second cycle: already escalated flag
    assert len(sent) == 1
    assert registry.get("provider:p2").status == "dead"
