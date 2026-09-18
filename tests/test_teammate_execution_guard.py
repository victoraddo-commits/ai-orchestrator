import json
import logging

import pytest


@pytest.fixture
def isolated_secrets(tmp_path, monkeypatch):
    from core.ai import secrets as secrets_mod
    monkeypatch.setattr(secrets_mod, "STORAGE_PATH", tmp_path / "provider_secrets.json")
    monkeypatch.setattr(secrets_mod, "AUDIT_PATH", tmp_path / "secret_access_audit.json")
    yield secrets_mod


def _make_teammate(**over):
    from core.teammate.registry import TeammateRegistry
    mates = TeammateRegistry()
    mate = mates.create({
        "name": "coder", "specialization": "coder",
        "capabilities": ["code", "write"],
        "skills": ["write_code"],
    })
    for k, v in over.items():
        setattr(mate, k, v)
    return mate, mates


def _make_skill_registry():
    from core.teammate.skills import SkillRegistry
    reg = SkillRegistry()
    reg.seed_default_15()
    return reg


def _make_fetch(value="super_secret_value_99"):
    """Stub vault fetch. Records (vault_path, env_var) invocations."""
    calls = []

    def fetch(vault_path, env_var):
        calls.append((vault_path, env_var))
        return value

    fetch.calls = calls
    return fetch


def _make_guard():
    from core.agentguard.guard import AgentGuard
    from core.teammate.execution_guard import ExecutionGuard
    return ExecutionGuard(agent_guard=AgentGuard({"enabled": True}),
                          skill_registry=_make_skill_registry())


def test_record_object_access_writes_only_metadata(isolated_secrets, tmp_path):
    from core.ai.secrets import record_object_access, get_object_access_log

    record_object_access("t1", "write_code", "vault.fetch", True,
                         detail="purpose: build step")
    recs = get_object_access_log()
    assert len(recs) == 1
    entry = recs[0]
    assert entry["object_id"] == "t1"
    assert entry["scope"] == "write_code"
    assert "secret_value" not in json.dumps(entry)
    assert "super_secret" not in json.dumps(entry)


def test_records_order_newest_first(isolated_secrets):
    from core.ai.secrets import record_object_access, get_object_access_log

    record_object_access("t1", "a", "fetch", True)
    record_object_access("t1", "b", "fetch", True)
    recs = get_object_access_log()
    assert recs[0]["scope"] == "b"


def test_allowed_low_risk_executes(isolated_secrets, tmp_path, monkeypatch):
    from core.agentguard.guard import AgentGuard, ActionType
    from core.teammate.execution_guard import ExecutionGuard, GuardDenied
    from core.ai.secrets import get_object_access_log

    eg = _make_guard()
    mate, mates = _make_teammate()

    ran = []
    def fn(ctx):
        ran.append(1)
        return "done"

    out = eg.execute(mate, "write_code", ActionType.READ, resource="/tmp/x",
                     details="read temp", fn=fn, mate_registry=mates)
    assert out == "done"
    assert ran == [1]


def test_capability_denied_uses_skill_requirements(isolated_secrets, tmp_path, monkeypatch):
    from core.agentguard.guard import AgentGuard, ActionType
    from core.teammate.execution_guard import ExecutionGuard, GuardDenied
    from core.ai.secrets import get_object_access_log

    eg = _make_guard()
    mate, mates = _make_teammate(capabilities=["code"],
                                 skills=["run_security_scan"])

    ran = []
    def fn(ctx):
        ran.append(1)
        return 1

    with pytest.raises(GuardDenied) as exc:
        eg.execute(mate, "run_security_scan", ActionType.WRITE, resource="/tmp/scan",
                   details="scan", fn=fn, mate_registry=mates)
    assert exc.value.decision == "deny"
    assert "capabilit" in exc.value.message
    assert ran == []
    assert mate.security_history[-1]["decision"] == "deny"
    obj = [r for r in get_object_access_log() if "object_id" in r]
    assert obj and obj[-1]["success"] is False


