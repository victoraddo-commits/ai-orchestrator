"""Phase 13H policy tests.

These tests are the contract for the 6-level autonomy model. They
enforce two things per level N:

  1. Level N unlocks its own advertised capabilities (positive test:
     the gate returns True/proceeds at N).
  2. Level N-1 does NOT unlock those same capabilities (negative test:
     the gate returns False/refuses at N-1).

And two invariants that must hold at every level, including Level 5:

  A. The ARCHITECTURE_APPROVED gate in core/build_manager.py stays
     human-only -- no autonomy level bypasses it.
  B. The DEPLOY_APPROVAL gate in core/build_manager.py stays human-only
     -- no autonomy level bypasses it.

Anything that would let the system approve its own architecture or its
own deploy is a change these tests must catch.
"""

import pytest

import core.autonomy as autonomy
import core.kai.planner as planner
import core.roadmap_manager as roadmap_manager
from core.build_manager import (
    BUILD_TRANSITIONS,
    approve_architecture,
    approve_deploy,
    create_build,
    load_builds,
    save_builds,
    submit_answer,
)
from core.lifecycle import transition


# ---------------------------------------------------------------------------
# Fresh-install defaults (13H's decided-up-front spec: default = Level 1)
# ---------------------------------------------------------------------------


def test_default_level_is_1_observe_and_report():
    """A fresh install with no autonomy_level.json and no legacy
    autonomous_mode.json falls back to Level 1 -- the 13H default.
    Level 0 (fully manual) is intentionally NEVER reached by default;
    it must be set explicitly."""
    record = autonomy.get_autonomy_level()
    assert record["level"] == 1
    assert record["set_by"] == autonomy.SYSTEM_DEFAULT_IDENTITY


def test_first_read_writes_out_a_persistent_default_record():
    """The first get_autonomy_level() call synthesizes a Level-1
    record and persists it to disk, so the UI, the audit trail, and
    every future read see the same on-disk state."""
    autonomy.get_autonomy_level()
    stored = autonomy.load(autonomy.AUTONOMY_LEVEL_FILE)
    assert isinstance(stored, dict)
    assert stored.get("level") == 1
    assert stored.get("set_by") == autonomy.SYSTEM_DEFAULT_IDENTITY
    assert "updated_at" in stored


# ---------------------------------------------------------------------------
# Legacy migration (autonomous_mode.json -> autonomy_level.json)
# ---------------------------------------------------------------------------


def test_legacy_enabled_true_migrates_to_level_4():
    """Pre-13H ``enabled: true`` had exactly Level-4 semantics
    (drive builds through, still respect the human approval gates).
    Migration MUST produce Level 4 -- anything higher would be a
    silent privilege escalation, anything lower would silently roll
    back an operator's prior consent."""
    autonomy.save(autonomy.LEGACY_AUTONOMOUS_MODE_FILE, {"enabled": True})

    record = autonomy.get_autonomy_level()

    assert record["level"] == 4
    assert record["set_by"] == autonomy.SYSTEM_DEFAULT_IDENTITY


def test_legacy_enabled_false_migrates_to_level_1_not_level_0():
    """Pre-13H ``enabled: false`` meant "the roadmap loop is idle",
    which is exactly Level 1's guarantee -- NOT Level 0 (which would
    also silence proposals, observe/report, everything). This case
    is the one an operator could easily get wrong; the migration
    must not silently reach for Level 0."""
    autonomy.save(autonomy.LEGACY_AUTONOMOUS_MODE_FILE, {"enabled": False})

    record = autonomy.get_autonomy_level()

    assert record["level"] == 1


def test_malformed_legacy_file_migrates_to_level_1():
    """A garbled legacy file must not crash startup and must not
    silently promote to Level 4; treat it exactly like enabled:false."""
    autonomy.save(autonomy.LEGACY_AUTONOMOUS_MODE_FILE, {"unexpected": "shape"})

    record = autonomy.get_autonomy_level()

    assert record["level"] == 1


# ---------------------------------------------------------------------------
# set_autonomy_level validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_level", [-1, 6, 42, None, "high", 1.5])
def test_set_autonomy_level_rejects_out_of_range_values(bad_level):
    with pytest.raises(ValueError):
        autonomy.set_autonomy_level(bad_level, "operator@example")


def test_set_autonomy_level_requires_operator_identity():
    with pytest.raises(ValueError):
        autonomy.set_autonomy_level(3, "")


@pytest.mark.parametrize("level", list(autonomy.VALID_LEVELS))
def test_set_autonomy_level_accepts_every_valid_level(level):
    record = autonomy.set_autonomy_level(level, "operator@example")
    assert record["level"] == level
    assert record["set_by"] == "operator@example"


