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

    for name in ("gemini", "groq", "openai"):
        assert name in providers
        assert "text_task" in providers[name]["capabilities"]
        assert "coding_agent" not in providers[name]["capabilities"]


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
