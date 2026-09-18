"""§23 Dependency management — DAG-aware team execution.

The team executor must honour declared skill dependencies: independent tasks
run concurrently, a task waits for its dependencies, a failed dependency
blocks its dependents (never runs them), and a dependency cycle is detected
instead of hanging.
"""
from __future__ import annotations

import time

from core.teammate.execution import Team


class _Skill:
    def __init__(self, deps=None):
        self.dependencies = list(deps or [])


class _Registry:
    def __init__(self, deps):
        self._skills = {sid: _Skill(d) for sid, d in deps.items()}

    def get(self, sid):
        return self._skills.get(sid)


def _team(deps):
    return Team(mission_id="mis-dep", members=[], plan=None, bus=None,
                skill_registry=_Registry(deps))


def test_dependency_free_tasks_run_concurrently():
    """Three independent tasks must overlap, not serialize."""
    team = _team({s: [] for s in ("a", "b", "c")})

    def runner(_mate, _skill):
        time.sleep(0.3)
        return "ok"

    started = time.monotonic()
    results = team.execute(runner, tasks=["a", "b", "c"])
    elapsed = time.monotonic() - started

    assert [r.status for r in results] == ["completed"] * 3
    assert elapsed < 0.6, f"tasks serialized ({elapsed:.2f}s)"


def test_dependent_task_waits_for_dependency():
    """``b`` depends on ``a`` -> ``a`` completes before ``b`` starts."""
    team = _team({"a": [], "b": ["a"]})
    order = []

    def runner(_mate, skill):
        order.append(("start", skill))
        time.sleep(0.05)
        order.append(("end", skill))
        return "ok"

    results = team.execute(runner, tasks=["a", "b"])

    assert [r.status for r in results] == ["completed", "completed"]
    assert order.index(("end", "a")) < order.index(("start", "b")), order


def test_failed_dependency_blocks_dependent_without_running_it():
    """A failed dependency must block (not run) its dependents."""
    team = _team({"a": [], "b": ["a"]})
    calls = []
    events = []

    def runner(_mate, skill):
        calls.append(skill)
        if skill == "a":
            raise RuntimeError("boom")
        return "ok"

    team.bus = lambda topic, payload: events.append((topic, payload))
    results = team.execute(runner, tasks=["a", "b"])
    by = {r.skill_id: r for r in results}

    assert by["a"].status == "failed"
    assert by["b"].status == "blocked"
    assert "a" in by["b"].error
    assert calls == ["a"], "blocked task must never run"
    assert any(t == "teammate.blocked" for t, _ in events)


def test_dependency_cycle_is_detected_not_hung():
    team = _team({"a": ["b"], "b": ["a"]})

    def runner(_mate, _skill):
        return "ok"

    started = time.monotonic()
    results = team.execute(runner, tasks=["a", "b"])
    elapsed = time.monotonic() - started

    assert {r.status for r in results} == {"blocked"}
    assert elapsed < 2.0


def test_seed_skills_declare_engineering_dependencies():
    """The canonical skills ship with the §23 dependency graph."""
    from core.teammate.skills import SkillRegistry

    skills = SkillRegistry()
    skills.seed_default_15()

    assert "write_code" in skills.get("run_tests").dependencies
    assert "write_code" in skills.get("run_security_scan").dependencies
    assert {"run_tests", "run_security_scan"} <= set(
        skills.get("deploy_service").dependencies)
    # verify_endpoint is generic (QA) — it must not depend on deploy, or every
    # feature mission drags the deploy chain in.
    assert skills.get("verify_endpoint").dependencies == []
    # inspect-only skills stay dependency-free (safe to parallelize)
    assert skills.get("inspect_repository").dependencies == []