def test_set_autonomy_level_records_operator_identity_verbatim():
    """The ``set_by`` field is the audit trail for who last changed
    the autonomy state. It must be recorded exactly as passed in --
    no truncation, no normalization -- so an audit reader can match
    it to the operator record that produced it."""
    identity = "Fable (@fable_dev) tg:987654321"
    record = autonomy.set_autonomy_level(4, identity)
    assert record["set_by"] == identity


# ---------------------------------------------------------------------------
# Per-level capability contract
# ---------------------------------------------------------------------------


CAPABILITY_MIN_LEVELS = {
    "observe_and_report": (1, autonomy.can_observe_and_report),
    "create_proposals": (2, autonomy.can_create_proposals),
    "create_branches_and_builds": (3, autonomy.can_create_branches_and_builds),
    "execute_through_deploy_approval": (4, autonomy.can_execute_through_deploy_approval),
    "manage_roadmap_continuously": (5, autonomy.can_manage_roadmap_continuously),
}


@pytest.mark.parametrize("capability,min_level,gate", [
    (name, ml, g) for name, (ml, g) in CAPABILITY_MIN_LEVELS.items()
])
def test_capability_unlocked_at_its_advertised_minimum_level(capability, min_level, gate):
    """Level N unlocks its own capability."""
    autonomy.set_autonomy_level(min_level, "test")
    assert gate() is True, (
        f"{capability!r} must be unlocked at level {min_level}"
    )


@pytest.mark.parametrize("capability,min_level,gate", [
    (name, ml, g) for name, (ml, g) in CAPABILITY_MIN_LEVELS.items()
])
def test_capability_locked_below_its_advertised_minimum_level(capability, min_level, gate):
    """Level N-1 does NOT unlock N's capability. This is what stops
    a level upgrade from silently smearing across capabilities: no
    level lets the operator do more than they explicitly chose."""
    if min_level == autonomy.MIN_LEVEL:
        pytest.skip(
            f"{capability!r} has no lower level to test against "
            f"(already at MIN_LEVEL={autonomy.MIN_LEVEL})"
        )
    autonomy.set_autonomy_level(min_level - 1, "test")
    assert gate() is False, (
        f"{capability!r} must NOT be available at level {min_level - 1} "
        f"(only unlocked at level {min_level})"
    )


@pytest.mark.parametrize("level", list(autonomy.VALID_LEVELS))
def test_level_0_locks_everything_including_observe_and_report(level):
    """Level 0 is 'nothing automatic'. No capability should be
    True at Level 0 -- this is the property that lets an operator
    kill autonomous behavior entirely without deleting anything.

    (Parameterized only so the test name records which levels were
    exercised; the actual assertion is only about Level 0.)"""
    if level != 0:
        pytest.skip("only asserted for Level 0")
    autonomy.set_autonomy_level(0, "test")
    for name, (_, gate) in CAPABILITY_MIN_LEVELS.items():
        assert gate() is False, f"level 0 must NOT unlock {name!r}"


# ---------------------------------------------------------------------------
# Concrete hook-point tests: gates enforce the level, not just the predicate
# ---------------------------------------------------------------------------


def _stub_planner_signals(monkeypatch):
    monkeypatch.setattr(planner, "load_roadmap", lambda: {"phases": []})
    monkeypatch.setattr(planner, "get_progress_summary", lambda: {"total": 0})
    monkeypatch.setattr(planner, "analyze_health", lambda: [])
    monkeypatch.setattr(planner, "get_build_history", lambda: [])
    monkeypatch.setattr(planner, "get_usage_history", lambda: [])
    monkeypatch.setattr(planner.provider_health, "get_all_quota_snapshots", lambda: {})
    monkeypatch.setattr(planner, "load_builds", lambda: [])
    monkeypatch.setattr(
        planner, "delegate",
        lambda desc, **kw: {
            "provider": "test", "task_type": "planning",
            "response": '[{"title": "X", "description": "d"}]', "duration_ms": 1,
        },
    )


def test_generate_proposal_refuses_at_level_1_and_writes_nothing(monkeypatch):
    """Level 1 is observe + report only. generate_proposal must NOT
    persist a proposal there -- and must NOT even call the LLM (an
    LLM call has real cost and is exactly the sort of automatic
    activity Level 1 excludes)."""
    autonomy.set_autonomy_level(1, "test")

    _stub_planner_signals(monkeypatch)
    delegate_called = {"n": 0}

    def _counting_delegate(desc, **kw):
        delegate_called["n"] += 1
        return {"provider": "test", "task_type": "planning", "response": "[]", "duration_ms": 1}

    monkeypatch.setattr(planner, "delegate", _counting_delegate)

    result = planner.generate_proposal()

    assert result["status"] == "disabled"
    assert result["created"] == []
    assert delegate_called["n"] == 0, (
        "Level 1 must not spend an LLM call to generate a proposal"
    )
    assert planner.load_proposals() == []


