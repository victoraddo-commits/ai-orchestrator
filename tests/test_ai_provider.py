"""Local-only provider registry tests (owner directive: zero third-party providers).

The registry must expose exactly the local model-fabric providers and never a
cloud/third-party entry. Generic register/list/validate behaviour is still
covered so the registry contract itself stays honest.
"""

import pytest

import core.ai_provider as ai_provider

LOCAL_PROVIDERS = {
    "kai_brain", "kai_coder", "kai_deep", "llama_coder_cpu", "local",
}


def test_only_local_providers_are_registered():
    providers = ai_provider.list_providers()
    assert set(providers) == LOCAL_PROVIDERS
    for name, info in providers.items():
        assert info["kind"] == "local", name


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
    assert ai_provider.get_provider("claude") is None


def test_local_provider_is_text_only():
    """`local` is a text-task provider; coding runs through the kai_* agents."""
    provider = ai_provider.get_provider("local")

    assert provider["run_text_task"] is not None
    assert provider["run_coding_task"] is None


def test_kai_brain_and_kai_coder_have_coding_capability():
    providers = ai_provider.list_providers()

    for name in ("kai_brain", "kai_coder", "llama_coder_cpu"):
        assert "coding_agent" in providers[name]["capabilities"], name
        assert "file_access" in providers[name]["capabilities"], name


def test_every_registered_provider_has_a_valid_cost_tier():
    providers = ai_provider.list_providers()

    assert providers  # sanity: the registry is not empty
    for name, info in providers.items():
        assert "cost_tier" in info, name
        assert info["cost_tier"] in ai_provider.COST_TIERS, name


def test_every_local_provider_is_free():
    for name, info in ai_provider.list_providers().items():
        assert info["cost_tier"] == "free", name


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


def test_register_provider_rejects_an_unknown_cost_tier():
    with pytest.raises(ValueError, match="cost_tier"):
        ai_provider.register_provider(
            "bad-tier-provider",
            run_text_task=lambda *a, **k: "",
            available_fn=lambda: False,
            cost_tier="expensive",
        )

    assert ai_provider.get_provider("bad-tier-provider") is None


def test_local_run_text_task_uses_the_vm104_served_model(monkeypatch):
    captured = {}

    def fake(prompt, timeout=240, project_path=None, model=ai_provider._KAI_MODEL):
        captured["model"] = model
        return "ok"

    monkeypatch.setattr(ai_provider, "_kai_ollama_run_text_task", fake)

    assert ai_provider._local_run_text_task("hi", timeout=5) == "ok"
    assert captured["model"] == "qwen3-coder:kai"


def test_kai_ollama_text_task_keeps_model_resident(monkeypatch):
    # The P40 brain must not unload after ollama's default 5-min idle: a cold
    # reload takes ~50s and would be recorded as provider latency, demoting
    # the GPU provider. Every fabric request must carry an explicit
    # keep_alive so the resident model stays loaded between sparse calls.
    import requests

    captured = {}

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"response": "ok"}

    def fake_post(url, json=None, timeout=None, **kwargs):
        captured["url"] = url
        captured["json"] = json
        return _Resp()

    monkeypatch.setattr(requests, "post", fake_post)

    out = ai_provider._kai_brain_run_text_task("hi", timeout=5)

    assert out == "ok"
    assert captured["url"].endswith("/api/generate")
    assert ai_provider._OLLAMA_KEEP_ALIVE
    assert captured["json"]["keep_alive"] == ai_provider._OLLAMA_KEEP_ALIVE