def test_secret_value_never_in_logs_or_audit(isolated_secrets, tmp_path, monkeypatch, caplog):
    from core.agentguard.guard import AgentGuard, ActionType
    from core.teammate.execution_guard import ExecutionGuard
    from core.ai.secrets import get_object_access_log

    reg = _make_skill_registry()
    wc = reg.get("write_code")
    wc.required_permissions["secrets"] = ["secrets/test/provider"]
    reg.upsert(wc)
    eg = ExecutionGuard(agent_guard=AgentGuard({"enabled": True}),
                        skill_registry=reg)
    mate, mates = _make_teammate()

    def fn(ctx):
        assert ctx.secrets["OPENAI_API_KEY"] == "super_secret_value_99"
        return "ok"

    with caplog.at_level(logging.DEBUG):
        eg.execute(
            mate, "write_code", ActionType.READ, resource="/tmp/secrets",
            details="capability-scoped read", fn=fn,
            secret_grants=[("secrets/test/provider", "OPENAI_API_KEY")],
            mate_registry=mates,
            fetch_secret=_make_fetch("super_secret_value_99"),
        )

    joined = "\n".join(r.getMessage() for r in caplog.records)
    obj_rows = [r for r in get_object_access_log() if "object_id" in r]
    assert obj_rows, "expected object-access audit rows"
    for entry in obj_rows:
        assert set(entry) == {"object_id", "scope", "action", "success",
                              "detail", "timestamp"}
    assert "super_secret_value_99" not in joined
    assert "super_secret_value_99" not in json.dumps(get_object_access_log())
    assert "super_secret_value_99" not in json.dumps(mate.security_history)


def test_denied_action_stamps_security_history(isolated_secrets, tmp_path, monkeypatch):
    from core.agentguard.guard import AgentGuard, ActionType
    from core.teammate.execution_guard import ExecutionGuard, GuardDenied
    from core.agentguard.guard import ActionType as AT

    eg = _make_guard()
    mate, mates = _make_teammate()

    with pytest.raises(GuardDenied):
        eg.execute(mate, "write_code", AT.DESTRUCTIVE, resource="/etc/hosts",
                   details="rm -rf /etc", fn=lambda ctx: 1, mate_registry=mates)

    assert mate.security_history
    assert mate.security_history[-1]["decision"] in ("deny", "require_approval")


def test_skill_requiring_approval_raises_guard_denied(isolated_secrets, tmp_path, monkeypatch):
    from core.agentguard.guard import AgentGuard, ActionType
    from core.teammate.execution_guard import ExecutionGuard, GuardDenied
    from core.ai.secrets import get_object_access_log

    reg = _make_skill_registry()
    eg = ExecutionGuard(agent_guard=AgentGuard({"enabled": True}),
                        skill_registry=reg)
    mate, mates = _make_teammate(capabilities=["deploy", "write"], skills=[])
    assert mates.add_skill(mate.id, "deploy_service", reg) is True

    with pytest.raises(GuardDenied) as exc:
        eg.execute(mate, "deploy_service", ActionType.WRITE,
                   resource="/etc/systemd/system/app.service",
                   details="roll out build", fn=lambda ctx: 1, mate_registry=mates)
    assert exc.value.decision == "require_approval"
    obj_rows = [r for r in get_object_access_log() if "object_id" in r]
    assert obj_rows and obj_rows[-1]["action"] == "guarded_execution"
    assert obj_rows[-1]["success"] is False


def test_omitted_skill_id_raises_type_error(isolated_secrets, tmp_path, monkeypatch):
    from core.agentguard.guard import AgentGuard, ActionType
    from core.teammate.execution_guard import ExecutionGuard

    eg = _make_guard()
    mate, mates = _make_teammate()

    with pytest.raises(TypeError):
        eg.execute(mate, action_type=ActionType.READ, resource="/tmp/x",
                   details="read", fn=lambda ctx: 1, mate_registry=mates)


