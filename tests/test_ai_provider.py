import pytest

import core.ai_provider as ai_provider


def test_claude_provider_is_registered_by_default():
    providers = ai_provider.list_providers()

    assert "claude" in providers
    assert providers["claude"]["kind"] == "cloud"


def test_local_provider_is_registered_as_a_placeholder():
    providers = ai_provider.list_providers()

    assert "local" in providers
    assert providers["local"]["kind"] == "local"
    assert providers["local"]["available"] is False


def test_list_providers_does_not_expose_raw_callables():
    providers = ai_provider.list_providers()

    for info in providers.values():
        assert "run_coding_task" not in info
        assert "available_fn" not in info


def test_get_provider_returns_registered_callable():
    provider = ai_provider.get_provider("claude")

    assert callable(provider["run_coding_task"])


def test_get_provider_returns_none_for_unknown_name():
    assert ai_provider.get_provider("gpt5-turbo-max") is None


def test_local_provider_run_coding_task_raises_not_implemented():
    provider = ai_provider.get_provider("local")

    with pytest.raises(NotImplementedError):
        provider["run_coding_task"]("/proj", "do something")


def test_register_provider_adds_a_new_entry():
    ai_provider.register_provider(
        "test-provider",
        run_coding_task=lambda *a, **k: {"success": True},
        available_fn=lambda: True,
        kind="cloud",
        description="a test provider",
    )

    providers = ai_provider.list_providers()

    assert providers["test-provider"]["available"] is True
    assert providers["test-provider"]["description"] == "a test provider"


def test_gemini_groq_openai_providers_are_registered_with_text_task_capability():
    providers = ai_provider.list_providers()

    for name in ("gemini", "groq", "qwen4_text", "openrouter", "minimax", "deepseek"):
        assert name in providers
        assert "text_task" in providers[name]["capabilities"]
        assert "coding_agent" not in providers[name]["capabilities"]


