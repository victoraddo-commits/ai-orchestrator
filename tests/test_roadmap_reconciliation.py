"""Tests for roadmap <-> builds reconciliation (Phase 0.6)."""
import json
from pathlib import Path

from core import roadmap_reconciliation as rr


def _write(path, data):
    Path(path).write_text(json.dumps(data, indent=2))


def _roadmap(phases):
    return {"schema_version": 1, "phases": phases, "last_reconciled": None}


def _phase(pid, status="completed", **kw):
    phase = {"id": pid, "name": pid, "status": status, "dependencies": [], "priority": 0}
    phase.update(kw)
    return phase


def _build(bid, name, status):
    return {"id": bid, "name": name, "status": status}


def test_certain_phase_with_failed_linked_build():
    roadmap = _roadmap([_phase("P1", build_id="b1")])
    builds = [_build("b1", "P1", "FAILED")]

    changes = rr.compute_reconciliation(roadmap["phases"], builds)

    assert [c["phase_id"] for c in changes["certain"]] == ["P1"]
    assert changes["proposed"] == []


def test_phase_with_completed_linked_build_untouched():
    roadmap = _roadmap([_phase("P1", build_id="b1")])
    builds = [_build("b1", "P1", "COMPLETED")]

    changes = rr.compute_reconciliation(roadmap["phases"], builds)

    assert changes["certain"] == []
    assert changes["proposed"] == []


def test_already_failed_phase_not_reopened_again():
    roadmap = _roadmap([_phase("P1", status="failed", build_id="b1")])
    builds = [_build("b1", "P1", "FAILED")]

    changes = rr.compute_reconciliation(roadmap["phases"], builds)

    assert changes["certain"] == []


def test_name_only_failed_build_is_proposed_not_applied():
    roadmap = _roadmap([_phase("P1")])
    builds = [_build("b1", "P1", "FAILED")]

    changes = rr.compute_reconciliation(roadmap["phases"], builds)

    assert changes["certain"] == []
    assert [c["phase_id"] for c in changes["proposed"]] == ["P1"]


def test_name_linked_with_a_completed_build_is_ignored():
    roadmap = _roadmap([_phase("P1")])
    builds = [_build("b1", "P1", "FAILED"), _build("b2", "P1", "COMPLETED")]

    changes = rr.compute_reconciliation(roadmap["phases"], builds)

    assert changes["certain"] == []
    assert changes["proposed"] == []


def test_apply_backs_up_and_only_touches_certain(tmp_path):
    roadmap_path = tmp_path / "roadmap.json"
    builds_path = tmp_path / "builds.json"
    _write(roadmap_path, _roadmap([
        _phase("P1", build_id="b1"),                     # certain -> applied
        _phase("P2"),                                    # proposed only
        _phase("P3", status="failed", build_id="b3"),    # already terminal
    ]))
    _write(builds_path, {"schema_version": 1, "records": [
        _build("b1", "P1", "FAILED"),
        _build("b2", "P2", "FAILED"),
        _build("b3", "P3", "FAILED"),
    ]})

    result = rr.reconcile(roadmap_path, builds_path, apply=True)

    assert result["applied"] is True
    assert result["backup"] and Path(result["backup"]).exists()
    phases = {p["id"]: p for p in json.loads(Path(roadmap_path).read_text())["phases"]}
    assert phases["P1"]["status"] == "failed"
    assert "reconcil" in phases["P1"]["failure_reason"].lower()
    assert phases["P2"]["status"] == "completed"  # proposed, never auto-applied
    assert phases["P3"]["status"] == "failed"


def test_dry_run_changes_nothing_and_makes_no_backup(tmp_path):
    roadmap_path = tmp_path / "roadmap.json"
    builds_path = tmp_path / "builds.json"
    original = _roadmap([_phase("P1", build_id="b1")])
    _write(roadmap_path, original)
    _write(builds_path, {"schema_version": 1, "records": [_build("b1", "P1", "FAILED")]})

    result = rr.reconcile(roadmap_path, builds_path, apply=False)

    assert result["applied"] is False
    assert result["backup"] is None
    assert json.loads(Path(roadmap_path).read_text()) == original
    assert list(tmp_path.glob("roadmap.json.bak-*")) == []