def test_unresolved_skill_record_fails_closed(isolated_secrets, tmp_path, monkeypatch):
    from core.agentguard.guard import AgentGuard, ActionType
    from core.teammate.execution_guard import ExecutionGuard, GuardDenied
    from core.ai.secrets import get_object_access_log

    eg = _make_guard()
    mate, mates = _make_teammate(skills=["nope"])

    with pytest.raises(GuardDenied) as exc:
        eg.execute(mate, "nope", ActionType.READ, resource="/tmp/x",
                   details="read", fn=lambda ctx: 1, mate_registry=mates)
    assert exc.value.decision == "deny"
    assert "skill record unresolved" in exc.value.message
    assert mate.security_history
    assert mate.security_history[-1]["decision"] == "deny"
    obj = [r for r in get_object_access_log() if "object_id" in r]
    assert obj and obj[-1]["success"] is False


def test_scope_uses_executing_skill_only(isolated_secrets, tmp_path, monkeypatch):
    from core.agentguard.guard import AgentGuard, ActionType
    from core.teammate.skills import SkillRecord
    from core.teammate.execution_guard import ExecutionGuard

    reg = _make_skill_registry()
    reg.register(SkillRecord(
        skill_id="skill_a_clean", name="Skill A", description="clean skill",
        required_capabilities=["code", "write"],
        required_permissions={"secrets": []},
    ))
    reg.register(SkillRecord(
        skill_id="skill_b_sec", name="Skill B", description="secret skill",
        required_capabilities=["code", "write"],
        required_permissions={"secrets": ["secrets/infrastructure/x"]},
    ))
    eg = ExecutionGuard(agent_guard=AgentGuard({"enabled": True}),
                        skill_registry=reg)
    mate, mates = _make_teammate(capabilities=["code", "write"],
                                 skills=["skill_a_clean", "skill_b_sec"])

    fetched = []
    def fetch(vault_path, env_var):
        fetched.append((vault_path, env_var))
        return "INFRA_X_VALUE"

    ran = []
    def fn_a(ctx):
        ran.append(1)
        assert ctx.secrets == {}
        return "a"

    out = eg.execute(mate, "skill_a_clean", ActionType.READ, resource="/tmp/a",
                     details="read a", fn=fn_a,
                     secret_grants=[("secrets/infrastructure/x", "INFRA_SECRET")],
                     mate_registry=mates, fetch_secret=fetch)
    assert out == "a"
    assert ran == [1]
    assert fetched == []

    injected = {}
    def fn_b(ctx):
        injected.update(ctx.secrets)
        return "b"

    eg.execute(mate, "skill_b_sec", ActionType.READ, resource="/tmp/b",
               details="read b", fn=fn_b,
               secret_grants=[("secrets/infrastructure/x", "INFRA_SECRET")],
               mate_registry=mates, fetch_secret=fetch)
    assert injected == {"INFRA_SECRET": "INFRA_X_VALUE"}
    assert ("secrets/infrastructure/x", "INFRA_SECRET") in fetched


def test_out_of_scope_and_traversal_paths_never_fetched(isolated_secrets, tmp_path, monkeypatch):
    from core.agentguard.guard import AgentGuard, ActionType
    from core.teammate.execution_guard import ExecutionGuard

    reg = _make_skill_registry()
    wc = reg.get("write_code")
    wc.required_permissions["secrets"] = ["secrets/test/provider"]
    reg.upsert(wc)
    eg = ExecutionGuard(agent_guard=AgentGuard({"enabled": True}),
                        skill_registry=reg)
    mate, mates = _make_teammate()

    fetched = []
    def fetch(vault_path, env_var):
        fetched.append((vault_path, env_var))
        return "V"

    seen = {}
    def fn(ctx):
        seen.update(ctx.secrets)
        return "ok"

    eg.execute(
        mate, "write_code", ActionType.READ, resource="/tmp/s",
        details="read", fn=fn,
        secret_grants=[
            ("secrets/test/provider", "OK"),
            ("../secrets/test/provider", "TRAV"),
            ("/secrets/test/provider", "ABS"),
            ("secrets/other/x", "OTHER"),
        ],
        mate_registry=mates, fetch_secret=fetch)
    assert seen == {"OK": "V"}
    assert fetched == [("secrets/test/provider", "OK")]


