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


def test_architecture_agent_routes_to_a_local_provider(monkeypatch):
    # All earlier coding-chain locals are disabled by the autouse fixture;
    # append a stubbed local provider so the agent has one reachable worker.
    _ensure_in_role_providers(monkeypatch, "kai_deep", "coding")
    _stub_provider(monkeypatch, "kai_deep", "local answered")

    result = agent_roles.architecture_agent("Design the new module")

    assert result["provider"] == "kai_deep"
    assert result["task_type"] == "coding"


def test_research_agent_routes_to_a_local_provider(monkeypatch):
    # Local-only fabric: the autouse fixture disables the default planning
    # chain, so append a stubbed local provider and assert it is reached.
    _ensure_in_role_providers(monkeypatch, "kai_deep", "planning")
    _stub_provider(monkeypatch, "kai_deep", "local answered")

    result = agent_roles.research_agent("Summarize the docs")

    assert result["provider"] == "kai_deep"
    assert result["task_type"] == "planning"


def test_fast_analysis_agent_routes_to_groq(monkeypatch):
    import core.ai_provider as ai_provider
    # qwen4_text (was qwen3_coder_text) and deepseek now lead "classification"
    # — disable them so groq is the next candidate this test expects. Guard
    # against providers not being registered (RunPod env vars not set).
    qwen = ai_provider.get_provider("qwen4_text") or ai_provider.get_provider("qwen3_coder_text")
    if qwen is not None:
        monkeypatch.setitem(qwen, "available_fn", lambda: False)
    for ds in ("deepseek_native_flash", "deepseek_native_pro"):
        if ai_provider.get_provider(ds):
            monkeypatch.setitem(ai_provider.get_provider(ds), "available_fn", lambda: False)
    # local appears in provider config overrides — disable it too
    for extra in ("local",):
        if ai_provider.get_provider(extra) is not None:
            monkeypatch.setitem(ai_provider.get_provider(extra), "available_fn", lambda: False)
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


def test_general_reasoning_agent_falls_back_to_a_local_provider_when_primary_unavailable(monkeypatch):
    # Local-only fabric: with the default review chain disabled by the autouse
    # fixture, an appended stubbed local provider is the only one left standing.
    _ensure_in_role_providers(monkeypatch, "kai_deep", "review")
    _stub_provider(monkeypatch, "kai_deep", "local answered")

    result = agent_roles.general_reasoning_agent("Critique this proposal")

    assert result["provider"] == "kai_deep"


def test_agent_role_kwargs_forward_to_delegate(monkeypatch):
    import core.ai_provider as ai_provider
    # DeepSeek now leads classification — disable so groq's stub is reached.
    for ds in ("deepseek_native_flash", "deepseek_native_pro"):
        if ai_provider.get_provider(ds):
            monkeypatch.setitem(ai_provider.get_provider(ds), "available_fn", lambda: False)
    qwen = ai_provider.get_provider("qwen4_text") or ai_provider.get_provider("qwen3_coder_text")
    if qwen is not None:
        monkeypatch.setitem(qwen, "available_fn", lambda: False)
    monkeypatch.setitem(ai_provider.get_provider("groq"), "enabled", True)
    _stub_provider(monkeypatch, "groq", "groq answered")

    result = agent_roles.fast_analysis_agent("Triage this log", timeout=5)

    assert result["provider"] == "groq"
