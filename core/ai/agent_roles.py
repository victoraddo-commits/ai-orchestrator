"""Phase 13D: named semantic roles over ai_router.delegate().

This is not a parallel routing system -- each function below just pins a
task_type and forwards everything else straight through to delegate(),
which still owns all provider selection, fallback, and usage recording
(ROLE_PROVIDERS in ai_router.py). Only Claude has coding_agent capability
today (see ai_provider.py's module docstring), so "assigning a provider"
for actual code generation has no real choice to make yet -- callers doing
real code generation still pass capability="coding_agent" and a
project_path themselves, same as any other delegate() call. What this
module formalizes is the role names for the surrounding research/analysis/
review work that already routes through ai_router.
"""

from core.ai.ai_router import delegate


def architecture_agent(description, **kwargs):
    """Architecture Agent (Claude) -- design, structure, code generation."""
    return delegate(description, task_type="coding", **kwargs)


def research_agent(description, **kwargs):
    """Research Agent (Gemini) -- broad research, documentation analysis."""
    return delegate(description, task_type="planning", **kwargs)


def fast_analysis_agent(description, **kwargs):
    """Fast Analysis Agent (Groq) -- rapid triage, log/quick analysis."""
    return delegate(description, task_type="log_analysis", **kwargs)


def general_reasoning_agent(description, **kwargs):
    """General Reasoning Agent (OpenAI) -- general logic, critique, review."""
    return delegate(description, task_type="review", **kwargs)
