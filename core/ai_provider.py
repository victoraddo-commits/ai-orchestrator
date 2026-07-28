"""AI provider registry.

Phase 12I built this as architecture prep with two placeholder-ish entries
(claude, local). Phase 12J fills in real text-task providers (gemini, groq,
openai) -- plain request/response chat-completion calls, never file access
or tool use, so this still doesn't duplicate CloudCLI's coding engine. Only
Claude has "coding_agent" capability.
"""

import os
import shutil
import json
from pathlib import Path

from core.coding_bridge import run_coding_task as _claude_run_coding_task
import core.coding_bridge as coding_bridge
import core.llm_clients as llm_clients
import core.ai.provider_health as provider_health
import core.opencode_bridge as opencode_bridge
from core.repo_manager import create_local_repo


OPENCODE_AUTH_PATH = Path.home() / ".local" / "share" / "opencode" / "auth.json"


_PROVIDERS = {}

# Ad-hoc text-only Claude calls (e.g. router-delegated planning/review tasks
# with no build attached) need *some* real directory for CloudCLI's
# /api/agent to operate against -- this one is created once, lazily, and
# reused rather than requiring every caller to supply a project.
_SCRATCH_WORKSPACE = os.path.join(os.path.expanduser("~"), ".ai-orchestrator", "text-task-scratch")


def register_provider(name, run_coding_task=None, run_text_task=None, available_fn=None, kind="cloud", description=""):
    capabilities = []
    if run_coding_task is not None:
        capabilities.append("coding_agent")
    if run_text_task is not None:
        capabilities.append("text_task")

    _PROVIDERS[name] = {
        "run_coding_task": run_coding_task,
        "run_text_task": run_text_task,
        "available_fn": available_fn,
        "kind": kind,
        "description": description,
        "capabilities": capabilities,
    }


def get_provider(name):
    return _PROVIDERS.get(name)


def list_providers():
    return {
        name: {
            "kind": entry["kind"],
            "description": entry["description"],
            "available": bool(entry["available_fn"]()),
            "capabilities": entry["capabilities"],
        }
        for name, entry in _PROVIDERS.items()
    }


def _claude_available():
    return coding_bridge.API_KEY_PATH.exists()


def _claude_run_text_task(prompt, timeout=60, project_path=None):
    if project_path is None:
        create_local_repo(_SCRATCH_WORKSPACE)
        project_path = _SCRATCH_WORKSPACE

    instruction = (
        "Answer the following as text only. Do NOT write, edit, or modify "
        "any files, and do not run commands that change anything.\n\n"
        f"{prompt}"
    )
    result = _claude_run_coding_task(project_path, instruction, timeout=timeout)

    if not result.get("success"):
        # Surface whatever actually went wrong verbatim -- this is where a
        # real "usage limit reached" message would show up, but we don't
        # pattern-match for that specific wording since it's never been
        # observed/verified from this account; any failure gets recorded
        # and re-raised so the router's fallback logic engages.
        errors = result.get("tool_errors") or []
        detail = "; ".join(e.get("content", "") for e in errors) or "coding_bridge run did not succeed"
        provider_health.capture_provider_error("claude", detail=detail)
        raise RuntimeError(f"Claude text task failed: {detail}")

    return result.get("response_text", "")


