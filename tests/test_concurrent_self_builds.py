"""17A: concurrent self-modifying builds with a serialized merge gate.

Covers the three completion criteria the plan promises tests for:

1. Two self-modifying phases can be in PLANNING/GENERATING/CODE_REVIEW/
   SECURITY_REVIEW simultaneously (advance_roadmap starts a second phase
   even while the first is still non-terminal, each with its own isolated
   clone; the third candidate is not started once the cap is reached).
2. Only one build merges into the live repo at a time -- concurrent merge
   attempts through _merge_self_modifying_build never overlap on the same
   live HEAD.
3. A build whose clone has fallen behind live HEAD is detected and either
   safely re-tested against the current HEAD (clean sync) or fails cleanly
   as a stale-base conflict (dirty sync); never blindly merged.

Also covers exclusivity (completion criterion 4): a phase marked
``"exclusive": true`` on the roadmap entry, or a build whose project_path
resolves to SELF_PROJECT_PATH itself rather than an isolated clone, still
runs with everything else blocked -- the pre-17A single-in-flight safety
is not silently dropped for builds that genuinely need it.
"""

import json
import subprocess
import threading
import time
from pathlib import Path

import pytest

import core.deployment_manager as deploy_mgr
import core.roadmap_manager as roadmap_manager
import core.roadmap_engine as roadmap_engine
import core.build_manager as build_manager


@pytest.fixture(autouse=True)
def isolated_roadmap(tmp_path, monkeypatch):
    roadmap_path = tmp_path / "roadmap.json"
    monkeypatch.setattr(roadmap_engine, "ROADMAP_PATH", roadmap_path)
    return roadmap_path


def _write_roadmap(path, phases):
    path.write_text(json.dumps({"schema_version": 1, "phases": phases}))


def _init_repo(path):
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    (path / "README.md").write_text("live repo\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "initial"], check=True)


def _head(repo):
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True,
    ).stdout.strip()


# -------------------- 1. Two phases in parallel --------------------


def test_two_self_modifying_phases_run_in_parallel(isolated_roadmap, monkeypatch, tmp_path):
    """advance_roadmap starts phase A on cycle 1. On cycle 2, with A still
    non-terminal in its own isolated clone, phase B also starts -- planning
    /generation /review are safe to run concurrently since K3."""
    workspace_root = tmp_path / "self-build-workspaces"
    workspace_root.mkdir()
    monkeypatch.setattr(roadmap_manager, "SELF_BUILD_WORKSPACE_ROOT", workspace_root)
    monkeypatch.setattr(build_manager, "MAX_CONCURRENT_BUILDS", 2)
    monkeypatch.setattr(roadmap_manager, "MAX_CONCURRENT_BUILDS", 2)

    _write_roadmap(isolated_roadmap, [
        {"id": "A", "name": "phase-a", "description": "task A", "completion_criteria": [],
         "status": "pending", "dependencies": [], "priority": 1},
        {"id": "B", "name": "phase-b", "description": "task B", "completion_criteria": [],
         "status": "pending", "dependencies": [], "priority": 2},
        {"id": "C", "name": "phase-c", "description": "task C", "completion_criteria": [],
         "status": "pending", "dependencies": [], "priority": 3},
    ])
    roadmap_manager.enable_autonomous_mode()

    # Give each started build its own real isolated-clone-shaped workspace
    # under SELF_BUILD_WORKSPACE_ROOT so _requires_exclusive() rightly sees
    # them as parallelizable (K3-style clones, not raw SELF_PROJECT_PATH).
    created_paths = []

    def fake_clone(include_plugin=False):
        clone = workspace_root / f"clone-{len(created_paths)}"
        clone.mkdir()
        created_paths.append(str(clone))
        return str(clone)

    monkeypatch.setattr(roadmap_manager, "_create_isolated_self_clone", fake_clone)

    # Cycle 1: no in-flight phases, A should start.
    result_1 = roadmap_manager.advance_roadmap()
    assert result_1["action"] == "started_phase"
    assert result_1["phase_id"] == "A"
    assert roadmap_engine.get_phase("A")["status"] == "in_progress"
    assert roadmap_engine.get_phase("B")["status"] == "pending"
    a_build_id = result_1["build_id"]

    # Stub the in-flight builds as non-terminal (planning/review) so the
    # phase-processing loop doesn't finish them prematurely.
    def stub_build(build_id):
        # A stays in a non-terminal status; anything else answers with the
        # normal builds store.
        for b in build_manager.load_builds():
            if b["id"] == build_id:
                # Force each in-flight build to a non-terminal status.
                if b["status"] not in ("COMPLETED", "FAILED", "ROLLED_BACK"):
                    b["status"] = "WAITING_FOR_ARCHITECTURE_APPROVAL"
                return b
        return None

    monkeypatch.setattr(roadmap_manager, "get_build", stub_build)

    # Cycle 2: A is still active -- B should now start too (cap=2).
    result_2 = roadmap_manager.advance_roadmap()
    assert roadmap_engine.get_phase("A")["status"] == "in_progress"
    assert roadmap_engine.get_phase("B")["status"] == "in_progress"
    assert roadmap_engine.get_phase("C")["status"] == "pending"

    # Both builds have distinct isolated workspaces.
    a_path = roadmap_engine.get_phase("A").get("build_id")
    b_path = roadmap_engine.get_phase("B").get("build_id")
    assert a_path != b_path
    assert len({p for p in created_paths}) == len(created_paths)

    # Cycle 3: cap reached (A and B both active), C stays pending.
    result_3 = roadmap_manager.advance_roadmap()
    assert roadmap_engine.get_phase("C")["status"] == "pending"


