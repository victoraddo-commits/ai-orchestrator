import core.ai.agent_roles as agent_roles


def _stub_provider(monkeypatch, name, response):
    import core.ai_provider as ai_provider

    provider = ai_provider.get_provider(name)
    monkeypatch.setitem(provider, "available_fn", lambda: True)
    monkeypatch.setitem(provider, "run_text_task", lambda p, timeout=60, project_path=None: response)


def test_architecture_agent_routes_to_claude(monkeypatch):
    _stub_provider(monkeypatch, "claude", "claude answered")

    result = agent_roles.architecture_agent("Design the new module")

    assert result["provider"] == "claude"
    assert result["task_type"] == "coding"


def test_research_agent_routes_to_gemini(monkeypatch):
    _stub_provider(monkeypatch, "gemini", "gemini answered")

    result = agent_roles.research_agent("Summarize the docs")

    assert result["provider"] == "gemini"
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


def test_general_reasoning_agent_falls_back_to_gemini_when_openai_unavailable(monkeypatch):
    import core.ai_provider as ai_provider

    monkeypatch.setitem(ai_provider.get_provider("openai"), "available_fn", lambda: False)
    _stub_provider(monkeypatch, "gemini", "gemini answered")

    result = agent_roles.general_reasoning_agent("Critique this proposal")

    assert result["provider"] == "gemini"


def test_agent_role_kwargs_forward_to_delegate(monkeypatch):
    _stub_provider(monkeypatch, "groq", "groq answered")

    result = agent_roles.fast_analysis_agent("Triage this log", timeout=5)

    assert result["provider"] == "groq"
