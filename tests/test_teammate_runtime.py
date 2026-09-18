"""KAI 2.0 Phase 1 — runtime wiring + acceptance tests (directive §46–§54).

These tests run with the model mocked, so they exercise the real object graph
(factory → guard → dispatcher → execution → verification → persistence) without
network calls. The real end-to-end §46/§47 run lives in
``tests/test_teammate_e2e.py`` (skipped unless ``KAI_E2E=1``).
"""
from __future__ import annotations

import time

import pytest


# ── fixtures ────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _reset_singletons():
    from core.teammate.runtime import reset_runtime
    from core.teammate.routes import reset_engine
    reset_runtime()
    reset_engine()
    yield
    reset_runtime()
    reset_engine()


@pytest.fixture
def runtime():
    from core.teammate.runtime import get_runtime
    return get_runtime()


@pytest.fixture
def engine(runtime):
    from core.teammate.engine import WorkforceEngine
    return WorkforceEngine(runtime=runtime, model_verify=False)


def _fake_delegate(monkeypatch, fn=None):
    """Patch ai_router.delegate with a deterministic in-process model."""
    import core.ai.ai_router as ai_router

    calls = []

    def _default(description, **kw):
        calls.append({"description": description, **kw})
        return {
            "provider": kw.get("provider"),
            "task_type": kw.get("task_type") or "unknown",
            "response": f"[mock:{kw.get('provider')}] handled: {str(description)[:60]}",
            "duration_ms": 1,
            "attempts": [],
        }

    monkeypatch.setattr(ai_router, "delegate", fn or _default)
    return calls


# ── runtime construction ────────────────────────────────────────────────────
def test_factory_and_dispatcher_singletons_are_real(runtime):
    from core.teammate.factory import Factory
    from core.teammate.dispatcher import TieredDispatcher
    from core.teammate.runtime import get_factory, get_dispatcher

    assert isinstance(get_factory(), Factory)
    assert get_factory() is get_factory()
    assert isinstance(get_dispatcher(), TieredDispatcher)
    assert get_dispatcher() is get_dispatcher()
    # the dispatcher must have a REAL runner, not the library default (None)
    assert get_dispatcher()._runner is not None
    assert get_dispatcher()._arbiter is not None
    comps = runtime.components()
    for attr in ("registry", "skills", "factory", "guard", "tool_fabric",
                 "dispatcher", "integrator", "linker"):
        assert getattr(comps, attr) is not None


def test_runtime_seeds_skills(runtime):
    for sid in ("inspect_repository", "write_code", "run_tests", "verify_endpoint"):
        assert runtime.skills.get(sid) is not None


# ── §46 basic teammate ──────────────────────────────────────────────────────
def test_acceptance_46_create_teammate_ready(engine):
    mate = engine.create_teammate("coder")
    assert mate["status"] == "READY"
    assert mate["teammate_id"]
    assert "write_code" in mate["skills"]
    assert "code" in mate["capabilities"]


def test_create_teammate_reuses_healthy_match(engine):
    first = engine.create_teammate("coder")
    second = engine.create_teammate("coder")
    assert second["created"] is False
    assert second["teammate_id"] == first["teammate_id"]


def test_task_runner_executes_a_real_result(runtime, monkeypatch):
    calls = _fake_delegate(monkeypatch)
    mate = runtime.registry.get(runtime.create_teammate("coder").teammate.id)
    out = runtime.task_runner(mate, "write_code",
                              instruction="write a tiny add() function")
    assert out["response"].startswith("[mock:")
    assert calls and calls[0]["provider"]
    # AgentGuard stamped an allow decision on the teammate
    assert any(h.get("decision") == "allow" for h in mate.security_history)


# ── §47 team builds a feature ───────────────────────────────────────────────
def test_acceptance_47_form_engineering_team(engine):
    team = engine.form_team("Build a small software feature")
    assert team["status"] == "READY"
    specs = team["specializations"]
    for spec in ("planner", "coder", "qa", "reviewer"):
        assert spec in specs
    assert len(team["member_ids"]) == 4
    from core.teammate.registry import TeammateRegistry
    reg = TeammateRegistry()
    for mid in team["member_ids"]:
        assert reg.get(mid) is not None