def test_advance_roadmap_returns_events_list_for_multiple_phases(isolated_roadmap, monkeypatch, tmp_path):
    """advance_roadmap's return shape gains an ``events`` key: a list of
    per-phase outcomes, alongside the top-level ``action`` chosen from the
    highest-significance event. Consumers that only look at
    ``result["action"]`` keep working unchanged."""
    workspace_root = tmp_path / "self-build-workspaces"
    workspace_root.mkdir()
    monkeypatch.setattr(roadmap_manager, "SELF_BUILD_WORKSPACE_ROOT", workspace_root)
    monkeypatch.setattr(build_manager, "MAX_CONCURRENT_BUILDS", 2)
    monkeypatch.setattr(roadmap_manager, "MAX_CONCURRENT_BUILDS", 2)

    _write_roadmap(isolated_roadmap, [
        {"id": "X", "status": "in_progress", "dependencies": [], "priority": 1, "build_id": "b-x"},
        {"id": "Y", "status": "in_progress", "dependencies": [], "priority": 2, "build_id": "b-y"},
    ])
    roadmap_manager.enable_autonomous_mode()

    def stub(build_id):
        if build_id == "b-x":
            return {"id": "b-x", "status": "COMPLETED", "project_path": str(workspace_root / "cx")}
        return {"id": "b-y", "status": "WAITING_FOR_ARCHITECTURE_APPROVAL",
                "project_path": str(workspace_root / "cy")}

    monkeypatch.setattr(roadmap_manager, "get_build", stub)

    result = roadmap_manager.advance_roadmap()

    # phase_completed outranks waiting_on_human.
    assert result["action"] == "phase_completed"
    assert result["phase_id"] == "X"
    # Both events are present.
    assert "events" in result
    actions = sorted(e["action"] for e in result["events"])
    assert actions == ["phase_completed", "waiting_on_human"]


# -------------------- 2. Exclusivity preserved --------------------


def test_exclusive_flag_prevents_new_starts_while_in_flight(isolated_roadmap, monkeypatch, tmp_path):
    """A phase marked ``"exclusive": true`` while in flight blocks all new
    starts -- the pre-17A single-in-flight guard, kept for the cases that
    still need it (e.g. 17A itself)."""
    workspace_root = tmp_path / "self-build-workspaces"
    workspace_root.mkdir()
    monkeypatch.setattr(roadmap_manager, "SELF_BUILD_WORKSPACE_ROOT", workspace_root)
    monkeypatch.setattr(build_manager, "MAX_CONCURRENT_BUILDS", 4)
    monkeypatch.setattr(roadmap_manager, "MAX_CONCURRENT_BUILDS", 4)

    _write_roadmap(isolated_roadmap, [
        {"id": "X", "status": "in_progress", "exclusive": True, "dependencies": [],
         "priority": 1, "build_id": "b-x"},
        {"id": "Y", "status": "pending", "dependencies": [], "priority": 2},
    ])
    roadmap_manager.enable_autonomous_mode()

    (workspace_root / "cx").mkdir()
    monkeypatch.setattr(
        roadmap_manager, "get_build",
        lambda bid: {"id": bid, "status": "GENERATING", "project_path": str(workspace_root / "cx")},
    )
    monkeypatch.setattr(
        roadmap_manager, "create_build",
        lambda *a, **k: pytest.fail("must not start Y while an exclusive phase X is in flight"),
    )

    result = roadmap_manager.advance_roadmap()

    assert result["action"] == "waiting_on_human"
    assert roadmap_engine.get_phase("Y")["status"] == "pending"


