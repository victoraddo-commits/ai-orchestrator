import core.ai.agent_roles as agent_roles


def _stub_provider(monkeypatch, name, response):
    import core.ai_provider as ai_provider

    provider = ai_provider.get_provider(name)
    assert provider is not None, f"provider {name!r} is not registered"
    monkeypatch.setitem(provider, "available_fn", lambda: True)
    monkeypatch.setitem(provider, "enabled", True)
    monkeypatch.setitem(provider, "run_text_task", lambda p, timeout=60, project_path=None: response)


def _disable_provider(monkeypatch, name):
    import core.ai_provider as ai_provider

    provider = ai_provider.get_provider(name)
    if provider is not None:
        monkeypatch.setitem(provider, "available_fn", lambda: False)


# 2026-09-10 KAI MODEL TEAM directive: all roles route to local Ollama
# providers. The role functions below pin a task_type and forward to
# ai_router.delegate(), so each test stubs the local provider that is the
# primary for that role and asserts routing reaches it.

def test_architecture_agent_routes_to_kai_coder(monkeypatch):
    # architecture_agent -> task_type="coding" -> kai_coder (k.coder.fast)
    _stub_provider(monkeypatch, "kai_coder", "kai_coder answered")

    result = agent_roles.architecture_agent("Design the new module")

    assert result["provider"] == "kai_coder"
    assert result["task_type"] == "coding"


def test_research_agent_routes_to_kai_brain(monkeypatch):
    # research_agent -> task_type="planning" -> kai_brain (k.brain)
    _stub_provider(monkeypatch, "kai_brain", "kai_brain answered")

    result = agent_roles.research_agent("Summarize the docs")

    assert result["provider"] == "kai_brain"
    assert result["task_type"] == "planning"


def test_fast_analysis_agent_routes_to_local(monkeypatch):
    # fast_analysis_agent -> task_type="log_analysis" -> local (qwen2.5:7b)
    _stub_provider(monkeypatch, "local", "local answered")

    result = agent_roles.fast_analysis_agent("Triage this log")

    assert result["provider"] == "local"
    assert result["task_type"] == "log_analysis"


def test_general_reasoning_agent_routes_to_kai_brain(monkeypatch):
    # general_reasoning_agent -> task_type="review" -> kai_brain
    _stub_provider(monkeypatch, "kai_brain", "kai_brain answered")

    result = agent_roles.general_reasoning_agent("Critique this proposal")

    assert result["provider"] == "kai_brain"
    assert result["task_type"] == "review"


def test_general_reasoning_agent_falls_back_to_local_when_kai_brain_unavailable(monkeypatch):
    # review chain is ["kai_brain", "local"] -- disable the primary so local
    # (the universal local fallback) is the only candidate left standing.
    _disable_provider(monkeypatch, "kai_brain")
    _stub_provider(monkeypatch, "local", "local answered")

    result = agent_roles.general_reasoning_agent("Critique this proposal")

    assert result["provider"] == "local"


def test_agent_role_kwargs_forward_to_delegate(monkeypatch):
    _stub_provider(monkeypatch, "local", "local answered")

    result = agent_roles.fast_analysis_agent("Triage this log", timeout=5)

    assert result["provider"] == "local"