def test_exception_path_clears_context_secrets(isolated_secrets, tmp_path, monkeypatch):
    from core.agentguard.guard import AgentGuard, ActionType
    from core.teammate.execution_guard import ExecutionGuard
    from core.ai.secrets import get_object_access_log

    reg = _make_skill_registry()
    wc = reg.get("write_code")
    wc.required_permissions["secrets"] = ["secrets/test/provider"]
    reg.upsert(wc)
    eg = ExecutionGuard(agent_guard=AgentGuard({"enabled": True}),
                        skill_registry=reg)
    mate, mates = _make_teammate()

    holder = {}
    def fn(ctx):
        holder["ctx"] = ctx
        assert ctx.secrets == {"OPENAI_API_KEY": "super_secret_value_99"}
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        eg.execute(mate, "write_code", ActionType.READ, resource="/tmp/s",
                   details="read", fn=fn,
                   secret_grants=[("secrets/test/provider", "OPENAI_API_KEY")],
                   mate_registry=mates, fetch_secret=_make_fetch("super_secret_value_99"))
    assert holder["ctx"].secrets == {}
    obj = [r for r in get_object_access_log() if "object_id" in r]
    assert any(r["action"] == "execution" and r["success"] is False for r in obj)


def test_repr_and_public_view_never_show_values(isolated_secrets, tmp_path, monkeypatch):
    from core.agentguard.guard import AgentGuard, ActionType
    from core.teammate.execution_guard import ExecutionGuard

    reg = _make_skill_registry()
    wc = reg.get("write_code")
    wc.required_permissions["secrets"] = ["secrets/test/provider"]
    reg.upsert(wc)
    eg = ExecutionGuard(agent_guard=AgentGuard({"enabled": True}),
                        skill_registry=reg)
    mate, mates = _make_teammate()

    holder = {}
    def fn(ctx):
        holder["ctx"] = ctx
        assert ctx.secrets == {"OPENAI_API_KEY": "super_secret_value_99"}
        assert "super_secret_value_99" not in repr(ctx)
        assert "super_secret_value_99" not in json.dumps(ctx.as_public())
        return "ok"

    eg.execute(mate, "write_code", ActionType.READ, resource="/tmp/s",
               details="read", fn=fn,
               secret_grants=[("secrets/test/provider", "OPENAI_API_KEY")],
               mate_registry=mates, fetch_secret=_make_fetch("super_secret_value_99"))
    assert holder["ctx"].secrets == {}
    assert "super_secret_value_99" not in repr(holder["ctx"])


def test_scoped_fetch_audits_only_fetched_in_scope(isolated_secrets, tmp_path, monkeypatch):
    from core.agentguard.guard import AgentGuard, ActionType
    from core.teammate.execution_guard import ExecutionGuard
    from core.ai.secrets import get_object_access_log

    reg = _make_skill_registry()
    wc = reg.get("write_code")
    wc.required_permissions["secrets"] = ["secrets/test/provider"]
    reg.upsert(wc)
    eg = ExecutionGuard(agent_guard=AgentGuard({"enabled": True}),
                        skill_registry=reg)
    mate, mates = _make_teammate()

    eg.execute(
        mate, "write_code", ActionType.READ, resource="/tmp/s",
        details="read", fn=lambda ctx: "ok",
        secret_grants=[
            ("secrets/test/provider", "OK"),
            ("../secrets/test/provider", "TRAV"),
        ],
        mate_registry=mates, fetch_secret=_make_fetch("V"))

    obj = [r for r in get_object_access_log() if "object_id" in r]
    fetched_rows = [r for r in obj if r["action"] == "vault.fetch"]
    assert len(fetched_rows) == 1
    assert fetched_rows[0]["scope"] == "write_code"
    assert "secrets/test/provider" in fetched_rows[0]["detail"]
    assert fetched_rows[0]["success"] is True
    allowed = [r for r in obj if r["action"] == "guarded_execution"]
    assert allowed and allowed[0]["success"] is True


