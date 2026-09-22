"""§7 Model Fabric — local diversity + failover (2026-09-18).

The Model Fabric must not collapse every role onto the single VM104 GPU
endpoint (ollama ``localhost:11434``). Each routing chain must span more than
one *local node* so the loss of the primary model fails over to the next local
model — never to a cloud provider.

Nodes:
  * VM104 GPU (Tesla P40)  -> ollama ``localhost:11434``   (kai_brain/kai_coder/…)
  * VM112 CPU              -> llama.cpp ``192.168.1.242:5001`` (llama_coder_cpu)
"""
from __future__ import annotations

import pytest

import core.ai.ai_router as ai_router
import core.ai_provider as ai_provider
from core.ai.ai_router import AllProvidersFailed

# Providers physically served by the VM104 GPU (ollama localhost:11434).
VM104 = {"kai_brain", "kai_coder", "kai_deep", "local", "llama3"}
# Providers physically served by the VM112 CPU llama.cpp server.
VM112 = {"llama_coder_cpu", "koboldcpp_cpu", "koboldcpp_cpu_a", "koboldcpp_cpu_b"}

# Roles that carry real task traffic and therefore must survive a VM104 loss.
CORE_ROLES = [
    "planning", "architecture", "review", "coding", "classification",
    "documentation", "log_analysis", "deep", "security", "legal_coding",
]


@pytest.fixture(autouse=True)
def _isolation(monkeypatch):
    """Deterministic routing: no workforce gate, clean breakers, no Telegram."""
    import core.ai.circuit_breaker as circuit_breaker
    import core.telegram_bridge as telegram_bridge
    from core.memory import save

    monkeypatch.setattr(ai_router, "_gate_filter_candidates", None)
    circuit_breaker.reset_all_breakers()
    save("provider_quota.json", {})
    monkeypatch.setattr(
        telegram_bridge, "send_message",
        lambda text, token=None, chat_id=None, reply_markup=None: None)
    yield
    circuit_breaker.reset_all_breakers()


def _make_unavailable(monkeypatch, names):
    import core.ai_provider as ap
    for name in names:
        prov = ap.get_provider(name)
        if prov is not None:
            monkeypatch.setitem(prov, "available_fn", lambda: False)


def _make_available(monkeypatch, name, response):
    import core.ai_provider as ap
    prov = ap.get_provider(name)
    assert prov is not None, f"provider {name} is not registered"
    monkeypatch.setitem(prov, "available_fn", lambda: True)
    monkeypatch.setitem(
        prov, "run_text_task",
        lambda p, timeout=60, project_path=None, r=response: r)


# ── chain topology ──────────────────────────────────────────────────────────
def test_every_chain_is_local_only():
    """No cloud provider may appear in ANY routing chain (100% local)."""
    providers = ai_provider.list_providers()
    for role, chain in ai_router.ROLE_PROVIDERS.items():
        for name in chain:
            assert name in providers, f"{role}: unknown provider {name!r}"
            assert providers[name]["kind"] == "local", \
                f"{role}: non-local provider {name!r} ({providers[name]['kind']})"


@pytest.mark.parametrize("role", CORE_ROLES)
def test_core_roles_have_local_node_failover(role):
    """Every core role spans VM104 AND VM112 (no single-node SPOF)."""
    chain = ai_router.ROLE_PROVIDERS[role]
    assert chain, f"{role}: empty chain"
    assert any(p in VM104 for p in chain), f"{role}: no VM104 primary: {chain}"
    assert any(p in VM112 for p in chain), f"{role}: no VM112 failover: {chain}"
    assert len(chain) >= 2, f"{role}: chain of one cannot fail over: {chain}"


def test_provider_chain_report_surfaces_health_and_nodes():
    report = ai_router.provider_chain_report()
    assert set(report) >= {"chains", "default_chains", "provider_state", "diversity"}
    state = report["provider_state"]
    assert state["kai_brain"]["kind"] == "local"
    assert state["llama_coder_cpu"]["node"] == "vm112-cpu"
    assert state["kai_brain"]["node"] == "vm104-gpu"
    # diversity flags that the planning chain can survive a node loss
    assert report["diversity"]["planning"]["nodes"] >= 2
    assert report["diversity"]["planning"]["has_failover"] is True


