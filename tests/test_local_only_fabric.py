"""Local-only model fabric invariant (owner directive: zero third-party providers).

These tests are the hard guard against a cloud provider being reintroduced:
every registered provider must be ``kind == "local"``, every routing chain
must resolve to a registered local provider, and the Kai-bot chat path
(``ai_router.chat`` -> ``planning``) must complete on a working local model.
"""
from __future__ import annotations

import core.ai_provider as ai_provider
import core.ai.ai_router as ai_router


def test_no_non_local_provider_is_registered():
    """Hard, enumerable invariant: nothing with kind != "local" may exist."""
    providers = ai_provider.list_providers()
    non_local = {
        name: info["kind"]
        for name, info in providers.items()
        if info.get("kind") != "local"
    }
    assert non_local == {}, (
        "third-party/cloud providers must not be registered: "
        f"{non_local}"
    )


def test_every_role_chain_resolves_to_registered_local_providers():
    """Every role in ROLE_PROVIDERS names only registered local providers."""
    providers = ai_provider.list_providers()
    for role, chain in ai_router.ROLE_PROVIDERS.items():
        assert chain, f"role {role!r} has an empty provider chain"
        for name in chain:
            assert name in providers, f"{role}: provider {name!r} is not registered"
            assert providers[name]["kind"] == "local", (
                f"{role}: provider {name!r} is not local "
                f"({providers[name]['kind']})"
            )


def test_local_provider_targets_the_served_local_model():
    """`local` must serve the model the fabric actually runs (qwen3-coder:kai),
    not the retired qwen2.5:7b that made every call raise HTTPError."""
    from core.model_registry import LOCAL_MODELS

    assert ai_provider._KAI_MODEL == "qwen3-coder:kai"
    assert LOCAL_MODELS["local"] == "qwen3-coder:kai"


def test_local_run_text_task_uses_the_vm104_served_model(monkeypatch):
    """`local` delegates to the VM104 ollama call for qwen3-coder:kai."""
    captured = {}

    def fake(prompt, timeout=240, project_path=None, model=ai_provider._KAI_MODEL):
        captured["model"] = model
        captured["prompt"] = prompt
        return "ok"

    monkeypatch.setattr(ai_provider, "_kai_ollama_run_text_task", fake)

    assert ai_provider._local_run_text_task("hi", timeout=5) == "ok"
    assert captured["model"] == "qwen3-coder:kai"
    assert captured["prompt"] == "hi"


def test_local_provider_availability_reflects_served_model(monkeypatch):
    """`local` is available only when ollama reports the model it targets."""
    monkeypatch.setattr(
        ai_provider, "_ollama_model_present",
        lambda model=ai_provider._KAI_MODEL, timeout=2: False,
    )
    assert ai_provider._local_available() is False

    monkeypatch.setattr(
        ai_provider, "_ollama_model_present",
        lambda model=ai_provider._KAI_MODEL, timeout=2: True,
    )
    assert ai_provider._local_available() is True


def test_kai_chat_resolves_to_a_working_local_provider(monkeypatch):
    """The Kai-bot chat path (chat -> planning) completes on a local model."""
    import core.ai.circuit_breaker as circuit_breaker
    import core.kai.conversation as conversation
    from core.memory import save

    monkeypatch.setattr(ai_router, "_gate_filter_candidates", None)
    circuit_breaker.reset_all_breakers()
    save("provider_quota.json", {})

    # Deterministic prompt (no RAG/knowledge dependency in this assertion).
    monkeypatch.setattr(
        conversation, "build_chat_prompt",
        lambda messages, signals: "What is 2+2?",
    )

    # Every planning-chain candidate but kai_brain is unavailable.
    for name in ("local", "llama_coder_cpu", "kai_coder", "kai_deep"):
        prov = ai_provider.get_provider(name)
        if prov is not None:
            monkeypatch.setitem(prov, "available_fn", lambda: False)

    brain = ai_provider.get_provider("kai_brain")
    assert brain is not None, "kai_brain must be registered"
    monkeypatch.setitem(brain, "available_fn", lambda: True)
    monkeypatch.setitem(
        brain, "run_text_task",
        lambda p, timeout=60, project_path=None: "LOCAL-OK",
    )

    result = ai_router.chat(
        [{"role": "user", "content": "What is 2+2?"}], {})

    assert result == "LOCAL-OK"