def test_disabled_guard_rejected_at_construction(isolated_secrets, tmp_path, monkeypatch):
    from core.agentguard.guard import AgentGuard
    from core.teammate.execution_guard import ExecutionGuard

    with pytest.raises(ValueError):
        ExecutionGuard(agent_guard=AgentGuard({"enabled": False}))


def test_malformed_security_history_raises_but_allow_audit_written(isolated_secrets,
                                                                   tmp_path, monkeypatch):
    from core.agentguard.guard import AgentGuard, ActionType
    from core.teammate.execution_guard import ExecutionGuard
    from core.ai.secrets import get_object_access_log

    eg = _make_guard()
    mate, mates = _make_teammate()
    mate.security_history = "corrupted-non-list"

    with pytest.raises(TypeError):
        eg.execute(mate, "write_code", ActionType.READ, resource="/tmp/x",
                   details="read", fn=lambda ctx: "ok", mate_registry=mates)

    obj = [r for r in get_object_access_log() if "object_id" in r]
    assert any(r["action"] == "guarded_execution" and r["success"] is True
               for r in obj)


def test_deny_audit_survives_corrupted_security_history(isolated_secrets,
                                                        tmp_path, monkeypatch):
    from core.agentguard.guard import ActionType
    from core.teammate.execution_guard import ExecutionGuard
    from core.ai.secrets import get_object_access_log

    eg = _make_guard()

    mate, mates = _make_teammate(skills=["nope"])
    mate.security_history = "corrupted-non-list"
    with pytest.raises(TypeError):
        eg.execute(mate, "nope", ActionType.READ, resource="/tmp/x",
                   details="read", fn=lambda ctx: 1, mate_registry=mates)
    obj = [r for r in get_object_access_log() if "object_id" in r]
    deny = [r for r in obj if r["action"] == "guarded_execution"
            and r["success"] is False]
    assert deny, "unresolved-skill DENY audit row must survive a corrupt stamp"
    assert "unresolved skill" in deny[-1]["detail"]

    mate2, mates2 = _make_teammate()
    mate2.security_history = "corrupted-non-list"
    with pytest.raises(TypeError):
        eg.execute(mate2, "write_code", ActionType.DESTRUCTIVE,
                   resource="/etc/hosts", details="rm -rf /etc",
                   fn=lambda ctx: 1, mate_registry=mates2)
    obj2 = [r for r in get_object_access_log() if "object_id" in r]
    deny2 = [r for r in obj2 if r["action"] == "guarded_execution"
             and r["success"] is False]
    assert deny2, "agentguard DENY audit row must survive a corrupt stamp"


def test_fetch_failure_is_audited_and_fn_not_called(isolated_secrets,
                                                    tmp_path, monkeypatch):
    from core.agentguard.guard import AgentGuard, ActionType
    from core.teammate.execution_guard import ExecutionGuard
    from core.ai.secrets import get_object_access_log

    reg = _make_skill_registry()
    wc = reg.get("write_code")
    wc.required_permissions["secrets"] = ["secrets/test/provider"]
    reg.upsert(wc)
    eg = ExecutionGuard(agent_guard=AgentGuard({"enabled": True}),
                        skill_registry=reg)
    mate, mates = _make_teammate()

    def fetch(vault_path, env_var):
        raise RuntimeError("vault unreachable: super_secret_value_99")

    ran = []
    with pytest.raises(RuntimeError):
        eg.execute(mate, "write_code", ActionType.READ, resource="/tmp/s",
                   details="read", fn=lambda ctx: ran.append(1),
                   secret_grants=[("secrets/test/provider", "OPENAI_API_KEY")],
                   mate_registry=mates, fetch_secret=fetch)
    assert ran == [], "fn must never run when a fetch fails"

    obj = [r for r in get_object_access_log() if "object_id" in r]
    failed = [r for r in obj if r["action"] == "vault.fetch"
              and r["success"] is False]
    assert failed, "fetch-failure row must be written"
    assert failed[-1]["detail"] == "fetch failed vault_path=secrets/test/provider"
    assert "unreachable" not in failed[-1]["detail"]
    assert "super_secret_value_99" not in failed[-1]["detail"]


