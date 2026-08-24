"""Tests for the KAI Tool Bus (JARVIS P2/P3).

Run: cd /project/ai-orchestrator && python3 -m pytest tests/test_kai_tools.py -q
"""

import json

import pytest

from core.kai_tools import builtin  # noqa: F401 — registers all tools
from core.kai_tools import policy
from core.kai_tools.registry import (CONTROLLED, HIGH_RISK, REGISTRY, SAFE,
                                     ToolSpec, describe_all)


@pytest.fixture()
def isolated_policy(tmp_path, monkeypatch):
    """Point audit + autonomy files at a temp dir; default autonomy 1."""
    monkeypatch.setattr(policy, "AUDIT_PATH", tmp_path / "tool_audit.jsonl")
    monkeypatch.setattr(policy, "AUTONOMY_LEVEL_FILE", tmp_path / "autonomy_level.json")
    (tmp_path / "autonomy_level.json").write_text(json.dumps({"level": 1}))
    return tmp_path


def test_registry_basics():
    specs = {s["id"]: s for s in describe_all()}
    assert "kai.system.health" in specs
    assert specs["kai.system.health"]["risk"] == SAFE
    assert specs["kai.docker.container_action"]["risk"] == CONTROLLED
    # ids are namespaced
    for sid in specs:
        assert sid.startswith("kai.")


def test_unknown_tool_fails_cleanly():
    r = policy.execute("kai.does.not_exist")
    assert not r.ok and "unknown tool" in (r.error or "")


def test_safe_tool_auto_executes(isolated_policy):
    r = policy.execute("kai.alerts.pending_approvals")
    assert r.ok
    assert isinstance(r.data, dict) and "count" in r.data
    audit = (isolated_policy / "tool_audit.jsonl").read_text().strip().splitlines()
    assert json.loads(audit[-1])["decision"] == "auto_execute"


def test_controlled_blocked_below_autonomy(isolated_policy):
    """Autonomy 1 (<3): CONTROLLED must NOT run — approval requested instead."""
    r = policy.execute("kai.docker.container_action",
                       {"container": "nope", "action": "restart"})
    assert not r.ok and not r.executed
    assert r.approval_id is None or isinstance(r.approval_id, str)
    rec = json.loads((isolated_policy / "tool_audit.jsonl").read_text().strip().splitlines()[-1])
    assert rec["decision"] == "blocked_pending_approval"
    # the container was never touched: no docker call happened because the
    # policy gate returned before invoking the tool function.


def test_controlled_runs_at_high_autonomy(isolated_policy, monkeypatch):
    (isolated_policy / "autonomy_level.json").write_text(json.dumps({"level": 4}))
    called = {}
    monkeypatch.setitem(REGISTRY._tools, "kai.docker.container_action", {
        "spec": ToolSpec(id="kai.docker.container_action", name="t", description="d",
                         risk=CONTROLLED),
        "fn": lambda container, action: {"fake": True},
    })
    r = policy.execute("kai.docker.container_action",
                       {"container": "x", "action": "restart"})
    assert r.ok and r.data == {"fake": True}


def test_high_risk_never_auto_executes(isolated_policy, monkeypatch):
    boom = []
    def dangerous():
        boom.append(1)
        return {"deleted": "everything"}
    monkeypatch.setitem(REGISTRY._tools, "kai.test.dangerous", {
        "spec": ToolSpec(id="kai.test.dangerous", name="d", description="d",
                         risk=HIGH_RISK),
        "fn": dangerous,
    })
    r = policy.execute("kai.test.dangerous", reason="test")
    assert not r.executed and boom == []          # fn NEVER ran
    assert "approval" in (r.error or "").lower()


def test_tool_failure_is_honest(isolated_policy):
    r = policy.execute("kai.server.inspect")     # real tool; may scan or error
    if not r.ok:
        assert r.error                            # failure carries the cause


def test_audit_records_risk_class(isolated_policy):
    policy.execute("kai.costs.summary", {"days": 1})
    rec = json.loads((isolated_policy / "tool_audit.jsonl").read_text().strip().splitlines()[-1])
    assert rec["risk"] == SAFE


def test_world_model_impact(isolated_policy, monkeypatch):
    from core import world_model
    monkeypatch.setattr(world_model, "WORLD_PATH", isolated_policy / "world.json")
    # inject a minimal snapshot instead of live collection
    world_model._save({
        "schema_version": 1, "updated_at": "t",
        "entities": {"ct:104": {"type": "lxc", "status": "running"},
                     "svc:npm": {"type": "service"}},
        "edges": [{"src": "svc:npm", "dst": "ct:104", "kind": "runs_on"}],
        "changes_since_previous": [],
    })
    imp = world_model.impact_of("ct:104")
    assert imp["impacted_count"] == 1
    assert imp["impacted"][0]["entity"] == "svc:npm"


def test_world_model_state_query(isolated_policy, monkeypatch):
    from core import world_model
    monkeypatch.setattr(world_model, "WORLD_PATH", isolated_policy / "world2.json")
    world_model._save({
        "schema_version": 1, "updated_at": "t",
        "entities": {"host:x": {"type": "proxmox_node", "status": "online"}},
        "edges": [], "counts": {"entities": 1, "by_type": {"proxmox_node": 1}},
    })
    st = world_model.get_state()
    assert st["entities"] == 1
    e = world_model.get_state("host:x")
    assert e["entity"]["status"] == "online"


def test_executive_memory_roundtrip(tmp_path, monkeypatch):
    from core import kai_executive as ke
    monkeypatch.setattr(ke, "DECISIONS_PATH", tmp_path / "d.json")
    monkeypatch.setattr(ke, "FAILURES_PATH", tmp_path / "f.json")
    ke.remember_decision("Use P40 for inference", reason="cost/perf", alternatives=["T4"])
    ke.remember_failure("restart x", cause="timeout", lesson="backoff first")  # unverified
    ds = ke.recent_decisions()
    assert ds[0]["decision"].startswith("Use P40")
    all_f = ke.recent_failures()
    ver_f = ke.recent_failures(verified_only=True)
    assert len(all_f) == 1 and len(ver_f) == 0  # speculative ≠ verified


def test_executive_correlates_duplicate_approvals(monkeypatch):
    from core import kai_executive as ke
    monkeypatch.setattr(ke, "_world_changes", lambda: [])
    monkeypatch.setattr(ke, "_disk_signals", lambda: [])
    monkeypatch.setattr(ke, "_pending_approvals", lambda: [
        {"id": str(i), "action": "restart_container",
         "reason": "Repeated critical incident: backup errors"} for i in range(5)])
    p = ke.prioritize()
    assert p["counts"]["attention"] == 1          # one root cause, not five
    assert p["counts"]["approvals_pending"] == 5
