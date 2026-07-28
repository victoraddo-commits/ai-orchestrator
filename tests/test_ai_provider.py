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


def test_claude_provider_availability_reflects_bridge_key_presence(tmp_path, monkeypatch):
    import core.coding_bridge as bridge

    key_path = tmp_path / "cloudcli_api_key"
    monkeypatch.setattr(bridge, "API_KEY_PATH", key_path)

    providers = ai_provider.list_providers()
    assert providers["claude"]["available"] is False

    key_path.write_text("ck_test")
    providers = ai_provider.list_providers()
    assert providers["claude"]["available"] is True