def test_stale_triple_grant_raises_clear_type_error(isolated_secrets,
                                                    tmp_path, monkeypatch):
    from core.agentguard.guard import AgentGuard, ActionType
    from core.teammate.execution_guard import ExecutionGuard

    reg = _make_skill_registry()
    wc = reg.get("write_code")
    wc.required_permissions["secrets"] = ["secrets/test/provider"]
    reg.upsert(wc)
    eg = ExecutionGuard(agent_guard=AgentGuard({"enabled": True}),
                        skill_registry=reg)
    mate, mates = _make_teammate()

    with pytest.raises(TypeError, match=r"must be \(vault_path, env_var\) pairs"):
        eg.execute(mate, "write_code", ActionType.READ, resource="/tmp/s",
                   details="read", fn=lambda ctx: 1,
                   secret_grants=[("secrets/test/provider", "OPENAI_API_KEY",
                                   "super_secret_value_99")],
                   mate_registry=mates, fetch_secret=_make_fetch())


def test_stamps_persist_to_registry_after_deny_and_allow(isolated_secrets,
                                                         tmp_path, monkeypatch):
    from core.agentguard.guard import AgentGuard, ActionType
    from core.teammate.execution_guard import ExecutionGuard, GuardDenied
    from core.teammate.registry import TeammateRegistry

    reg = _make_skill_registry()
    eg = ExecutionGuard(agent_guard=AgentGuard({"enabled": True}),
                        skill_registry=reg)

    mates = TeammateRegistry()
    mate = mates.create({"name": "operator", "specialization": "operator",
                         "capabilities": ["deploy", "write"], "skills": []})
    assert mates.add_skill(mate.id, "deploy_service", reg) is True
    with pytest.raises(GuardDenied):
        eg.execute(mate, "deploy_service", ActionType.WRITE,
                   resource="/etc/systemd/system/app.service",
                   details="roll out build", fn=lambda ctx: 1,
                   mate_registry=mates)
    reloaded = TeammateRegistry()
    rm = reloaded.get(mate.id)
    assert rm is not None
    assert rm.security_history, "deny stamp must persist across registry reload"
    assert rm.security_history[-1]["decision"] == "require_approval"

    mates2 = TeammateRegistry()
    mate2 = mates2.create({"name": "coder2", "specialization": "coder",
                           "capabilities": ["code", "write"], "skills": []})
    assert mates2.add_skill(mate2.id, "write_code", reg) is True
    eg.execute(mate2, "write_code", ActionType.READ, resource="/tmp/x",
               details="read temp", fn=lambda ctx: "ok", mate_registry=mates2)
    reloaded2 = TeammateRegistry()
    rm2 = reloaded2.get(mate2.id)
    assert rm2 is not None
    assert rm2.security_history, "allow stamp must persist across registry reload"
    assert rm2.security_history[-1]["decision"] == "allow"