def test_exclusive_candidate_cannot_start_while_anything_else_is_in_flight(isolated_roadmap, monkeypatch, tmp_path):
    """The converse: an exclusive candidate is blocked from starting while
    any (even non-exclusive) phase is in flight."""
    workspace_root = tmp_path / "self-build-workspaces"
    workspace_root.mkdir()
    monkeypatch.setattr(roadmap_manager, "SELF_BUILD_WORKSPACE_ROOT", workspace_root)
    monkeypatch.setattr(build_manager, "MAX_CONCURRENT_BUILDS", 4)
    monkeypatch.setattr(roadmap_manager, "MAX_CONCURRENT_BUILDS", 4)

    _write_roadmap(isolated_roadmap, [
        {"id": "X", "status": "in_progress", "dependencies": [], "priority": 1, "build_id": "b-x"},
        # Y is exclusive and normally the highest-priority candidate.
        {"id": "Y", "status": "pending", "exclusive": True, "dependencies": [], "priority": 2},
    ])
    roadmap_manager.enable_autonomous_mode()

    (workspace_root / "cx").mkdir()
    monkeypatch.setattr(
        roadmap_manager, "get_build",
        lambda bid: {"id": bid, "status": "GENERATING", "project_path": str(workspace_root / "cx")},
    )
    monkeypatch.setattr(
        roadmap_manager, "create_build",
        lambda *a, **k: pytest.fail("must not start exclusive Y while X is in flight"),
    )

    result = roadmap_manager.advance_roadmap()

    assert result["action"] == "waiting_on_human"
    assert roadmap_engine.get_phase("Y")["status"] == "pending"


def test_build_using_self_project_path_directly_is_treated_as_exclusive(isolated_roadmap, monkeypatch, tmp_path):
    """A build whose project_path resolves to SELF_PROJECT_PATH itself
    (rather than an isolated clone under SELF_BUILD_WORKSPACE_ROOT) is the
    unsafe pre-K3-style build the original single-in-flight guard was
    written for. It must still force serialization: nothing else may start
    while such a build is in flight."""
    live_project = tmp_path / "live"
    _init_repo(live_project)
    workspace_root = tmp_path / "self-build-workspaces"
    workspace_root.mkdir()
    monkeypatch.setattr(roadmap_manager, "SELF_PROJECT_PATH", live_project)
    monkeypatch.setattr(roadmap_manager, "SELF_BUILD_WORKSPACE_ROOT", workspace_root)
    monkeypatch.setattr(build_manager, "MAX_CONCURRENT_BUILDS", 4)
    monkeypatch.setattr(roadmap_manager, "MAX_CONCURRENT_BUILDS", 4)

    _write_roadmap(isolated_roadmap, [
        {"id": "X", "status": "in_progress", "dependencies": [], "priority": 1, "build_id": "b-x"},
        {"id": "Y", "status": "pending", "dependencies": [], "priority": 2},
    ])
    roadmap_manager.enable_autonomous_mode()

    # X's build points at SELF_PROJECT_PATH directly -- an unsafe layout.
    monkeypatch.setattr(
        roadmap_manager, "get_build",
        lambda bid: {"id": bid, "status": "GENERATING", "project_path": str(live_project)},
    )
    monkeypatch.setattr(
        roadmap_manager, "create_build",
        lambda *a, **k: pytest.fail("must not start Y while X operates on SELF_PROJECT_PATH itself"),
    )

    result = roadmap_manager.advance_roadmap()

    assert result["action"] == "waiting_on_human"
    assert roadmap_engine.get_phase("Y")["status"] == "pending"


# -------------------- 3. Merge-time conflict between two concurrent builds --------------------