# ── live failover within the fabric ─────────────────────────────────────────
def test_planning_fails_over_from_vm104_to_vm112(monkeypatch):
    """VM104 unreachable → planning completes on the VM112 local model."""
    _make_unavailable(monkeypatch, VM104)
    _make_available(monkeypatch, "llama_coder_cpu", "cpu plan result")

    result = ai_router.delegate("Design an application architecture")

    assert result["provider"] == "llama_coder_cpu"
    assert result["response"] == "cpu plan result"


def test_coding_fails_over_on_vm104_timeout(monkeypatch):
    """A VM104 timeout (not just unavailability) fails over to VM112."""
    import core.ai_provider as ap

    coder = ap.get_provider("kai_coder")
    monkeypatch.setitem(coder, "available_fn", lambda: True)

    def _timeout(p, timeout=60, project_path=None):
        raise TimeoutError("kai_coder request failed: ReadTimeout")

    monkeypatch.setitem(coder, "run_text_task", _timeout)
    _make_available(monkeypatch, "llama_coder_cpu", "cpu code result")

    result = ai_router.delegate("Implement a python function add(a, b)",
                                task_type="coding")

    assert result["provider"] == "llama_coder_cpu"
    assert result["response"] == "cpu code result"


def test_kai_brain_failure_falls_over_across_nodes(monkeypatch):
    """R1: a registered kai_brain (VM104) failure is recorded and the route
    falls through to llama_coder_cpu (VM112) — two distinct physical nodes,
    so losing the GPU does not take the chain with it."""
    from core.model_registry import node_for

    assert node_for("kai_brain") == "vm104-gpu"
    assert node_for("llama_coder_cpu") == "vm112-cpu"

    brain = ai_provider.get_provider("kai_brain")
    assert brain is not None, "kai_brain must be registered"
    monkeypatch.setitem(brain, "available_fn", lambda: True)

    def _boom(p, timeout=60, project_path=None):
        raise RuntimeError("VM104 ollama request timed out")

    monkeypatch.setitem(brain, "run_text_task", _boom)
    _make_available(monkeypatch, "llama_coder_cpu", "vm112 fallback")

    result = ai_router.delegate("Design an application architecture",
                                return_attempts=True)

    assert result["provider"] == "llama_coder_cpu"
    assert result["response"] == "vm112 fallback"
    attempted = [a for a in result["attempts"] if a["provider"] == "kai_brain"]
    assert attempted, "kai_brain's failure must be recorded in the attempt log"
    assert attempted[0]["error_type"] == "timeout"


def test_all_local_models_down_raises_all_providers_failed(monkeypatch):
    """With both local nodes down the fabric fails closed — it never goes cloud."""
    _make_unavailable(monkeypatch, VM104 | VM112)
    with pytest.raises(AllProvidersFailed):
        ai_router.delegate("Design an application architecture")


# ── teammate model plan inherits the multi-local chain ──────────────────────
def test_teammate_plan_widens_single_role_to_local_failover():
    from core.teammate.skills import SkillRegistry
    from core.teammate import model_fabric

    skills = SkillRegistry()
    skills.seed_default_15()
    plan = model_fabric.resolve_model_plan("t1", "inspect_repository", skills)

    assert plan.task_type == "planning"
    assert plan.provider_chain[0] == "kai_brain"
    assert any(p in VM112 for p in plan.provider_chain), plan.provider_chain
    assert len(plan.provider_chain) >= 2


def test_teammate_write_code_plan_starts_on_kai_coder_with_cpu_failover():
    from core.teammate.skills import SkillRegistry
    from core.teammate import model_fabric

    skills = SkillRegistry()
    skills.seed_default_15()
    plan = model_fabric.resolve_model_plan("t1", "write_code", skills)

    assert plan.task_type == "coding"
    assert plan.provider_chain[0] == "kai_coder"
    assert "llama_coder_cpu" in plan.provider_chain
