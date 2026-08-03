import core.ai.agent_roles as agent_roles


def _stub_provider(monkeypatch, name, response):
    import core.ai_provider as ai_provider

    provider = ai_provider.get_provider(name)
    monkeypatch.setitem(provider, "available_fn", lambda: True)
    monkeypatch.setitem(provider, "run_text_task", lambda p, timeout=60, project_path=None: response)


def test_architecture_agent_routes_to_claude(monkeypatch):
    # opencode_claude gained a real text_task route 2026-08-02 and leads
    # "coding" ahead of claude -- disabled so this test doesn't make a real
    # opencode/Zen call.
    import core.ai_provider as ai_provider
    monkeypatch.setitem(ai_provider.get_provider("opencode_claude"), "available_fn", lambda: False)

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
    for name in ("deepseek_native_flash", "openrouter", "deepseek", "opencode_claude", "deepseek_native_pro", "gemini", "geminix", "qwen3_coder_text"):
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    _stub_provider(monkeypatch, "claude", "claude answered")

    result = agent_roles.research_agent("Summarize the docs")

    assert result["provider"] == "claude"
    assert result["task_type"] == "planning"


def test_fast_analysis_agent_routes_to_groq(monkeypatch):
    _stub_provider(monkeypatch, "groq", "groq answered")

    result = agent_roles.fast_analysis_agent("Triage this log")

    assert result["provider"] == "groq"
    assert result["task_type"] == "log_analysis"


def test_general_reasoning_agent_routes_to_openai(monkeypatch):
    _stub_provider(monkeypatch, "openai", "openai answered")

    result = agent_roles.general_reasoning_agent("Critique this proposal")

    assert result["provider"] == "openai"
    assert result["task_type"] == "review"


def test_general_reasoning_agent_falls_back_to_claude_when_openai_unavailable(monkeypatch):
    # gemini removed from "review" entirely 2026-08-02 -- claude (now last)
    # demonstrates the fallback instead.
    import core.ai_provider as ai_provider

    monkeypatch.setitem(ai_provider.get_provider("openai"), "available_fn", lambda: False)
    # gemini re-enabled 2026-08-02 (credit reloaded) and rejoined "review".
    for name in ("deepseek_native_flash", "deepseek", "gemini", "geminix", "qwen3_coder_text"):
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    _stub_provider(monkeypatch, "claude", "claude answered")

    result = agent_roles.general_reasoning_agent("Critique this proposal")

    assert result["provider"] == "claude"


def test_agent_role_kwargs_forward_to_delegate(monkeypatch):
    _stub_provider(monkeypatch, "groq", "groq answered")

    result = agent_roles.fast_analysis_agent("Triage this log", timeout=5)

    assert result["provider"] == "groq"