def test_acceptance_47_mission_executes_and_verifies(engine, monkeypatch):
    _fake_delegate(monkeypatch)
    mission = engine.create_mission("Build a small software feature", execute=True)
    assert mission["status"] == "COMPLETED"
    assert all(t["status"] == "COMPLETED" for t in mission["tasks"])
    assert mission["verification"]["passed"] is True
    assert mission["tasks"]


# ── §48 worker failure recovery ─────────────────────────────────────────────
def test_acceptance_48_worker_failure_preserves_state_then_resumes(engine, monkeypatch, runtime):
    import core.ai.ai_router as ai_router

    state = {"fail": True}

    def _flaky(description, **kw):
        if state["fail"]:
            raise RuntimeError("worker vanished")
        return {"provider": kw.get("provider"), "response": "recovered output",
                "duration_ms": 1, "attempts": []}

    monkeypatch.setattr(ai_router, "delegate", _flaky)
    mission = engine.create_mission("Build a small software feature", execute=True)
    # state preserved: the mission and its task errors are persisted, not lost
    assert mission is not None
    assert mission["status"] == "FAILED"
    assert any(t["status"] == "FAILED" and t["error"] for t in mission["tasks"])

    from core.teammate.recovery import RecoveryManager
    plan = RecoveryManager().plan_recovery(mission, None, "worker_unavailable",
                                           remaining_work=["write_code"])
    assert plan.restart_mission is False
    assert plan.preserve_mission is True
    assert plan.action == "replace_worker"

    # resume with a healthy worker → completes
    state["fail"] = False
    resumed = engine.execute_mission(mission["id"])
    assert resumed["status"] == "COMPLETED"


# ── §49 model failure failover ──────────────────────────────────────────────
def test_acceptance_49_model_failure_fails_over(runtime, monkeypatch):
    import core.ai.ai_router as ai_router

    # make write_code accept both providers and give the role a 2-provider chain
    runtime.skills.get("write_code").model_requirements["roles"] = ["kai_coder", "local"]
    monkeypatch.setattr(ai_router, "get_effective_providers",
                        lambda tt: ["kai_coder", "local"] if tt == "coding" else ai_router.ROLE_PROVIDERS.get(tt, ["local"]))

    tried = []

    def _failover(description, **kw):
        tried.append(kw.get("provider"))
        if kw.get("provider") == "kai_coder":
            raise ai_router.AllProvidersFailed("kai_coder down")
        return {"provider": "local", "response": "ok from local",
                "duration_ms": 1, "attempts": []}

    monkeypatch.setattr(ai_router, "delegate", _failover)
    mate = runtime.registry.get(runtime.create_teammate("coder").teammate.id)
    out = runtime.task_runner(mate, "write_code", instruction="implement x")
    assert tried == ["kai_coder", "local"]
    assert out["provider"] == "local"
    # the teammate is logically intact (not failed) after a provider failover
    assert runtime.registry.get(mate.id).status in ("READY", "ASSIGNED", "EXECUTING", "VERIFIED", "COMPLETED", "REVIEW", "WAITING")


# ── §50 AgentGuard denial ───────────────────────────────────────────────────
def test_acceptance_50_agentguard_denies_and_audits(runtime, monkeypatch):
    _fake_delegate(monkeypatch)
    mate = runtime.registry.get(runtime.create_teammate("coder").teammate.id)
    mate.capabilities = []  # strip the capability the skill requires
    from core.teammate.execution_guard import GuardDenied
    with pytest.raises(GuardDenied):
        runtime.task_runner(mate, "write_code", instruction="write code")
    assert any(h.get("decision") == "deny" for h in mate.security_history)


# ── §51 memory scoping ──────────────────────────────────────────────────────
def test_acceptance_51_restricted_memory_scope(runtime, monkeypatch):
    from core.teammate.execution_guard import ExecutionGuard

    mate = runtime.registry.get(runtime.create_teammate("coder").teammate.id)
    guard = runtime.guard
    # coder holds only write_code/run_tests → no vault secret paths at all
    assert guard.for_teammate(mate, runtime.skills) == []

    fetched = []

    def _fetch(path, env):
        fetched.append(path)
        return "leaked"

    guard.execute(mate, "write_code", __import__("core.agentguard.guard",
                                                 fromlist=["ActionType"]).ActionType.WRITE,
                  resource="write_code", details="scoped",
                  fn=lambda ctx: ctx.secrets,
                  secret_grants=[("secrets/external/unrelated", "LEAK")],
                  mate_registry=runtime.registry, fetch_secret=_fetch)
    assert fetched == []  # out-of-scope secret never fetched