def _make_build_clone(live_repo, workspace_root, build_id, edit_line):
    """Two builds cloned from the same live HEAD, each with its own branch
    editing README.md the same way we'd expect a real generation to have
    committed -- ready to be deployed independently."""
    clone_dir = workspace_root / f"clone-{build_id}"
    subprocess.run(["git", "clone", "-q", str(live_repo), str(clone_dir)], check=True)
    subprocess.run(["git", "-C", str(clone_dir), "checkout", "-q", "-b", f"build-{build_id}"], check=True)
    (clone_dir / "README.md").write_text(edit_line)
    subprocess.run(["git", "-C", str(clone_dir), "commit", "-q", "-am", f"build {build_id} change"], check=True)
    return clone_dir


def test_merge_conflict_between_two_concurrently_completed_builds(tmp_path, monkeypatch):
    """Real live repo + two clones editing the same line. Deploy A merges
    cleanly. Deploy B's pre-merge sync detects the conflict against the
    now-advanced live HEAD and fails with a "Stale base" reason. Live HEAD
    equals A's merge commit, no conflict markers on live, no automated-
    resolution commit exists."""
    live_repo = tmp_path / "live"
    _init_repo(live_repo)

    workspace_root = tmp_path / "self-build-workspaces"
    workspace_root.mkdir()
    monkeypatch.setattr(roadmap_manager, "SELF_PROJECT_PATH", live_repo)
    monkeypatch.setattr(roadmap_manager, "SELF_BUILD_WORKSPACE_ROOT", workspace_root)

    clone_a = _make_build_clone(live_repo, workspace_root, "A", "A changed this line\n")
    clone_b = _make_build_clone(live_repo, workspace_root, "B", "B changed this line\n")

    build_a = {"id": "A", "name": "A-phase", "project_path": str(clone_a)}
    build_b = {"id": "B", "name": "B-phase", "project_path": str(clone_b)}

    # Deploy A first -- clean merge.
    result_a = deploy_mgr.deploy_build(build_a)
    assert result_a["deployed"] is True
    live_after_a = _head(live_repo)
    assert (live_repo / "README.md").read_text() == "A changed this line\n"

    # Now deploy B -- must be detected as a stale-base conflict.
    result_b = deploy_mgr.deploy_build(build_b)
    assert result_b["deployed"] is False
    assert "Stale base" in result_b["reason"]
    # The reason surfaces the git conflict output so an operator can see it.
    assert "conflict" in result_b["reason"].lower() or "CONFLICT" in result_b["reason"]

    # Live HEAD is untouched by B -- still equal to A's merge commit.
    assert _head(live_repo) == live_after_a
    assert (live_repo / "README.md").read_text() == "A changed this line\n"

    # Live tree has no conflict markers anywhere.
    for path in live_repo.rglob("*"):
        if path.is_file() and path.suffix != "":
            try:
                text = path.read_text(errors="ignore")
            except OSError:
                continue
            assert "<<<<<<<" not in text and ">>>>>>>" not in text

    # And no "auto-resolved" commit landed on live -- the log after A must
    # be exactly A's commits (initial + merge), nothing from B.
    log = subprocess.run(
        ["git", "-C", str(live_repo), "log", "--oneline"], capture_output=True, text=True,
    ).stdout
    assert "build-B" not in log
    assert "B changed" not in log


