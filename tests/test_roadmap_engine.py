import json

import pytest

import core.roadmap_engine as roadmap_engine


@pytest.fixture(autouse=True)
def isolated_roadmap(tmp_path, monkeypatch):
    roadmap_path = tmp_path / "roadmap.json"
    monkeypatch.setattr(roadmap_engine, "ROADMAP_PATH", roadmap_path)
    yield roadmap_path


def _write(path, phases):
    path.write_text(json.dumps({"schema_version": 1, "phases": phases}))


def test_load_roadmap_returns_phases(isolated_roadmap):
    _write(isolated_roadmap, [
        {"id": "A", "name": "Phase A", "status": "completed", "dependencies": [], "priority": 1},
    ])

    roadmap = roadmap_engine.load_roadmap()

    assert len(roadmap["phases"]) == 1
    assert roadmap["phases"][0]["id"] == "A"


def test_load_roadmap_raises_clear_error_when_file_missing(isolated_roadmap):
    with pytest.raises(FileNotFoundError):
        roadmap_engine.load_roadmap()


def test_get_phase_returns_none_for_unknown_id(isolated_roadmap):
    _write(isolated_roadmap, [{"id": "A", "status": "completed", "dependencies": [], "priority": 1}])

    assert roadmap_engine.get_phase("Z") is None


def test_get_remaining_work_excludes_completed_phases(isolated_roadmap):
    _write(isolated_roadmap, [
        {"id": "A", "status": "completed", "dependencies": [], "priority": 1},
        {"id": "B", "status": "pending", "dependencies": ["A"], "priority": 2},
        {"id": "C", "status": "in_progress", "dependencies": ["A"], "priority": 3},
    ])

    remaining = roadmap_engine.get_remaining_work()

    assert {p["id"] for p in remaining} == {"B", "C"}


def test_get_next_phase_returns_lowest_priority_phase_whose_deps_are_done(isolated_roadmap):
    _write(isolated_roadmap, [
        {"id": "A", "status": "completed", "dependencies": [], "priority": 1},
        {"id": "B", "status": "pending", "dependencies": ["A"], "priority": 2},
        {"id": "C", "status": "pending", "dependencies": ["B"], "priority": 3},
    ])

    next_phase = roadmap_engine.get_next_phase()

    assert next_phase["id"] == "B"


def test_get_next_phase_skips_phases_with_incomplete_dependencies(isolated_roadmap):
    _write(isolated_roadmap, [
        {"id": "A", "status": "pending", "dependencies": [], "priority": 1},
        {"id": "B", "status": "pending", "dependencies": ["A"], "priority": 0},
    ])

    next_phase = roadmap_engine.get_next_phase()

    # B has lower priority number but depends on A, which isn't done yet --
    # A must come first regardless of B's priority value.
    assert next_phase["id"] == "A"


def test_get_next_phase_returns_none_when_everything_is_done_or_blocked(isolated_roadmap):
    _write(isolated_roadmap, [
        {"id": "A", "status": "completed", "dependencies": [], "priority": 1},
    ])

    assert roadmap_engine.get_next_phase() is None


def test_get_next_phase_ignores_in_progress_phases(isolated_roadmap):
    _write(isolated_roadmap, [
        {"id": "A", "status": "in_progress", "dependencies": [], "priority": 1},
        {"id": "B", "status": "pending", "dependencies": [], "priority": 2},
    ])

    next_phase = roadmap_engine.get_next_phase()

    assert next_phase["id"] == "B"


def test_mark_phase_status_updates_and_persists(isolated_roadmap):
    _write(isolated_roadmap, [{"id": "A", "status": "pending", "dependencies": [], "priority": 1}])

    updated = roadmap_engine.mark_phase_status("A", "completed")

    assert updated["status"] == "completed"
    assert roadmap_engine.get_phase("A")["status"] == "completed"


def test_mark_phase_status_raises_for_unknown_id(isolated_roadmap):
    _write(isolated_roadmap, [{"id": "A", "status": "pending", "dependencies": [], "priority": 1}])

    with pytest.raises(ValueError):
        roadmap_engine.mark_phase_status("does-not-exist", "completed")


def test_mark_phase_status_rejects_unknown_status_value(isolated_roadmap):
    _write(isolated_roadmap, [{"id": "A", "status": "pending", "dependencies": [], "priority": 1}])

    with pytest.raises(ValueError):
        roadmap_engine.mark_phase_status("A", "definitely_not_a_real_status")


def test_get_progress_summary_reports_counts_by_status(isolated_roadmap):
    _write(isolated_roadmap, [
        {"id": "A", "status": "completed", "dependencies": [], "priority": 1},
        {"id": "B", "status": "completed", "dependencies": [], "priority": 2},
        {"id": "C", "status": "pending", "dependencies": [], "priority": 3},
        {"id": "D", "status": "in_progress", "dependencies": [], "priority": 4},
    ])

    summary = roadmap_engine.get_progress_summary()

    assert summary["total"] == 4
    assert summary["completed"] == 2
    assert summary["pending"] == 1
    assert summary["in_progress"] == 1
    assert summary["percent_complete"] == 50.0