def _opencode_available():
    if shutil.which("opencode") is None:
        return False

    try:
        auth = json.loads(OPENCODE_AUTH_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return False

    return "opencode" in auth


def _opencode_run_coding_task(project_path, instruction, **kwargs):
    return opencode_bridge.run_coding_task(project_path, instruction, **kwargs)


# Billed through OpenCode Zen's own account, entirely separate from the
# CloudCLI/Anthropic subscription -- confirmed useful live: that subscription
# hit its weekly limit tonight, but Zen's Claude access has its own quota
# pool and would have kept working. Same availability check as "opencode"
# (same CLI, same Zen credential) since model choice doesn't affect that.
OPENCODE_CLAUDE_MODEL = "opencode/claude-sonnet-5"


def _opencode_claude_run_coding_task(project_path, instruction, **kwargs):
    kwargs.setdefault("model", OPENCODE_CLAUDE_MODEL)
    return opencode_bridge.run_coding_task(project_path, instruction, **kwargs)


def _local_not_implemented(*args, **kwargs):
    raise NotImplementedError(
        "The local provider is a Phase 12I architecture placeholder -- no "
        "local model is installed or implemented. See Phase 12J/12I in the "
        "roadmap before wiring one in."
    )


# Uniform run_text_task(prompt, timeout=60, project_path=None) contract
# across every provider -- project_path is accepted but ignored here since
# plain chat-completion providers never touch a filesystem. Each wrapper
# looks up its llm_clients function as a live module attribute (not a
# captured reference) so monkeypatching core.llm_clients.call_* in tests
# actually takes effect, same pattern as core.build_manager's imports.
def _gemini_run_text_task(prompt, timeout=60, project_path=None):
    return llm_clients.call_gemini(prompt, timeout=timeout)


def _groq_run_text_task(prompt, timeout=60, project_path=None):
    return llm_clients.call_groq(prompt, timeout=timeout)


def _openai_run_text_task(prompt, timeout=60, project_path=None):
    return llm_clients.call_openai(prompt, timeout=timeout)


def _openrouter_run_text_task(prompt, timeout=60, project_path=None):
    return llm_clients.call_openrouter(prompt, timeout=timeout)


def _minimax_run_text_task(prompt, timeout=60, project_path=None):
    return llm_clients.call_minimax(prompt, timeout=timeout)


register_provider(
    "claude",
    run_coding_task=_claude_run_coding_task,
    run_text_task=_claude_run_text_task,
    available_fn=_claude_available,
    kind="cloud",
    description="Claude Agent SDK via CloudCLI's /api/agent (Phase 12B) -- senior engineer: coding, implementation, hard debugging",
)

register_provider(
    "gemini",
    run_text_task=_gemini_run_text_task,
    available_fn=lambda: bool(os.getenv("GEMINI_API_KEY")),
    kind="cloud",
    description="Google Gemini -- planning, architecture review, documentation",
)

register_provider(
    "groq",
    run_text_task=_groq_run_text_task,
    available_fn=lambda: bool(os.getenv("GROQ_API_KEY")),
    kind="cloud",
    description="Groq -- fast log/quick analysis, simple tasks",
)

register_provider(
    "openai",
    run_text_task=_openai_run_text_task,
    available_fn=lambda: bool(os.getenv("OPENAI_API_KEY")),
    kind="cloud",
    description="OpenAI -- available, not currently assigned a primary role",
)

register_provider(
    "openrouter",
    run_text_task=_openrouter_run_text_task,
    available_fn=lambda: bool(os.getenv("OPENROUTER_API_KEY")),
    kind="cloud",
    description="OpenRouter (openai/gpt-4o-mini) -- planning/research fallback, reduces Claude-credit usage",
)

register_provider(
    "minimax",
    run_text_task=_minimax_run_text_task,
    available_fn=lambda: bool(os.getenv("MINIMAX_API_KEY")),
    kind="cloud",
    description="MiniMax-M2 -- planning/research fallback (account currently has no usable credits for this model)",
)

register_provider(
    "opencode",
    run_coding_task=_opencode_run_coding_task,
    available_fn=_opencode_available,
    kind="cloud",
    description="OpenCode (MiniMax-m2.7 via OpenCode Zen by default) -- sandboxed coding agent, second code-writing worker alongside Claude",
)

register_provider(
    "opencode_claude",
    run_coding_task=_opencode_claude_run_coding_task,
    available_fn=_opencode_available,
    kind="cloud",
    description="Claude (opencode/claude-sonnet-5 via OpenCode Zen) -- billed separately from the CloudCLI/Anthropic subscription, survives that subscription's own outages/quota limits",
)

register_provider(
    "local",
    run_coding_task=_local_not_implemented,
    available_fn=lambda: False,
    kind="local",
    description="Extension point for a future local model (e.g. Ollama) -- not installed",
)