def test_ctx_cleared_when_second_grant_fetch_fails(isolated_secrets,
                                                   tmp_path, monkeypatch):
    import core.teammate.execution_guard as eg_mod
    from core.agentguard.guard import AgentGuard, ActionType
    from core.teammate.execution_guard import ExecutionGuard
    from core.ai.secrets import get_object_access_log

    reg = _make_skill_registry()
    wc = reg.get("write_code")
    wc.required_permissions["secrets"] = ["secrets/a/provider",
                                          "secrets/b/provider"]
    reg.upsert(wc)
    eg = ExecutionGuard(agent_guard=AgentGuard({"enabled": True}),
                        skill_registry=reg)
    mate, mates = _make_teammate()

    captured = []
    real_ctx = eg_mod.RedactionContext

    class RecordingCtx(real_ctx):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            captured.append(self)

    monkeypatch.setattr(eg_mod, "RedactionContext", RecordingCtx)

    def fetch(vault_path, env_var):
        if vault_path == "secrets/b/provider":
            raise RuntimeError("vault down on second grant")
        return "V1"

    ran = []
    with pytest.raises(RuntimeError):
        eg.execute(mate, "write_code", ActionType.READ, resource="/tmp/s",
                   details="read", fn=lambda ctx: ran.append(1),
                   secret_grants=[("secrets/a/provider", "A"),
                                  ("secrets/b/provider", "B")],
                   mate_registry=mates, fetch_secret=fetch)
    assert ran == [], "fn must never run"
    assert captured, "RedactionContext must have been built"
    assert captured[0].secrets == {}, \
        "grant-1 secret must be cleared when a later grant's fetch raises"

    obj = [r for r in get_object_access_log() if "object_id" in r]
    failed = [r for r in obj if r["action"] == "vault.fetch"
              and r["success"] is False]
    assert failed and "secrets/b/provider" in failed[-1]["detail"]


def test_for_teammate_is_reporting_only_never_injection(isolated_secrets,
                                                        tmp_path, monkeypatch):
    from core.agentguard.guard import AgentGuard, ActionType
    from core.teammate.skills import SkillRecord
    from core.teammate.execution_guard import ExecutionGuard

    reg = _make_skill_registry()
    reg.register(SkillRecord(
        skill_id="skill_a_clean", name="Skill A", description="clean skill",
        required_capabilities=["code", "write"],
        required_permissions={"secrets": []},
    ))
    reg.register(SkillRecord(
        skill_id="skill_b_sec", name="Skill B", description="secret skill",
        required_capabilities=["code", "write"],
        required_permissions={"secrets": ["secrets/infrastructure/x"]},
    ))
    eg = ExecutionGuard(agent_guard=AgentGuard({"enabled": True}),
                        skill_registry=reg)
    mate, mates = _make_teammate(capabilities=["code", "write"],
                                 skills=["skill_a_clean", "skill_b_sec"])

    assert eg.for_teammate(mate, reg) == ["secrets/infrastructure/x"], \
        "for_teammate REPORTS the union across a teammate's skills"

    fetched = []
    view = {}
    out = eg.execute(mate, "skill_a_clean", ActionType.READ,
                     resource="/tmp/a", details="read a",
                     fn=lambda ctx: (view.update(ctx.secrets), "ok")[1],
                     secret_grants=[("secrets/infrastructure/x", "X")],
                     mate_registry=mates,
                     fetch_secret=lambda p, e: (fetched.append((p, e)), "XVAL")[1])
    assert out == "ok"
    assert view == {}, "execute() must not inject the reported union scope"
    assert fetched == [], "executing skill_a (empty scope) must fetch nothing"


def test_fetch_miss_runs_fn_without_secret(isolated_secrets,
                                           tmp_path, monkeypatch):
    from core.agentguard.guard import AgentGuard, ActionType
    from core.teammate.execution_guard import ExecutionGuard
    from core.ai.secrets import get_object_access_log

    reg = _make_skill_registry()
    wc = reg.get("write_code")
    wc.required_permissions["secrets"] = ["secrets/test/provider"]
    reg.upsert(wc)
    eg = ExecutionGuard(agent_guard=AgentGuard({"enabled": True}),
                        skill_registry=reg)
    mate, mates = _make_teammate()

    seen = {}
    out = eg.execute(mate, "write_code", ActionType.READ, resource="/tmp/s",
                     details="read", fn=lambda ctx: (seen.update(ctx.secrets),
                                                       "ok")[1],
                     secret_grants=[("secrets/test/provider", "OPENAI_API_KEY")],
                     mate_registry=mates, fetch_secret=lambda p, e: None)
    assert out == "ok", "vault miss (None) must still run fn"
    assert seen == {}, "no secret injected on a miss"

    obj = [r for r in get_object_access_log() if "object_id" in r]
    miss = [r for r in obj if r["action"] == "vault.fetch"
            and r["success"] is False]
    assert miss and "secret not found" in miss[-1]["detail"]
