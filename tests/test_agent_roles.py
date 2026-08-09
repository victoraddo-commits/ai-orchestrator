import core.ai.agent_roles as agent_roles


def _stub_provider(monkeypatch, name, response):
    import core.ai_provider as ai_provider

    provider = ai_provider.get_provider(name)
    monkeypatch.setitem(provider, "available_fn", lambda: True)
    monkeypatch.setitem(provider, "enabled", True)
    monkeypatch.setitem(provider, "run_text_task", lambda p, timeout=60, project_path=None: response)


def _ensure_in_role_providers(monkeypatch, name, task_type):
    """Re-add a disabled provider to ROLE_PROVIDERS so tests can reach it."""
    import core.ai.ai_router as ai_router
    providers = list(ai_router.ROLE_PROVIDERS.get(task_type, []))
    if name not in providers:
        providers.append(name)
    monkeypatch.setitem(ai_router.ROLE_PROVIDERS, task_type, providers)


def test_architecture_agent_routes_to_claude(monkeypatch):
    # opencode_claude gained a real text_task route 2026-08-02 and leads
    # "coding" ahead of claude -- disabled so this test doesn't make a real
    # opencode/Zen call.
    import core.ai_provider as ai_provider
    monkeypatch.setitem(ai_provider.get_provider("opencode_claude"), "available_fn", lambda: False)

    _ensure_in_role_providers(monkeypatch, "claude", "coding")
    _stub_provider(monkeypatch, "claude", "claude answered")

    result = agent_roles.architecture_agent("Design the new module")

    assert result["provider"] == "claude"
    assert result["task_type"] == "coding"


def test_research_agent_routes_to_claude_fallback(monkeypatch):
    # gemini removed from "planning" entirely 2026-08-02 (disabled,
    # quota-exhausted) -- claude (now last) demonstrates the fallback.
    # opencode_claude gained a real text_task route 2026-08-02 -- disabled
    # so this test doesn't make a real opencode/Zen call. deepseek_native_pro
    # joined "planning" the same day -- disabled for the same reason (real
    # api.deepseek.com call).
    import core.ai_provider as ai_provider
    # gemini re-enabled 2026-08-02 (credit reloaded) and rejoined "planning".
    # Only disable providers that are actually registered (qwen may be absent
    # when RunPod env vars aren't set).
    to_disable = [n for n in ("deepseek_native_flash", "omniroute_deepseek_flash",
                    "openrouter", "deepseek", "opencode_claude",
                    "deepseek_native_pro", "gemini", "geminix",
                    "qwen4_text", "qwen4_pod_b")
                  if ai_provider.get_provider(n) is not None]
    for name in to_disable:
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    _ensure_in_role_providers(monkeypatch, "claude", "planning")
    _stub_provider(monkeypatch, "claude", "claude answered")

    result = agent_roles.research_agent("Summarize the docs")

    assert result["provider"] == "claude"
    assert result["task_type"] == "planning"


def test_fast_analysis_agent_routes_to_groq(monkeypatch):
    import core.ai_provider as ai_provider
    # qwen4_text (was qwen3_coder_text) leads "classification" — disable
    # it so groq is the next candidate this test expects. Guard against
    # the provider not being registered (RunPod env vars not set).
    qwen = ai_provider.get_provider("qwen4_text") or ai_provider.get_provider("qwen3_coder_text")
    if qwen is not None:
        monkeypatch.setitem(qwen, "available_fn", lambda: False)
    # groq is disabled in persisted provider state — re-enable for this test.
    monkeypatch.setitem(ai_provider.get_provider("groq"), "enabled", True)
    _stub_provider(monkeypatch, "groq", "groq answered")

    result = agent_roles.fast_analysis_agent("Triage this log")

    assert result["provider"] == "groq"
    assert result["task_type"] == "log_analysis"


def test_general_reasoning_agent_routes_to_qwen4_text(monkeypatch):
    import core.ai_provider as ai_provider
    # qwen4_text (was openai) leads "review" — but only when RunPod is configured.
    # Fall back to the actual primary provider when qwen4_text isn't registered.
    provider_name = "qwen4_text" if ai_provider.get_provider("qwen4_text") else "qwen4_pod_b"
    if ai_provider.get_provider(provider_name) is None:
        import pytest
        pytest.skip("qwen4_text and qwen4_pod_b not registered (RunPod env vars not set)")
    _stub_provider(monkeypatch, provider_name, "qwen4 answered")

    result = agent_roles.general_reasoning_agent("Critique this proposal")

    assert result["provider"] == provider_name
    assert result["task_type"] == "review"


def test_general_reasoning_agent_falls_back_to_claude_when_primary_unavailable(monkeypatch):
    # gemini removed from "review" entirely 2026-08-02 -- claude (now last)
    # demonstrates the fallback instead.
    import core.ai_provider as ai_provider

    # gemini re-enabled 2026-08-02 (credit reloaded) and rejoined "review".
    # Only disable providers that are actually registered.
    to_disable = [n for n in ("deepseek_native_flash", "omniroute_deepseek_flash",
                    "deepseek", "gemini", "geminix", "qwen4_text", "qwen4_pod_b")
                  if ai_provider.get_provider(n) is not None]
    for name in to_disable:
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    _ensure_in_role_providers(monkeypatch, "claude", "review")
    _stub_provider(monkeypatch, "claude", "claude answered")

    result = agent_roles.general_reasoning_agent("Critique this proposal")

    assert result["provider"] == "claude"


def test_agent_role_kwargs_forward_to_delegate(monkeypatch):
    _stub_provider(monkeypatch, "groq", "groq answered")

    result = agent_roles.fast_analysis_agent("Triage this log", timeout=5)

    assert result["provider"] == "groq"
