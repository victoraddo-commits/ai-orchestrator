"""§37 MODULE INTEGRATION — every KAI module consumes the unified workforce.

These tests prove the integration seam without a network call (``ai_router.delegate``
is mocked): a module requests a capability, KAI reuses/creates a teammate through
the one teammate factory, runs a mission through the one mission engine, and the
outcome is recorded back into the module's Second Brain record with events on the
KAI event bus. The real live cross-module run lives in
``scripts/kai_module_integration_e2e.py`` (evidence capture).
"""
from __future__ import annotations

import pytest


# ── fixtures ────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _reset_singletons():
    from core.teammate.runtime import reset_runtime
    from core.teammate.routes import reset_engine
    from core.integration.module_bridge import reset_bridge
    reset_runtime()
    reset_engine()
    reset_bridge()
    yield
    reset_runtime()
    reset_engine()
    reset_bridge()


@pytest.fixture
def bus():
    from core.kai_event_bus import KAIEventBus
    return KAIEventBus()


@pytest.fixture
def runtime(bus):
    from core.teammate.runtime import TeammateRuntime
    return TeammateRuntime(bus=bus)


@pytest.fixture
def engine(runtime):
    from core.teammate.engine import WorkforceEngine
    return WorkforceEngine(runtime=runtime, model_verify=False)


@pytest.fixture
def stores_base(tmp_path):
    return tmp_path / "sb" / "stores"


@pytest.fixture
def bridge(runtime, engine, bus, stores_base):
    from core.integration.module_bridge import ModuleBridge
    return ModuleBridge(runtime=runtime, engine=engine, bus=bus,
                        stores_base=stores_base)


def _capture(bus, pattern="*"):
    events = []
    bus.subscribe(pattern, lambda topic, envelope: events.append(envelope))
    return events


def _fake_delegate(monkeypatch, fn=None):
    import core.ai.ai_router as ai_router

    calls = []

    def _default(description, **kw):
        calls.append({"description": description, **kw})
        return {
            "provider": kw.get("provider"),
            "task_type": kw.get("task_type") or "unknown",
            "response": f"[mock:{kw.get('provider')}] {str(description)[:40]}",
            "duration_ms": 1,
            "attempts": [],
        }

    monkeypatch.setattr(ai_router, "delegate", fn or _default)
    return calls


# ── catalog coverage (§37 module list) ──────────────────────────────────────
def test_catalog_covers_directive_37_modules():
    from core.integration.module_catalog import MODULE_CATALOG

    required = {
        "juris-kai", "legal-brain", "klaus", "kai-money", "susu", "kai-betting",
        "it-manager", "proxdash", "airdrop-hunter", "android-factory", "kai-net",
        "command-center", "telegram-manager",
    }
    assert required.issubset(MODULE_CATALOG.keys())
    for name, entry in MODULE_CATALOG.items():
        assert entry["specialization"], name
        assert entry["skills"], name
        assert entry["capabilities"], name


def test_every_catalog_skill_is_registered_when_exposed(bridge, runtime):
    summary = bridge.ensure_exposed()
    assert summary["skills"] >= summary["modules"]
    from core.integration.module_catalog import MODULE_SKILLS
    for skill_id in MODULE_SKILLS:
        assert runtime.skills.get(skill_id) is not None, skill_id


# ── exposure: capability registers with the workforce ───────────────────────
def test_expose_registers_module_workers(bridge):
    bridge.ensure_exposed()
    from core.workforce import registry as workforce

    worker = workforce.get("module:juris-kai")
    assert worker is not None
    assert worker.kind == "module"
    assert "legal-ai" in worker.capabilities


def test_expose_is_idempotent(bridge, runtime):
    first = bridge.ensure_exposed()
    skills_after_first = len(runtime.skills.list())
    second = bridge.ensure_exposed()
    assert second["skills"] == first["skills"]
    assert len(runtime.skills.list()) == skills_after_first


# ── request path: capability -> teammate -> mission ─────────────────────────
def test_request_capability_creates_teammate_and_completes_mission(
        bridge, runtime, monkeypatch):
    _fake_delegate(monkeypatch)
    events = _capture(bridge.bus)

    result = bridge.request_capability(
        "juris-kai", "legal-ai",
        objective="Summarise the doctrine of consideration in Ghana contract law.",
        execute=True)

    assert result["module"] == "juris-kai"
    assert result["capability"] == "legal-ai"
    mid = result["mission_id"]

    mission = bridge.engine.get_mission(mid)
    assert mission is not None
    assert mission["status"] == "COMPLETED", mission
    assert all(t["status"] == "COMPLETED" for t in mission["tasks"]), mission["tasks"]

    mate = runtime.registry.get(result["teammate_id"])
    assert mate is not None
    assert mate.specialization == "legal_researcher"
    assert "legal_research" in mate.skills

    topics = [e["topic"] for e in events]
    assert "module.capability.requested" in topics
    assert "module.mission.recorded" in topics
    recorded = next(e for e in events if e["topic"] == "module.mission.recorded")
    assert recorded["payload"]["mission_id"] == mid


def test_request_reuses_existing_teammate(bridge, monkeypatch):
    _fake_delegate(monkeypatch)
    first = bridge.request_capability("susu", "group-savings", objective="Reconcile group A")
    second = bridge.request_capability("susu", "group-savings", objective="Reconcile group B")

    assert second["teammate_id"] == first["teammate_id"]
    assert second["created"] is False


def test_request_unknown_module_is_rejected(bridge):
    with pytest.raises(KeyError):
        bridge.request_capability("not-a-real-module", "anything")


