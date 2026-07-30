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

    for name in ("gemini", "groq", "openai", "openrouter", "minimax", "deepseek"):
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

    assert set(providers["claude"]["capabilities"]) == {"coding_agent", "text_task"}


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


def test_opencode_claude_provider_is_registered_with_coding_agent_capability_only():
    providers = ai_provider.list_providers()

    assert "opencode_claude" in providers
    assert "coding_agent" in providers["opencode_claude"]["capabilities"]
    assert "text_task" not in providers["opencode_claude"]["capabilities"]


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


def test_opencode_deepseek_provider_is_registered_with_coding_agent_capability_only():
    providers = ai_provider.list_providers()
    assert "opencode_deepseek" in providers
    assert "coding_agent" in providers["opencode_deepseek"]["capabilities"]
    assert "text_task" not in providers["opencode_deepseek"]["capabilities"]


def test_opencode_deepseek_provider_defaults_to_correct_model(monkeypatch, tmp_path):
    import core.opencode_bridge as opencode_bridge

    captured = {}

    def fake_run_coding_task(project_path, instruction, **kwargs):
        captured["model"] = kwargs.get("model")
        return {"success": True, "response_text": "ok", "files_changed": [], "commits": [], "tool_errors": []}

    monkeypatch.setattr(opencode_bridge, "run_coding_task", fake_run_coding_task)

    provider = ai_provider.get_provider("opencode_deepseek")
    provider["run_coding_task"](str(tmp_path), "build a widget")

    assert captured["model"] == "openrouter/deepseek/deepseek-v4-pro"


def test_opencode_deepseek_provider_shares_availability_with_opencode(monkeypatch, tmp_path):
    import core.ai_provider as provider_module

    auth_path = tmp_path / "auth.json"
    auth_path.write_text('{"opencode": {"type": "api", "key": "sk-test"}}')

    monkeypatch.setattr(provider_module.shutil, "which", lambda name: "/usr/bin/opencode")
    monkeypatch.setattr(provider_module, "OPENCODE_AUTH_PATH", auth_path)

    assert ai_provider.list_providers()["opencode_deepseek"]["available"] is True

    monkeypatch.setattr(provider_module.shutil, "which", lambda name: None)
    assert ai_provider.list_providers()["opencode_deepseek"]["available"] is False


# --- 13W: static cost_tier classification per provider ----------------------

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
        ("opencode_deepseek", "free_or_low_cost"),
        # real per-call billing / materially expensive models
        ("claude", "paid"),
        ("openai", "paid"),
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