# ── §52 persistence across restart ──────────────────────────────────────────
def test_acceptance_52_persistence_survives_restart(engine, monkeypatch):
    _fake_delegate(monkeypatch)
    mission = engine.create_mission("Build a small software feature", execute=True)
    mid = mission["id"]

    from core.teammate.runtime import reset_runtime
    from core.teammate.routes import reset_engine
    from core.teammate.engine import WorkforceEngine
    reset_runtime()
    reset_engine()
    fresh = WorkforceEngine(model_verify=False)
    recovered = fresh.get_mission(mid)
    assert recovered is not None
    assert recovered["status"] == mission["status"]
    assert len(recovered["tasks"]) == len(mission["tasks"])
    assert fresh.get_teammate(mission["team_member_ids"][0]) is not None


# ── §53 Telegram mission ────────────────────────────────────────────────────
def test_acceptance_53_telegram_commands(monkeypatch, engine):
    _fake_delegate(monkeypatch)
    from core.team_commands import handle_team_command

    created = handle_team_command("/create-team coder")
    assert "teammate" in created and "READY" in created

    teams = handle_team_command("/teams")
    assert "TEAMS" in teams or "No teams yet" in teams

    started = handle_team_command("/mission Build a tiny feature module")
    assert "mission `mis-" in started and "started" in started
    mid = started.split("`")[1]

    deadline = time.time() + 10
    mission = None
    while time.time() < deadline:
        mission = engine.get_mission(mid)
        if mission and mission["status"] not in ("CREATED", "RUNNING"):
            break
        time.sleep(0.05)
    assert mission is not None
    assert mission["status"] == "COMPLETED"


# ── API surface (Kai + OpenCode) ────────────────────────────────────────────
def _client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from core.teammate import routes
    import core.teammate.routes as routes_mod
    monkeypatch.setattr(routes_mod, "_load_api_token", lambda: "test-token")
    app = FastAPI()
    app.include_router(routes.teammate_router)
    return TestClient(app)


def test_api_requires_auth(monkeypatch):
    client = _client(monkeypatch)
    assert client.get("/api/workforce/teammates").status_code == 401
    assert client.get("/api/workforce/teams").status_code == 401
    assert client.get("/api/missions").status_code == 401


def test_api_create_teammate_team_mission(monkeypatch, engine):
    _fake_delegate(monkeypatch)
    client = _client(monkeypatch)
    h = {"Authorization": "Bearer test-token"}

    r = client.post("/api/workforce/teammates", json={"role": "coder"}, headers=h)
    assert r.status_code == 200, r.text
    mate = r.json()["teammate"]
    assert mate["status"] == "READY"

    r = client.get("/api/workforce/teammates", headers=h)
    assert r.status_code == 200
    assert any(t["teammate_id"] == mate["teammate_id"] for t in r.json()["teammates"])

    r = client.get(f"/api/workforce/teammates/{mate['teammate_id']}", headers=h)
    assert r.status_code == 200

    r = client.post("/api/workforce/teams",
                    json={"requirement": "Build a small software feature"}, headers=h)
    assert r.status_code == 200, r.text
    team = r.json()["team"]
    assert len(team["member_ids"]) == 4

    r = client.get("/api/workforce/teams", headers=h)
    assert r.status_code == 200 and r.json()["teams"]

    r = client.get(f"/api/workforce/teams/{team['id']}", headers=h)
    assert r.status_code == 200

    # §54 Command Center visibility: missions list + task graph
    r = client.post("/api/missions", json={"goal": "Build a small feature"}, headers=h)
    assert r.status_code == 200, r.text
    mission = r.json()["mission"]
    assert mission["status"] == "COMPLETED"

    r = client.get("/api/missions", headers=h)
    assert r.status_code == 200 and r.json()["missions"]

    r = client.get(f"/api/missions/{mission['id']}", headers=h)
    assert r.status_code == 200
    graph = r.json()["mission"]
    assert graph["tasks"] and graph["team_id"]
    assert graph["verification"]["passed"] is True


def test_api_404s(monkeypatch):
    client = _client(monkeypatch)
    h = {"Authorization": "Bearer test-token"}
    assert client.get("/api/workforce/teammates/nope", headers=h).status_code == 404
    assert client.get("/api/workforce/teams/nope", headers=h).status_code == 404
    assert client.get("/api/missions/nope", headers=h).status_code == 404