def test_descriptor_only_module_can_request(bridge, monkeypatch):
    from core import module_registry

    module_registry.reset()
    module_registry.register_module(
        "demo-module", "1.0.0", "A demo module",
        capabilities=["demo-capability"])
    try:
        _fake_delegate(monkeypatch)
        result = bridge.request_capability("demo-module", "demo-capability",
                                           objective="Perform the demo task")
        assert result["specialization"] == "demo_module_operator"
        mission = bridge.engine.get_mission(result["mission_id"])
        assert mission["status"] == "COMPLETED", mission
    finally:
        module_registry.reset()


# ── outcome recorded back into the module (Second Brain + journal) ──────────
def test_outcome_is_recorded_in_second_brain_and_journal(
        bridge, stores_base, monkeypatch):
    import json

    _fake_delegate(monkeypatch)
    result = bridge.request_capability(
        "it-manager", "health-monitoring",
        objective="Check the health of the orchestrator services.")

    entry = bridge.get_request(result["mission_id"])
    assert entry["status"] == "COMPLETED"
    assert entry["second_brain_record_id"]

    records = stores_base / "operational" / "records.jsonl"
    assert records.exists()
    rows = [json.loads(line) for line in records.read_text().splitlines() if line.strip()]
    entity = "module:it-manager:health-monitoring"
    assert any(r.get("entity") == entity for r in rows), rows
    match = next(r for r in rows if r.get("entity") == entity)
    assert match["fact"]["mission_id"] == result["mission_id"]
    assert match["fact"]["module"] == "it-manager"


def test_failed_mission_records_failure_outcome(bridge, monkeypatch):
    def _boom(description, **kw):
        raise RuntimeError("worker vanished")

    _fake_delegate(monkeypatch, _boom)
    bridge.engine.max_task_retries = 0
    bridge.engine.max_attempts = 0

    result = bridge.request_capability("kai-net", "network-health",
                                       objective="Probe the network mesh")
    entry = bridge.get_request(result["mission_id"])
    assert entry["status"] == "FAILED"
    assert entry["second_brain_record_id"]


def test_list_module_requests_filters_by_module(bridge, monkeypatch):
    _fake_delegate(monkeypatch)
    bridge.request_capability("susu", "group-savings", objective="a")
    bridge.request_capability("juris-kai", "legal-ai", objective="b")

    susu = bridge.list_requests(module="susu")
    assert susu and all(r["module"] == "susu" for r in susu)


def test_narrowed_skill_request(bridge, monkeypatch):
    _fake_delegate(monkeypatch)
    result = bridge.request_capability(
        "juris-kai", "legal_research", objective="x",
        skills=["legal_research"])
    assert result["skills"] == ["legal_research"]
    mission = bridge.engine.get_mission(result["mission_id"])
    assert [t["skill_id"] for t in mission["tasks"]] == ["legal_research"]


# ── real module call sites consume the workforce ────────────────────────────
def test_juris_kai_research_command_uses_workforce(bridge, monkeypatch):
    _fake_delegate(monkeypatch)
    monkeypatch.setattr("core.integration.module_bridge.get_bridge",
                        lambda: bridge)
    # Strict grounding: research only reaches the workforce with a source.
    monkeypatch.setattr(
        "core.juris_kai.grounding.retrieve",
        lambda q, limit=3: {
            "docs": [{"id": 1, "title": "Contracts Act, 1960",
                      "citation": "Act 25", "store_mode": "full",
                      "chunk_content": "Offer and acceptance. " * 20}],
            "verdict": "GROUNDED", "stage": 1})
    import core.juris_kai.commands as juris

    out = juris.handle_research("explain consideration in Ghana", {},
                                {"account_id": "acct-1"})
    assert "[mock:" in out


def test_team_commands_expose_and_request_module_capability(bridge, monkeypatch):
    _fake_delegate(monkeypatch)
    monkeypatch.setattr("core.integration.module_bridge.get_bridge",
                        lambda: bridge)
    from core.team_commands import handle_team_command

    listed = handle_team_command("/module juris-kai")
    assert "legal_researcher" in listed

    started = handle_team_command(
        "/module-request telegram-manager telegram-management Check allowlist")
    assert "mission `mis-" in started
    assert "capability `telegram-management`" in started


# ── API surface ─────────────────────────────────────────────────────────────
def _client(bridge, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import core.integration.routes as routes_mod
    import core.teammate.routes as teammate_routes

    monkeypatch.setattr(teammate_routes, "_load_api_token", lambda: "test-token")
    monkeypatch.setattr(routes_mod, "get_bridge", lambda: bridge)
    app = FastAPI()
    app.include_router(routes_mod.integration_router)
    return TestClient(app)


def test_api_requires_auth(bridge, monkeypatch):
    client = _client(bridge, monkeypatch)
    assert client.get("/api/integration/modules").status_code == 401
    assert client.post(
        "/api/integration/modules/juris-kai/capabilities/legal-ai/request",
        json={"objective": "x"}).status_code == 401


def test_api_lists_and_requests_capability(bridge, monkeypatch):
    _fake_delegate(monkeypatch)
    client = _client(bridge, monkeypatch)
    headers = {"Authorization": "Bearer test-token"}

    listed = client.get("/api/integration/modules", headers=headers)
    assert listed.status_code == 200, listed.text
    modules = {m["module"] for m in listed.json()["modules"]}
    assert "juris-kai" in modules

    r = client.post(
        "/api/integration/modules/juris-kai/capabilities/legal-ai/request",
        headers=headers,
        json={"objective": "Research Ghanaian contract law", "execute": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["module"] == "juris-kai"
    assert body["mission"]["status"] == "COMPLETED"

    journal = client.get(
        "/api/integration/modules/juris-kai/requests", headers=headers)
    assert journal.status_code == 200
    assert journal.json()["requests"]
