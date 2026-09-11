import pytest

import core.ai_provider as ai_provider


# 2026-09-10 KAI MODEL TEAM directive: 100% local Ollama providers.
# All cloud/3rd-party providers (claude, gemini, groq, openai, openrouter,
# minimax, deepseek*, opencode*, qwen4*, gpuai_minimax, omniroute*) were
# removed from the registry. The tests below assert the local-only registry.
# (conftest's disable_slow_local_providers already stubs available_fn() for
# every local provider, so list_providers() makes no network calls here.)

def test_cloud_providers_are_not_registered():
    providers = ai_provider.list_providers()

    for name in (
        "claude", "gemini", "groq", "openai", "openrouter", "minimax",
        "deepseek", "deepseek_native_pro", "deepseek_native_flash",
        "geminix", "gpuai_minimax", "openrouter_claude", "omniroute",
        "qwen4_text", "qwen4_pod_b", "qwen4_coding",
    ):
        assert name not in providers


def test_local_provider_is_registered():
    providers = ai_provider.list_providers()

    assert "local" in providers
    assert providers["local"]["kind"] == "local"
    # Available depends on whether the ollama GPU server is reachable.
    assert providers["local"]["available"] in (True, False)


def test_local_providers_are_free_tier():
    providers = ai_provider.list_providers()

    for name in ("kai_brain", "kai_coder", "kai_deep", "local", "llama3",
                 "local_brain_fast", "local_coder"):
        assert name in providers
        assert providers[name]["cost_tier"] == "free", name


def test_kai_brain_and_kai_coder_have_both_capabilities():
    providers = ai_provider.list_providers()

    for name in ("kai_brain", "kai_coder"):
        assert set(providers[name]["capabilities"]) == {"coding_agent", "text_task", "file_access"}


def test_text_only_local_providers_have_text_task_capability_only():
    providers = ai_provider.list_providers()

    for name in ("kai_deep", "local", "llama3", "local_brain_fast", "local_coder"):
        assert "text_task" in providers[name]["capabilities"]
        assert "coding_agent" not in providers[name]["capabilities"]


def test_list_providers_does_not_expose_raw_callables():
    providers = ai_provider.list_providers()

    for info in providers.values():
        assert "run_coding_task" not in info
        assert "available_fn" not in info


def test_get_provider_returns_registered_callable():
    provider = ai_provider.get_provider("kai_brain")

    assert callable(provider["run_coding_task"])
    assert callable(provider["run_text_task"])


def test_get_provider_returns_none_for_unknown_name():
    assert ai_provider.get_provider("gpt5-turbo-max") is None


def test_local_provider_is_text_only():
    """Local provider (qwen2.5:7b ollama) is text-only — no coding agent."""
    provider = ai_provider.get_provider("local")

    assert provider["run_text_task"] is not None
    assert provider["run_coding_task"] is None


def test_register_provider_adds_a_new_entry():
    ai_provider.register_provider(
        "test-provider",
        run_coding_task=lambda *a, **k: {"success": True},
        available_fn=lambda: True,
        kind="local",
        description="a test provider",
    )

    providers = ai_provider.list_providers()

    assert providers["test-provider"]["available"] is True
    assert providers["test-provider"]["description"] == "a test provider"


def test_every_registered_provider_has_a_valid_cost_tier():
    providers = ai_provider.list_providers()

    assert providers  # sanity: the registry is not empty
    for name, info in providers.items():
        assert "cost_tier" in info, name
        assert info["cost_tier"] in ai_provider.COST_TIERS, name


def test_register_provider_rejects_an_unknown_cost_tier():
    with pytest.raises(ValueError, match="cost_tier"):
        ai_provider.register_provider(
            "bad-tier-provider",
            run_text_task=lambda *a, **k: "",
            available_fn=lambda: False,
            cost_tier="expensive",
        )

    assert ai_provider.get_provider("bad-tier-provider") is None
