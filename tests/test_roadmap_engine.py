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


def test_get_candidate_phases_returns_all_eligible_phases_not_just_one(isolated_roadmap):
    _write(isolated_roadmap, [
        {"id": "A", "status": "completed", "dependencies": [], "priority": 1},
        {"id": "B", "status": "pending", "dependencies": ["A"], "priority": 3},
        {"id": "C", "status": "pending", "dependencies": ["A"], "priority": 2},
        {"id": "D", "status": "pending", "dependencies": ["Z"], "priority": 0},
    ])

    candidates = {p["id"] for p in roadmap_engine.get_candidate_phases()}

    # B and C are both eligible (dependency A is done); D is excluded even
    # though it has the lowest priority, since its dependency isn't done.
    assert candidates == {"B", "C"}


def test_get_next_phase_still_picks_lowest_priority_among_candidates(isolated_roadmap):
    _write(isolated_roadmap, [
        {"id": "A", "status": "completed", "dependencies": [], "priority": 1},
        {"id": "B", "status": "pending", "dependencies": ["A"], "priority": 3},
        {"id": "C", "status": "pending", "dependencies": ["A"], "priority": 2},
    ])

    assert roadmap_engine.get_next_phase()["id"] == "C"


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


def test_add_phase_appends_a_new_phase(isolated_roadmap):
    _write(isolated_roadmap, [{"id": "A", "status": "completed", "dependencies": [], "priority": 1}])

    new_phase = roadmap_engine.add_phase(
        id="B", name="New phase", description="desc", status="pending",
        dependencies=["A"], priority=2,
    )

    assert new_phase["id"] == "B"
    assert roadmap_engine.get_phase("B")["status"] == "pending"
    assert len(roadmap_engine.load_roadmap()["phases"]) == 2


def test_add_phase_rejects_duplicate_id(isolated_roadmap):
    _write(isolated_roadmap, [{"id": "A", "status": "completed", "dependencies": [], "priority": 1}])

    with pytest.raises(ValueError):
        roadmap_engine.add_phase(id="A", name="dup", description="", status="pending", dependencies=[], priority=2)


def test_add_phase_rejects_unresolvable_dependency(isolated_roadmap):
    _write(isolated_roadmap, [{"id": "A", "status": "completed", "dependencies": [], "priority": 1}])

    with pytest.raises(ValueError):
        roadmap_engine.add_phase(
            id="B", name="new", description="", status="pending",
            dependencies=["does-not-exist"], priority=2,
        )


def test_add_phase_defaults_status_to_proposed_when_not_given(isolated_roadmap):
    _write(isolated_roadmap, [])

    new_phase = roadmap_engine.add_phase(id="X", name="n", description="d", dependencies=[], priority=1)

    assert new_phase["status"] == "proposed"


def test_get_next_phase_never_returns_a_proposed_phase(isolated_roadmap):
    # "proposed" is deliberately not "pending" -- get_next_phase() (used by
    # the autonomous manager) must never pick up AI-generated phases until a
    # human promotes them to "pending". Without this, "AI can propose
    # roadmap items" would silently become "AI can queue its own work".
    _write(isolated_roadmap, [{"id": "X", "status": "proposed", "dependencies": [], "priority": 1}])

    assert roadmap_engine.get_next_phase() is None


def test_update_phase_merges_arbitrary_fields(isolated_roadmap):
    _write(isolated_roadmap, [{"id": "A", "status": "pending", "dependencies": [], "priority": 1}])

    updated = roadmap_engine.update_phase("A", status="in_progress", build_id="build-42")

    assert updated["status"] == "in_progress"
    assert updated["build_id"] == "build-42"
    assert roadmap_engine.get_phase("A")["build_id"] == "build-42"


def test_update_phase_raises_for_unknown_id(isolated_roadmap):
    _write(isolated_roadmap, [{"id": "A", "status": "pending", "dependencies": [], "priority": 1}])

    with pytest.raises(ValueError):
        roadmap_engine.update_phase("does-not-exist", status="completed")


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