def test_stale_base_clean_sync_retests_before_merging(tmp_path, monkeypatch):
    """A clone falls behind live HEAD because another build merged first
    with a NON-conflicting change. The pre-merge sync moves the clone's
    HEAD; the test suite is re-run against that current base; only then
    does the merge into live proceed. Final merge commit contains both
    changes; pre_merge_retest is recorded on the deployment result."""
    live_repo = tmp_path / "live"
    _init_repo(live_repo)

    workspace_root = tmp_path / "self-build-workspaces"
    workspace_root.mkdir()
    monkeypatch.setattr(roadmap_manager, "SELF_PROJECT_PATH", live_repo)
    monkeypatch.setattr(roadmap_manager, "SELF_BUILD_WORKSPACE_ROOT", workspace_root)

    clone_b = workspace_root / "clone-B"
    subprocess.run(["git", "clone", "-q", str(live_repo), str(clone_b)], check=True)
    subprocess.run(["git", "-C", str(clone_b), "checkout", "-q", "-b", "build-B"], check=True)
    (clone_b / "b_feature.py").write_text("# B\n")
    subprocess.run(["git", "-C", str(clone_b), "add", "."], check=True)
    subprocess.run(["git", "-C", str(clone_b), "commit", "-q", "-m", "B feature"], check=True)

    # A different, NON-conflicting change lands on live after B was cloned.
    (live_repo / "unrelated_a.py").write_text("# from A\n")
    subprocess.run(["git", "-C", str(live_repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(live_repo), "commit", "-q", "-m", "A change"], check=True)

    retest_calls = {"count": 0, "project_path": None}

    def fake_retest(project_path):
        retest_calls["count"] += 1
        retest_calls["project_path"] = project_path
        return {"passed": True, "returncode": 0, "output": "3 passed"}

    monkeypatch.setattr(roadmap_manager, "_run_self_build_tests", fake_retest)

    build_b = {"id": "B", "name": "B-phase", "project_path": str(clone_b)}
    result = deploy_mgr.deploy_build(build_b)

    assert result["deployed"] is True
    # The pre-merge retest was invoked exactly because the clone's HEAD
    # actually moved during the sync -- the retest was NOT skipped.
    assert retest_calls["count"] == 1
    assert retest_calls["project_path"] == str(clone_b)
    assert result["pre_merge_retest"] == {"passed": True, "returncode": 0, "output": "3 passed"}

    # Both changes are present on live now.
    assert (live_repo / "unrelated_a.py").exists()
    assert (live_repo / "b_feature.py").exists()


def test_stale_base_retest_skipped_when_clone_already_current(tmp_path, monkeypatch):
    """Converse: if the pre-merge sync doesn't actually move the clone's
    HEAD (base was already current), the pytest re-run is skipped -- 300s
    of testing when nothing changed is pure latency."""
    live_repo = tmp_path / "live"
    _init_repo(live_repo)

    workspace_root = tmp_path / "self-build-workspaces"
    workspace_root.mkdir()
    monkeypatch.setattr(roadmap_manager, "SELF_PROJECT_PATH", live_repo)
    monkeypatch.setattr(roadmap_manager, "SELF_BUILD_WORKSPACE_ROOT", workspace_root)

    clone = workspace_root / "clone-current"
    subprocess.run(["git", "clone", "-q", str(live_repo), str(clone)], check=True)
    subprocess.run(["git", "-C", str(clone), "checkout", "-q", "-b", "build-current"], check=True)
    (clone / "feature.py").write_text("# feature\n")
    subprocess.run(["git", "-C", str(clone), "add", "."], check=True)
    subprocess.run(["git", "-C", str(clone), "commit", "-q", "-m", "add feature"], check=True)

    monkeypatch.setattr(
        roadmap_manager, "_run_self_build_tests",
        lambda project_path: pytest.fail("must not re-run tests when the clone is already current"),
    )

    build = {"id": "current", "name": "current-phase", "project_path": str(clone)}
    result = deploy_mgr.deploy_build(build)

    assert result["deployed"] is True
    # pre_merge_retest is still recorded, but flagged as skipped so the
    # Approval Center / build record can explain why no re-run happened.
    assert result["pre_merge_retest"] == {"skipped": "clone already current"}


def test_stale_base_retest_failure_blocks_merge(tmp_path, monkeypatch):
    """If the pre-merge retest fails after a clean sync (build is
    incompatible with the new live HEAD's behavior even though the diff
    merged cleanly), the deploy fails cleanly with a "Stale base" reason
    and live HEAD is not moved."""
    live_repo = tmp_path / "live"
    _init_repo(live_repo)

    workspace_root = tmp_path / "self-build-workspaces"
    workspace_root.mkdir()
    monkeypatch.setattr(roadmap_manager, "SELF_PROJECT_PATH", live_repo)
    monkeypatch.setattr(roadmap_manager, "SELF_BUILD_WORKSPACE_ROOT", workspace_root)

    clone = workspace_root / "clone-x"
    subprocess.run(["git", "clone", "-q", str(live_repo), str(clone)], check=True)
    subprocess.run(["git", "-C", str(clone), "checkout", "-q", "-b", "build-x"], check=True)
    (clone / "feature.py").write_text("# feature\n")
    subprocess.run(["git", "-C", str(clone), "add", "."], check=True)
    subprocess.run(["git", "-C", str(clone), "commit", "-q", "-m", "add feature"], check=True)

    # Live advances with a non-conflicting change.
    (live_repo / "a.py").write_text("# a\n")
    subprocess.run(["git", "-C", str(live_repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(live_repo), "commit", "-q", "-m", "A change"], check=True)

    monkeypatch.setattr(
        roadmap_manager, "_run_self_build_tests",
        lambda project_path: {"passed": False, "returncode": 1, "output": "1 failed against new base"},
    )

    live_head_before = _head(live_repo)
    build = {"id": "x", "name": "x-phase", "project_path": str(clone)}
    result = deploy_mgr.deploy_build(build)

    assert result["deployed"] is False
    assert "Stale base" in result["reason"]
    assert "1 failed" in result["reason"]
    # Live was never touched.
    assert _head(live_repo) == live_head_before


# -------------------- 4. Merge serialization --------------------


def test_two_concurrent_merges_never_overlap_on_the_same_live_repo(tmp_path, monkeypatch):
    """Two threads each call _merge_self_modifying_build for a different
    build against the same live repo. The flock in _live_merge_lock must
    keep the critical sections non-overlapping. Both builds land, one full
    merge commit after the other."""
    live_repo = tmp_path / "live"
    _init_repo(live_repo)

    workspace_root = tmp_path / "self-build-workspaces"
    workspace_root.mkdir()
    monkeypatch.setattr(roadmap_manager, "SELF_PROJECT_PATH", live_repo)
    monkeypatch.setattr(roadmap_manager, "SELF_BUILD_WORKSPACE_ROOT", workspace_root)

    def _prepare_clone(build_id, filename):
        clone = workspace_root / f"clone-{build_id}"
        subprocess.run(["git", "clone", "-q", str(live_repo), str(clone)], check=True)
        subprocess.run(["git", "-C", str(clone), "checkout", "-q", "-b", f"build-{build_id}"], check=True)
        (clone / filename).write_text(f"# {build_id}\n")
        subprocess.run(["git", "-C", str(clone), "add", "."], check=True)
        subprocess.run(["git", "-C", str(clone), "commit", "-q", "-m", f"{build_id} feature"], check=True)
        return clone

    clone_p = _prepare_clone("P", "p.py")
    clone_q = _prepare_clone("Q", "q.py")

    # Instrument the actual merge step with enter/exit timestamps so we can
    # assert non-overlap. The wrapper also holds the merge open for a real
    # measurable interval, forcing overlap unless the flock actually
    # serializes -- if the lock is broken, the two intervals overlap by at
    # least the sleep duration.
    real_merge = deploy_mgr._merge_branch_into_live_repo
    intervals = []
    intervals_lock = threading.Lock()

    def slow_merge(live, clone, branch, name):
        tid = threading.get_ident()
        entered = time.monotonic()
        time.sleep(0.15)
        try:
            result = real_merge(live, clone, branch, name)
        finally:
            exited = time.monotonic()
            with intervals_lock:
                intervals.append((tid, entered, exited))
        return result

    monkeypatch.setattr(deploy_mgr, "_merge_branch_into_live_repo", slow_merge)

    # The second-to-merge build will find its clone's HEAD advanced by the
    # first build's merge (fetch+merge origin/main pulls it in) and trigger
    # the pre-merge retest. Stub it to a passing result -- exercising the
    # real pytest against SELF_PROJECT_PATH is a separate test's job, and
    # here the point is the serialization property, not the test suite.
    monkeypatch.setattr(
        roadmap_manager, "_run_self_build_tests",
        lambda project_path: {"passed": True, "returncode": 0, "output": "ok"},
    )

    build_p = {"id": "P", "name": "P-phase", "project_path": str(clone_p)}
    build_q = {"id": "Q", "name": "Q-phase", "project_path": str(clone_q)}

    results = {}

    def deploy(build, key):
        results[key] = deploy_mgr.deploy_build(build)

    t_p = threading.Thread(target=deploy, args=(build_p, "P"))
    t_q = threading.Thread(target=deploy, args=(build_q, "Q"))
    t_p.start()
    t_q.start()
    t_p.join(timeout=30)
    t_q.join(timeout=30)

    # Both builds succeeded -- one merged first, the other synced against
    # its now-advanced live HEAD and merged second.
    assert results["P"]["deployed"] is True
    assert results["Q"]["deployed"] is True

    # Live now contains both files.
    assert (live_repo / "p.py").exists()
    assert (live_repo / "q.py").exists()

    # Two merges observed, non-overlapping in wall-clock time.
    assert len(intervals) == 2
    a, b = sorted(intervals, key=lambda x: x[1])
    assert a[2] <= b[1] + 1e-6, (
        f"merge critical sections overlapped: A={a}, B={b} -- the flock "
        f"did not actually serialize concurrent deployers"
    )