def test_openrouter_provider_availability_reflects_env_var(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert ai_provider.list_providers()["openrouter"]["available"] is False

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    assert ai_provider.list_providers()["openrouter"]["available"] is True


def test_minimax_provider_availability_reflects_env_var(monkeypatch):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    assert ai_provider.list_providers()["minimax"]["available"] is False

    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    assert ai_provider.list_providers()["minimax"]["available"] is True


def test_claude_provider_has_both_capabilities():
    providers = ai_provider.list_providers()

    assert set(providers["claude"]["capabilities"]) == {"coding_agent", "text_task", "file_access"}


def test_gemini_provider_availability_reflects_env_var(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert ai_provider.list_providers()["gemini"]["available"] is False

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    assert ai_provider.list_providers()["gemini"]["available"] is True


def test_claude_run_text_task_wraps_coding_bridge_without_file_changes(monkeypatch, tmp_path):
    import core.ai_provider as provider_module

    captured = {}

    def fake_run_coding_task(project_path, instruction, **kwargs):
        captured["instruction"] = instruction
        captured["project_path"] = project_path
        return {"response_text": "some architecture thoughts", "success": True}

    monkeypatch.setattr(provider_module, "_claude_run_coding_task", fake_run_coding_task)

    claude = ai_provider.get_provider("claude")
    result = claude["run_text_task"]("design a queue system", project_path=str(tmp_path))

    assert result == "some architecture thoughts"
    assert "do not" in captured["instruction"].lower() or "not modify" in captured["instruction"].lower()
    assert captured["project_path"] == str(tmp_path)


def test_claude_run_text_task_surfaces_raw_error_and_raises_on_failure(monkeypatch, tmp_path):
    import core.ai_provider as provider_module
    import core.ai.provider_health as provider_health

    def fake_run_coding_task(project_path, instruction, **kwargs):
        return {
            "success": False,
            "response_text": "",
            "tool_errors": [{"tool": None, "content": "Claude usage limit reached. Resets at 3pm."}],
        }

    monkeypatch.setattr(provider_module, "_claude_run_coding_task", fake_run_coding_task)

    claude = provider_module.get_provider("claude")
    with pytest.raises(RuntimeError):
        claude["run_text_task"]("quick question", project_path=str(tmp_path))

    snapshot = provider_health.get_quota_snapshot("claude")
    assert snapshot["status"] == "error"
    assert "usage limit reached" in snapshot["detail"].lower()


def test_claude_run_text_task_treats_a_successful_but_empty_response_as_a_failure(monkeypatch, tmp_path):
    # Confirmed live 2026-08-01: success=True with an empty response_text
    # was silently returned as "" instead of triggering delegate()'s
    # fallback to the next candidate provider.
    import core.ai_provider as provider_module
    import core.ai.provider_health as provider_health

    def fake_run_coding_task(project_path, instruction, **kwargs):
        return {"success": True, "response_text": "", "tool_errors": []}

    monkeypatch.setattr(provider_module, "_claude_run_coding_task", fake_run_coding_task)

    claude = provider_module.get_provider("claude")
    with pytest.raises(RuntimeError):
        claude["run_text_task"]("quick question", project_path=str(tmp_path))

    snapshot = provider_health.get_quota_snapshot("claude")
    assert snapshot["status"] == "error"
    assert "empty" in snapshot["detail"].lower()


def test_claude_run_text_task_uses_a_scratch_workspace_when_no_project_path_given(monkeypatch):
    import core.ai_provider as provider_module

    captured = {}

    def fake_run_coding_task(project_path, instruction, **kwargs):
        captured["project_path"] = project_path
        return {"response_text": "ok", "success": True}

    monkeypatch.setattr(provider_module, "_claude_run_coding_task", fake_run_coding_task)

    claude = ai_provider.get_provider("claude")
    claude["run_text_task"]("quick question")

    assert captured["project_path"]  # some real path was used, not None


def test_gemini_groq_openai_run_text_task_ignores_project_path(monkeypatch):
    import core.llm_clients as llm_clients

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(llm_clients, "call_gemini", lambda prompt, timeout=60: "response text")

    gemini = ai_provider.get_provider("gemini")
    # project_path is accepted (uniform contract with claude) but unused
    result = gemini["run_text_task"]("hello", project_path="/does/not/matter")

    assert result == "response text"


def test_geminix_provider_availability_reflects_env_var(monkeypatch):
    monkeypatch.delenv("GEMINIX_API_KEY", raising=False)
    assert ai_provider.list_providers()["geminix"]["available"] is False

    monkeypatch.setenv("GEMINIX_API_KEY", "test-key")
    assert ai_provider.list_providers()["geminix"]["available"] is True


def test_geminix_run_text_task_calls_llm_clients_call_geminix(monkeypatch):
    import core.llm_clients as llm_clients

    monkeypatch.setenv("GEMINIX_API_KEY", "test-key")
    captured = {}

    def fake_call_geminix(prompt, timeout=60):
        captured["prompt"] = prompt
        return "geminix response"

    monkeypatch.setattr(llm_clients, "call_geminix", fake_call_geminix)

    provider = ai_provider.get_provider("geminix")
    result = provider["run_text_task"]("hello")
    assert result == "geminix response"
    assert captured["prompt"] == "hello"


def test_geminix_provider_has_text_task_capability_only():
    providers = ai_provider.list_providers()
    assert "geminix" in providers
    assert "text_task" in providers["geminix"]["capabilities"]
    assert "coding_agent" not in providers["geminix"]["capabilities"]


def test_claude_provider_availability_reflects_bridge_key_presence(tmp_path, monkeypatch):
    import core.coding_bridge as bridge

    key_path = tmp_path / "cloudcli_api_key"
    monkeypatch.setattr(bridge, "API_KEY_PATH", key_path)

    providers = ai_provider.list_providers()
    assert providers["claude"]["available"] is False

    key_path.write_text("ck_test")
    providers = ai_provider.list_providers()
    assert providers["claude"]["available"] is True


def test_opencode_provider_is_registered_with_coding_agent_capability_only():
    providers = ai_provider.list_providers()

    assert "opencode" in providers
    assert "coding_agent" in providers["opencode"]["capabilities"]
    assert "text_task" not in providers["opencode"]["capabilities"]


def test_opencode_provider_run_coding_task_wraps_the_bridge_module(monkeypatch, tmp_path):
    import core.opencode_bridge as opencode_bridge

    captured = {}

    def fake_run_coding_task(project_path, instruction, **kwargs):
        captured["project_path"] = project_path
        captured["instruction"] = instruction
        return {"success": True, "response_text": "ok", "files_changed": [], "commits": [], "tool_errors": []}

    monkeypatch.setattr(opencode_bridge, "run_coding_task", fake_run_coding_task)

    provider = ai_provider.get_provider("opencode")
    result = provider["run_coding_task"](str(tmp_path), "build a widget")

    assert result["success"] is True
    assert captured["project_path"] == str(tmp_path)
    assert captured["instruction"] == "build a widget"


def test_opencode_provider_unavailable_when_cli_missing(monkeypatch):
    import core.ai_provider as provider_module

    monkeypatch.setattr(provider_module.shutil, "which", lambda name: None)

    assert ai_provider.list_providers()["opencode"]["available"] is False


def test_opencode_provider_unavailable_when_cli_present_but_not_authenticated(monkeypatch, tmp_path):
    import core.ai_provider as provider_module

    monkeypatch.setattr(provider_module.shutil, "which", lambda name: "/usr/bin/opencode")
    monkeypatch.setattr(provider_module, "OPENCODE_AUTH_PATH", tmp_path / "auth.json")

    assert ai_provider.list_providers()["opencode"]["available"] is False


def test_opencode_provider_available_when_cli_present_and_authenticated(monkeypatch, tmp_path):
    import core.ai_provider as provider_module

    auth_path = tmp_path / "auth.json"
    auth_path.write_text('{"opencode": {"type": "api", "key": "sk-test"}}')

    monkeypatch.setattr(provider_module.shutil, "which", lambda name: "/usr/bin/opencode")
    monkeypatch.setattr(provider_module, "OPENCODE_AUTH_PATH", auth_path)

    assert ai_provider.list_providers()["opencode"]["available"] is True


def test_opencode_claude_provider_is_registered_with_both_capabilities():
    # 2026-08-02 operator directive: Fable 5 gained a text_task route
    # (Q&A, see ai_provider._opencode_claude_run_text_task) alongside its
    # original coding_agent one -- was coding_agent-only until this.
    providers = ai_provider.list_providers()

    assert "opencode_claude" in providers
    assert "coding_agent" in providers["opencode_claude"]["capabilities"]
    assert "text_task" in providers["opencode_claude"]["capabilities"]


def test_opencode_claude_provider_defaults_to_a_claude_model_via_zen(monkeypatch, tmp_path):
    import core.opencode_bridge as opencode_bridge

    captured = {}

    def fake_run_coding_task(project_path, instruction, **kwargs):
        captured["model"] = kwargs.get("model")
        return {"success": True, "response_text": "ok", "files_changed": [], "commits": [], "tool_errors": []}

    monkeypatch.setattr(opencode_bridge, "run_coding_task", fake_run_coding_task)

    provider = ai_provider.get_provider("opencode_claude")
    provider["run_coding_task"](str(tmp_path), "build a widget")

    assert "claude" in captured["model"]
    assert captured["model"].startswith("opencode/")


def test_opencode_claude_provider_shares_availability_with_opencode(monkeypatch, tmp_path):
    import core.ai_provider as provider_module

    auth_path = tmp_path / "auth.json"
    auth_path.write_text('{"opencode": {"type": "api", "key": "sk-test"}}')

    monkeypatch.setattr(provider_module.shutil, "which", lambda name: "/usr/bin/opencode")
    monkeypatch.setattr(provider_module, "OPENCODE_AUTH_PATH", auth_path)

    assert ai_provider.list_providers()["opencode_claude"]["available"] is True

    monkeypatch.setattr(provider_module.shutil, "which", lambda name: None)
    assert ai_provider.list_providers()["opencode_claude"]["available"] is False


@pytest.mark.parametrize(
    "provider_name,expected_model_fragment",
    [
        ("opencode_claude_sonnet", "claude-sonnet-5"),
        ("opencode_claude_opus", "claude-opus-5"),
    ],
)
def test_opencode_claude_escalation_tiers_are_registered_with_coding_agent_capability_only(
    provider_name, expected_model_fragment
):
    providers = ai_provider.list_providers()

    assert provider_name in providers
    assert "coding_agent" in providers[provider_name]["capabilities"]
    assert "text_task" not in providers[provider_name]["capabilities"]


@pytest.mark.parametrize(
    "provider_name,expected_model_fragment",
    [
        ("opencode_claude_sonnet", "claude-sonnet-5"),
        ("opencode_claude_opus", "claude-opus-5"),
    ],
)
def test_opencode_claude_escalation_tiers_use_the_correct_zen_model(
    monkeypatch, tmp_path, provider_name, expected_model_fragment
):
    import core.opencode_bridge as opencode_bridge

    captured = {}

    def fake_run_coding_task(project_path, instruction, **kwargs):
        captured["model"] = kwargs.get("model")
        return {"success": True, "response_text": "ok", "files_changed": [], "commits": [], "tool_errors": []}

    monkeypatch.setattr(opencode_bridge, "run_coding_task", fake_run_coding_task)

    provider = ai_provider.get_provider(provider_name)
    provider["run_coding_task"](str(tmp_path), "build a widget")

    assert captured["model"] == f"opencode/{expected_model_fragment}"


@pytest.mark.parametrize("provider_name", ["opencode_claude_sonnet", "opencode_claude_opus"])
def test_opencode_claude_escalation_tiers_share_availability_with_opencode(monkeypatch, tmp_path, provider_name):
    import core.ai_provider as provider_module

    auth_path = tmp_path / "auth.json"
    auth_path.write_text('{"opencode": {"type": "api", "key": "sk-test"}}')

    monkeypatch.setattr(provider_module.shutil, "which", lambda name: "/usr/bin/opencode")
    monkeypatch.setattr(provider_module, "OPENCODE_AUTH_PATH", auth_path)

    assert ai_provider.list_providers()[provider_name]["available"] is True

    monkeypatch.setattr(provider_module.shutil, "which", lambda name: None)
    assert ai_provider.list_providers()[provider_name]["available"] is False


# --- 13T: minimax restored on the coding_agent route only -------------------

def test_opencode_minimax_provider_is_registered_with_coding_agent_capability_only():
    # 13T: minimax-m2.7 is registered for coding_agent and *only* coding_agent
    # -- the usage-history review found its tools-less text_task record to be
    # 0/4 usable, so it must never be reachable as a text provider.
    providers = ai_provider.list_providers()

    assert "opencode_minimax" in providers
    assert "coding_agent" in providers["opencode_minimax"]["capabilities"]
    assert "text_task" not in providers["opencode_minimax"]["capabilities"]


def test_opencode_minimax_provider_uses_the_minimax_zen_model(monkeypatch, tmp_path):
    import core.opencode_bridge as opencode_bridge

    captured = {}

    def fake_run_coding_task(project_path, instruction, **kwargs):
        captured["model"] = kwargs.get("model")
        return {"success": True, "response_text": "ok", "files_changed": [], "commits": [], "tool_errors": []}

    monkeypatch.setattr(opencode_bridge, "run_coding_task", fake_run_coding_task)

    provider = ai_provider.get_provider("opencode_minimax")
    provider["run_coding_task"](str(tmp_path), "build a widget")

    assert captured["model"] == "opencode/minimax-m2.7"


def test_opencode_minimax_provider_shares_availability_with_opencode(monkeypatch, tmp_path):
    import core.ai_provider as provider_module

    auth_path = tmp_path / "auth.json"
    auth_path.write_text('{"opencode": {"type": "api", "key": "sk-test"}}')

    monkeypatch.setattr(provider_module.shutil, "which", lambda name: "/usr/bin/opencode")
    monkeypatch.setattr(provider_module, "OPENCODE_AUTH_PATH", auth_path)

    assert ai_provider.list_providers()["opencode_minimax"]["available"] is True

    monkeypatch.setattr(provider_module.shutil, "which", lambda name: None)
    assert ai_provider.list_providers()["opencode_minimax"]["available"] is False


def test_minimax_text_provider_is_still_registered_but_text_only():
    # The registry entry stays (it is what any future re-evaluation would
    # exercise); what 13T changed is only whether ai_router routes to it.
    providers = ai_provider.list_providers()

    assert "text_task" in providers["minimax"]["capabilities"]
    assert "coding_agent" not in providers["minimax"]["capabilities"]


# --- 13U: deepseek text-task provider + opencode_deepseek coding route ------

def test_deepseek_provider_availability_reflects_env_var(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_OPENROUTER_API_KEY", raising=False)
    assert ai_provider.list_providers()["deepseek"]["available"] is False

    monkeypatch.setenv("DEEPSEEK_OPENROUTER_API_KEY", "test-key")
    assert ai_provider.list_providers()["deepseek"]["available"] is True


def test_deepseek_run_text_task_calls_llm_clients_call_deepseek(monkeypatch):
    import core.llm_clients as llm_clients

    monkeypatch.setenv("DEEPSEEK_OPENROUTER_API_KEY", "test-key")
    captured = {}

    def fake_call_deepseek(prompt, timeout=60):
        captured["prompt"] = prompt
        return "deepseek response"

    monkeypatch.setattr(llm_clients, "call_deepseek", fake_call_deepseek)

    provider = ai_provider.get_provider("deepseek")
    result = provider["run_text_task"]("hello")
    assert result == "deepseek response"
    assert captured["prompt"] == "hello"


def test_deepseek_provider_has_text_task_capability_only():
    providers = ai_provider.list_providers()
    assert "deepseek" in providers
    assert "text_task" in providers["deepseek"]["capabilities"]
    assert "coding_agent" not in providers["deepseek"]["capabilities"]




# --- 2026-08-02: qwen4_coding -- self-hosted Qwen3-Coder as a real coding_agent, via a custom opencode provider ---

def test_qwen4_coding_provider_is_registered_with_coding_agent_capability_only():
    providers = ai_provider.list_providers()
    assert "qwen4_coding" in providers
    assert "coding_agent" in providers["qwen4_coding"]["capabilities"]
    assert "text_task" not in providers["qwen4_coding"]["capabilities"]


def test_qwen4_coding_provider_defaults_to_correct_model(monkeypatch, tmp_path):
    # V3: qwen4_coding uses direct vLLM API (call_qwen4_text),
    # NOT opencode_bridge. The model is set via QWEN3_CODING_MODEL.
    from core.ai_provider import QWEN3_CODING_MODEL
    assert "Qwen3-32B-FP8" in QWEN3_CODING_MODEL or "Qwen/Qwen3-32B-FP8" in QWEN3_CODING_MODEL


def test_qwen4_coding_provider_availability_requires_opencode_and_both_env_vars(monkeypatch):
    import core.ai_provider as provider_module

    # V3: qwen4_coding availability only checks VLLM_QWEN3_CODER env vars,
    # not shutil.which (direct vLLM API doesn't need opencode CLI).
    monkeypatch.setenv("VLLM_QWEN3_CODER_API_KEY", "test-key")
    monkeypatch.setenv("VLLM_QWEN3_CODER_BASE_URL", "https://example.proxy.runpod.net/v1")
    assert ai_provider.list_providers()["qwen4_coding"]["available"] is True

    monkeypatch.delenv("VLLM_QWEN3_CODER_BASE_URL", raising=False)
    assert ai_provider.list_providers()["qwen4_coding"]["available"] is False


# --- 17R item 1: native DeepSeek providers (api.deepseek.com, no OpenRouter/Zen quota exposure) ---

def test_deepseek_native_pro_provider_availability_reflects_env_var(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_NATIVE_PRO_API_KEY", raising=False)
    assert ai_provider.list_providers()["deepseek_native_pro"]["available"] is False

    monkeypatch.setenv("DEEPSEEK_NATIVE_PRO_API_KEY", "test-key")
    assert ai_provider.list_providers()["deepseek_native_pro"]["available"] is True


def test_deepseek_native_flash_provider_availability_reflects_env_var(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_NATIVE_FLASH_API_KEY", raising=False)
    assert ai_provider.list_providers()["deepseek_native_flash"]["available"] is False

    monkeypatch.setenv("DEEPSEEK_NATIVE_FLASH_API_KEY", "test-key")
    assert ai_provider.list_providers()["deepseek_native_flash"]["available"] is True


def test_deepseek_native_pro_run_text_task_calls_llm_clients_call_deepseek_native_pro(monkeypatch):
    import core.llm_clients as llm_clients

    monkeypatch.setenv("DEEPSEEK_NATIVE_PRO_API_KEY", "test-key")
    captured = {}

    def fake_call(prompt, timeout=60):
        captured["prompt"] = prompt
        return "deepseek native pro response"

    monkeypatch.setattr(llm_clients, "call_deepseek_native_pro", fake_call)

    provider = ai_provider.get_provider("deepseek_native_pro")
    result = provider["run_text_task"]("hello")
    assert result == "deepseek native pro response"
    assert captured["prompt"] == "hello"


def test_deepseek_native_flash_run_text_task_calls_llm_clients_call_deepseek_native_flash(monkeypatch):
    import core.llm_clients as llm_clients

    monkeypatch.setenv("DEEPSEEK_NATIVE_FLASH_API_KEY", "test-key")
    captured = {}

    def fake_call(prompt, timeout=60):
        captured["prompt"] = prompt
        return "deepseek native flash response"

    monkeypatch.setattr(llm_clients, "call_deepseek_native_flash", fake_call)

    provider = ai_provider.get_provider("deepseek_native_flash")
    result = provider["run_text_task"]("hello")
    assert result == "deepseek native flash response"
    assert captured["prompt"] == "hello"


def test_deepseek_native_providers_have_text_task_capability_only():
    providers = ai_provider.list_providers()
    for name in ("deepseek_native_pro", "deepseek_native_flash"):
        assert name in providers
        assert "text_task" in providers[name]["capabilities"]
        assert "coding_agent" not in providers[name]["capabilities"]


def test_deepseek_native_flash_does_not_use_the_shared_openrouter_or_zen_credentials(monkeypatch):
    # Confirms this route has no shared-quota exposure to OpenRouter/Zen --
    # the whole point of registering it (2026-08-02: gemini and every
    # OpenRouter-routed candidate were simultaneously credit/quota-exhausted).
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_NATIVE_FLASH_API_KEY", "test-key")

    import core.llm_clients as llm_clients
    monkeypatch.setattr(llm_clients, "call_deepseek_native_flash", lambda prompt, timeout=60: "ok")

    assert ai_provider.get_provider("deepseek_native_flash")["run_text_task"]("hello") == "ok"


def test_every_registered_provider_has_a_valid_cost_tier():
    providers = ai_provider.list_providers()

    assert providers  # sanity: the registry is not empty
    for name, info in providers.items():
        assert "cost_tier" in info, name
        assert info["cost_tier"] in ai_provider.COST_TIERS, name


@pytest.mark.parametrize(
    "provider_name,expected_tier",
    [
        # free-tier API keys
        ("gemini", "free"),
        ("groq", "free"),
        ("local", "free"),
        # cheap/credit-pool models, per the user's own labeling request
        ("minimax", "free_or_low_cost"),
        ("deepseek", "free_or_low_cost"),
        ("opencode", "free_or_low_cost"),
        ("opencode_claude", "free_or_low_cost"),
        ("opencode_minimax", "free_or_low_cost"),
        # 2026-08-02: qwen4_coding -- same self-hosted RunPod GPU as "qwen4_text"
        # below, own-hardware billing rather than per-call.
        ("qwen4_coding", "free_or_low_cost"),
        # 2026-08-02: "qwen4_text" now means the self-hosted Qwen3-Coder vLLM
        # instance (own RunPod GPU pod, not per-call billing) -- moved out
        # of "paid" the same day it was repointed away from the real OpenAI
        # API. See core.llm_clients.call_openai / core.ai_provider's
        # "qwen4_text" registration.
        ("qwen4_text", "paid"),
        # real per-call billing / materially expensive models
        ("claude", "paid"),
        ("openrouter", "paid"),
        ("opencode_claude_sonnet", "paid"),
        ("opencode_claude_opus", "paid"),
    ],
)
def test_provider_cost_tier_matches_the_static_classification(provider_name, expected_tier):
    assert ai_provider.list_providers()[provider_name]["cost_tier"] == expected_tier


def test_register_provider_rejects_an_unknown_cost_tier():
    with pytest.raises(ValueError, match="cost_tier"):
        ai_provider.register_provider(
            "bad-tier-provider",
            run_text_task=lambda *a, **k: "",
            available_fn=lambda: False,
            cost_tier="expensive",
        )

    assert ai_provider.get_provider("bad-tier-provider") is None


def test_deepseek_run_text_task_does_not_use_the_shared_OPENROUTER_API_KEY(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "shared-key")
    monkeypatch.delenv("DEEPSEEK_OPENROUTER_API_KEY", raising=False)

    assert ai_provider.list_providers()["deepseek"]["available"] is False

    import core.llm_clients as llm_clients
    with pytest.raises(llm_clients.ProviderUnavailable, match="DEEPSEEK_OPENROUTER_API_KEY"):
        ai_provider.get_provider("deepseek")["run_text_task"]("hello")


# --- 13M: opencode credential generalization ---------------------------------

def test_opencode_credential_available_checks_each_key_independently(monkeypatch, tmp_path):
    import core.ai_provider as provider_module

    auth_path = tmp_path / "auth.json"
    auth_path.write_text('{"opencode": {"type": "api", "key": "sk-zen"}}')

    monkeypatch.setattr(provider_module.shutil, "which", lambda name: "/usr/bin/opencode")
    monkeypatch.setattr(provider_module, "OPENCODE_AUTH_PATH", auth_path)

    assert provider_module._opencode_credential_available("opencode") is True
    assert provider_module._opencode_credential_available("openrouter") is False

    auth_path.write_text(
        '{"opencode": {"type": "api", "key": "sk-zen"}, "openrouter": {"type": "api", "key": "sk-or"}}'
    )
    assert provider_module._opencode_credential_available("openrouter") is True


def test_opencode_credential_available_is_false_when_cli_missing(monkeypatch, tmp_path):
    import core.ai_provider as provider_module

    auth_path = tmp_path / "auth.json"
    auth_path.write_text('{"openrouter": {"type": "api", "key": "sk-or"}}')

    monkeypatch.setattr(provider_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(provider_module, "OPENCODE_AUTH_PATH", auth_path)

    assert provider_module._opencode_credential_available("openrouter") is False


def test_opencode_credential_available_is_false_when_auth_file_missing_or_corrupt(monkeypatch, tmp_path):
    import core.ai_provider as provider_module

    monkeypatch.setattr(provider_module.shutil, "which", lambda name: "/usr/bin/opencode")
    monkeypatch.setattr(provider_module, "OPENCODE_AUTH_PATH", tmp_path / "auth.json")

    assert provider_module._opencode_credential_available("openrouter") is False

    (tmp_path / "auth.json").write_text("{not json")
    assert provider_module._opencode_credential_available("openrouter") is False


def test_opencode_available_is_a_thin_wrapper_over_the_generalized_helper(monkeypatch):
    import core.ai_provider as provider_module

    captured = {}

    def fake_credential_available(key):
        captured["key"] = key
        return True

    monkeypatch.setattr(provider_module, "_opencode_credential_available", fake_credential_available)

    assert provider_module._opencode_available() is True
    assert captured["key"] == "opencode"


# --- 13M: OpenRouter-billed Claude coding routes via the opencode CLI --------

@pytest.mark.parametrize("provider_name", [])
def test_openrouter_claude_coding_providers_are_registered_with_coding_agent_capability_only(provider_name):
    providers = ai_provider.list_providers()

    assert provider_name in providers
    assert "coding_agent" in providers[provider_name]["capabilities"]
    assert "text_task" not in providers[provider_name]["capabilities"]


def test_13v_text_task_openrouter_claude_provider_is_not_overwritten():
    # 13V shipped a text_task-only "openrouter_claude" (anthropic/
    # claude-sonnet-4.6 via OpenRouter's plain chat-completions API) for the
    # Chief Architect chain. 13M's coding routes use distinct keys
    # (openrouter_claude_opus/openrouter_claude_sonnet) -- the 13V entry must
    # survive unchanged.
    providers = ai_provider.list_providers()

    assert "openrouter_claude" in providers
    assert "text_task" in providers["openrouter_claude"]["capabilities"]
    assert "coding_agent" not in providers["openrouter_claude"]["capabilities"]

    entry = ai_provider.get_provider("openrouter_claude")
    assert entry["run_coding_task"] is None
    assert entry["run_text_task"] is not None



@pytest.mark.parametrize("provider_name", [])
def test_openrouter_claude_coding_providers_respect_an_explicit_model_override(
    monkeypatch, tmp_path, provider_name
):
    import core.opencode_bridge as opencode_bridge

    captured = {}

    def fake_run_coding_task(project_path, instruction, **kwargs):
        captured["model"] = kwargs.get("model")
        return {"success": True, "response_text": "ok", "files_changed": [], "commits": [], "tool_errors": []}

    monkeypatch.setattr(opencode_bridge, "run_coding_task", fake_run_coding_task)

    provider = ai_provider.get_provider(provider_name)
    provider["run_coding_task"](str(tmp_path), "build a widget", model="openrouter/some/other-model")

    assert captured["model"] == "openrouter/some/other-model"


@pytest.mark.parametrize("provider_name", [])
def test_openrouter_claude_coding_providers_are_gated_on_the_openrouter_credential(
    monkeypatch, tmp_path, provider_name
):
    import core.ai_provider as provider_module

    auth_path = tmp_path / "auth.json"
    monkeypatch.setattr(provider_module.shutil, "which", lambda name: "/usr/bin/opencode")
    monkeypatch.setattr(provider_module, "OPENCODE_AUTH_PATH", auth_path)

    # A Zen-only auth.json is NOT enough -- these bill through OpenRouter.
    auth_path.write_text('{"opencode": {"type": "api", "key": "sk-zen"}}')
    assert ai_provider.list_providers()[provider_name]["available"] is False

    auth_path.write_text('{"openrouter": {"type": "api", "key": "sk-or"}}')
    assert ai_provider.list_providers()[provider_name]["available"] is True

    monkeypatch.setattr(provider_module.shutil, "which", lambda name: None)
    assert ai_provider.list_providers()[provider_name]["available"] is False


# --- 13M: openrouter text-task model rotation --------------------------------

def test_next_openrouter_model_cycles_through_all_models_before_repeating():
    import core.ai_provider as provider_module
    import core.llm_clients as llm_clients

    seen = [provider_module._next_openrouter_model() for _ in range(len(llm_clients.OPENROUTER_MODELS))]

    assert seen == llm_clients.OPENROUTER_MODELS
    # The next call wraps back to the start of the list.
    assert provider_module._next_openrouter_model() == llm_clients.OPENROUTER_MODELS[0]


def test_next_openrouter_model_starts_from_index_zero_when_no_state_file_exists(isolated_memory):
    import core.ai_provider as provider_module
    import core.llm_clients as llm_clients

    assert not (isolated_memory / provider_module.OPENROUTER_MODEL_ROTATION_FILE).exists()
    assert provider_module._next_openrouter_model() == llm_clients.OPENROUTER_MODELS[0]


def test_next_openrouter_model_persists_rotation_state_across_calls(isolated_memory):
    import core.ai_provider as provider_module
    from core.memory import load

    provider_module._next_openrouter_model()
    provider_module._next_openrouter_model()

    # State lives on disk (not in-process), so it survives across calls and
    # process restarts alike.
    assert (isolated_memory / provider_module.OPENROUTER_MODEL_ROTATION_FILE).exists()
    assert load(provider_module.OPENROUTER_MODEL_ROTATION_FILE)["index"] == 2


def test_openrouter_run_text_task_uses_the_rotated_model(monkeypatch):
    import core.llm_clients as llm_clients

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    models_used = []

    def fake_call_openrouter(prompt, model=llm_clients.OPENROUTER_DEFAULT_MODEL, timeout=60):
        models_used.append(model)
        return "rotated response"

    monkeypatch.setattr(llm_clients, "call_openrouter", fake_call_openrouter)

    provider = ai_provider.get_provider("openrouter")
    assert provider["run_text_task"]("hello") == "rotated response"
    provider["run_text_task"]("hello again")

    assert models_used == llm_clients.OPENROUTER_MODELS[:2]


# 17Z: Qwen3-Coder-30B-A3B-Instruct-AWQ text-task provider on RunPod RTX 5090
def test_qwen4_text_provider_is_registered():
    provider = ai_provider.get_provider("qwen4_text")
    assert provider is not None
    assert provider["run_text_task"] is not None
    assert provider["run_coding_task"] is None  # text-task only, no coding agent
    assert provider["cost_tier"] == "paid"  # real per-request GPU billing


def test_qwen4_text_availability_reflects_env_vars(monkeypatch):
    monkeypatch.delenv("VLLM_QWEN3_CODER_API_KEY", raising=False)
    monkeypatch.delenv("VLLM_QWEN3_CODER_BASE_URL", raising=False)
    assert ai_provider.list_providers()["qwen4_text"]["available"] is False

    monkeypatch.setenv("VLLM_QWEN3_CODER_API_KEY", "test-key")
    assert ai_provider.list_providers()["qwen4_text"]["available"] is False  # still needs URL

    monkeypatch.setenv("VLLM_QWEN3_CODER_BASE_URL", "https://test.runpod.net/v1")
    assert ai_provider.list_providers()["qwen4_text"]["available"] is True


def test_qwen4_text_run_text_task_calls_llm_clients(monkeypatch):
    import core.llm_clients as llm_clients

    monkeypatch.setenv("VLLM_QWEN3_CODER_API_KEY", "test-key")
    monkeypatch.setenv("VLLM_QWEN3_CODER_BASE_URL", "https://test.runpod.net/v1")
    captured = {}

    def fake_call_qwen4_text(prompt, timeout=60):
        captured["prompt"] = prompt
        return "qwen4_text response"

    monkeypatch.setattr(llm_clients, "call_qwen4_text", fake_call_qwen4_text)

    provider = ai_provider.get_provider("qwen4_text")
    result = provider["run_text_task"]("what is the capital of ghana?")

    assert result == "qwen4_text response"
    assert captured["prompt"] == "what is the capital of ghana?"


def test_qwen4_text_has_text_task_capability_only():
    entry = ai_provider.list_providers()["qwen4_text"]
    assert "text_task" in entry["capabilities"]
    assert "coding_agent" not in entry["capabilities"]