def test_generate_proposal_works_at_level_2(monkeypatch):
    autonomy.set_autonomy_level(2, "test")
    _stub_planner_signals(monkeypatch)

    result = planner.generate_proposal()

    assert result["status"] == "ok"
    assert len(result["created"]) == 1
    assert len(planner.load_proposals()) == 1


def test_advance_roadmap_disabled_below_level_3(monkeypatch):
    """Cutting branches / starting builds is a Level-3-or-higher
    activity. Level 2 is 'propose only'; the roadmap loop must stay
    idle there."""
    autonomy.set_autonomy_level(2, "test")

    monkeypatch.setattr(
        roadmap_manager, "create_build",
        lambda *a, **k: pytest.fail("Level 2 must not start builds"),
    )

    result = roadmap_manager.advance_roadmap()
    assert result["action"] == "disabled"


def test_advance_roadmap_runs_at_level_3(monkeypatch, tmp_path):
    """Level 3 unlocks branch/build creation. advance_roadmap must
    now be willing to run (it may still short-circuit with a
    'nothing_to_do' when the roadmap is empty, but not with
    'disabled')."""
    autonomy.set_autonomy_level(3, "test")

    # Point the roadmap at an empty in-memory list so advance_roadmap
    # doesn't try to touch this repo's real roadmap.json.
    import core.roadmap_engine as roadmap_engine
    monkeypatch.setattr(roadmap_engine, "load_roadmap", lambda: {"phases": []})

    result = roadmap_manager.advance_roadmap()

    assert result["action"] != "disabled", (
        "Level 3 must not report 'disabled'; the gate has to let the "
        "loop actually execute"
    )


def test_promote_proposal_refuses_below_level_5(monkeypatch):
    """Promoting an approved proposal into a new roadmap phase is
    phase-generation logic per the 13H spec's Level 5 row. Level 4
    is the exact pre-13H 'enabled: true' behavior -- it drives
    existing phases, it doesn't invent new ones."""
    autonomy.set_autonomy_level(5, "seed-for-generate")
    _stub_planner_signals(monkeypatch)

    proposal = planner.generate_proposal()["created"][0]
    planner.update_proposal_status(proposal["id"], "under_review")
    planner.update_proposal_status(proposal["id"], "approved")

    # Drop to Level 4 and confirm promotion is blocked.
    autonomy.set_autonomy_level(4, "test")

    with pytest.raises(PermissionError):
        planner.promote_proposal(proposal["id"], "PHASE-13H-TEST")


def test_promote_proposal_allowed_at_level_5(monkeypatch):
    autonomy.set_autonomy_level(5, "test")
    _stub_planner_signals(monkeypatch)

    proposal = planner.generate_proposal()["created"][0]
    planner.update_proposal_status(proposal["id"], "under_review")
    planner.update_proposal_status(proposal["id"], "approved")

    created = []
    monkeypatch.setattr(
        planner, "add_phase",
        lambda **kw: (created.append(kw) or {"id": kw["id"], "status": "proposed", **kw}),
    )

    planner.promote_proposal(proposal["id"], "PHASE-13H-TEST")

    assert len(created) == 1
    assert created[0]["id"] == "PHASE-13H-TEST"


# ---------------------------------------------------------------------------
# The two invariants: NO level bypasses ARCHITECTURE_APPROVED / DEPLOY_APPROVAL.
# ---------------------------------------------------------------------------


def _fresh_build_at(status_target, tmp_path):
    """Create a build and fast-forward its status to one just before
    the human-approval gate we want to exercise. Returns the build
    dict as stored in memory/builds.json."""
    build = create_build(name="test", description="d", project_path=str(tmp_path))
    builds = load_builds()
    for b in builds:
        if b["id"] == build["id"]:
            for status in status_target:
                transition(b, status, BUILD_TRANSITIONS)
            break
    save_builds(builds)
    return next(b for b in load_builds() if b["id"] == build["id"])


@pytest.mark.parametrize("level", list(autonomy.VALID_LEVELS))
def test_no_autonomy_level_grants_the_system_permission_to_approve_architecture(
    level, tmp_path, monkeypatch
):
    """Invariant: the ARCHITECTURE_APPROVED transition is triggered
    by core.build_manager.approve_architecture, which is only ever
    called by the API endpoint POST /builds/{id}/approve-architecture,
    which is gated by require_bridge_token. NO code path anywhere
    else -- including the autonomous roadmap loop, the planner, the
    scheduler -- may call approve_architecture on its own. This test
    proves it at every level: at any level, if the system itself
    (not an authenticated human via the API) tries to promote a
    build to ARCHITECTURE_APPROVED without approve_architecture being
    invoked, that never happens.

    Concretely: we scan advance_roadmap()'s output at every level and
    assert it never returns a status that means 'I approved the
    architecture myself'. There is no such status by design
    (ARCHITECTURE_APPROVED is only reached through approve_architecture),
    but this test makes that guarantee explicit and re-runnable."""
    autonomy.set_autonomy_level(level, "test")

    calls = {"n": 0}

    def _forbidden_approve_architecture(*args, **kwargs):
        calls["n"] += 1
        raise AssertionError(
            "approve_architecture called by the autonomous loop -- 13H "
            "invariant broken: no level may bypass the human approval gate"
        )

    # Any code path that would auto-approve architecture would have to
    # reach into build_manager.approve_architecture. Sabotage it.
    import core.build_manager as bm
    monkeypatch.setattr(bm, "approve_architecture", _forbidden_approve_architecture)

    # Also patch it wherever it was imported.
    import core.api as api_mod
    if hasattr(api_mod, "approve_architecture"):
        monkeypatch.setattr(api_mod, "approve_architecture", _forbidden_approve_architecture)

    # Empty roadmap so advance_roadmap has no phase to touch.
    import core.roadmap_engine as roadmap_engine
    monkeypatch.setattr(roadmap_engine, "load_roadmap", lambda: {"phases": []})

    roadmap_manager.advance_roadmap()

    assert calls["n"] == 0, (
        f"level {level} must never call approve_architecture on its own"
    )


@pytest.mark.parametrize("level", list(autonomy.VALID_LEVELS))
def test_no_autonomy_level_grants_the_system_permission_to_approve_deploy(
    level, tmp_path, monkeypatch
):
    """Companion invariant to the ARCHITECTURE_APPROVED test: the
    DEPLOY_APPROVAL gate stays human-only at every level, including
    Level 5. The autonomous roadmap loop must never call
    build_manager.approve_deploy on its own behalf."""
    autonomy.set_autonomy_level(level, "test")

    calls = {"n": 0}

    def _forbidden_approve_deploy(*args, **kwargs):
        calls["n"] += 1
        raise AssertionError(
            "approve_deploy called by the autonomous loop -- 13H "
            "invariant broken: no level may bypass the human deploy gate"
        )

    import core.build_manager as bm
    monkeypatch.setattr(bm, "approve_deploy", _forbidden_approve_deploy)

    import core.api as api_mod
    if hasattr(api_mod, "approve_deploy"):
        monkeypatch.setattr(api_mod, "approve_deploy", _forbidden_approve_deploy)

    import core.roadmap_engine as roadmap_engine
    monkeypatch.setattr(roadmap_engine, "load_roadmap", lambda: {"phases": []})

    roadmap_manager.advance_roadmap()

    assert calls["n"] == 0, (
        f"level {level} must never call approve_deploy on its own"
    )


def test_autonomy_module_never_calls_approve_gates():
    """Belt-and-braces textual check: core/autonomy.py, the
    module that decides levels, must never reach into
    build_manager to auto-approve anything. If a future change
    ever imports approve_architecture / approve_deploy from
    build_manager here, this test will catch it -- before it
    becomes a runtime bypass."""
    import inspect
    import core.autonomy as autonomy_module

    source = inspect.getsource(autonomy_module)
    assert "approve_architecture" not in source
    assert "approve_deploy" not in source


# ---------------------------------------------------------------------------
# Backwards-compat shims: old callers still see coherent behavior
# ---------------------------------------------------------------------------


def test_is_autonomous_mode_enabled_returns_true_only_from_level_4():
    """The old binary flag is preserved as a shim: it must map
    exactly to level >= 4 so pre-13H callers see the same behavior."""
    for level in autonomy.VALID_LEVELS:
        autonomy.set_autonomy_level(level, "test")
        assert autonomy.is_autonomous_mode_enabled() is (level >= 4), (
            f"is_autonomous_mode_enabled() at level {level} disagreed with "
            f"the level>=4 rule"
        )


def test_enable_autonomous_mode_shim_sets_level_4_with_operator_identity():
    autonomy.enable_autonomous_mode("Fable tg:1")
    record = autonomy.get_autonomy_level()
    assert record["level"] == 4
    assert record["set_by"] == "Fable tg:1"


def test_disable_autonomous_mode_shim_sets_level_1_not_level_0():
    """Old "disable" semantics == "stop the roadmap loop" == Level 1.
    Reaching Level 0 must remain an explicit choice."""
    autonomy.set_autonomy_level(4, "seed")
    autonomy.disable_autonomous_mode("Fable tg:1")
    record = autonomy.get_autonomy_level()
    assert record["level"] == 1
