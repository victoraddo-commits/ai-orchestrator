"""AI coding-provider registry -- Phase 12I: architecture prep only.

This defines the extension point Phase 12J's router will actually use to
pick between providers; it does not do any routing itself, and it
installs nothing. Every provider entry must expose the same contract
core.coding_bridge.run_coding_task already has (project_path, instruction,
session_id=None, model=None, timeout=300) -> dict, so callers never need to
know which provider they're talking to.
"""

from core.coding_bridge import run_coding_task as _claude_run_coding_task
import core.coding_bridge as coding_bridge


_PROVIDERS = {}


def register_provider(name, run_coding_task, available_fn, kind, description):
    _PROVIDERS[name] = {
        "run_coding_task": run_coding_task,
        "available_fn": available_fn,
        "kind": kind,
        "description": description,
    }


def get_provider(name):
    return _PROVIDERS.get(name)


def list_providers():
    return {
        name: {
            "kind": entry["kind"],
            "description": entry["description"],
            "available": bool(entry["available_fn"]()),
        }
        for name, entry in _PROVIDERS.items()
    }


def _claude_available():
    return coding_bridge.API_KEY_PATH.exists()


def _local_not_implemented(*args, **kwargs):
    raise NotImplementedError(
        "The local provider is a Phase 12I architecture placeholder -- no "
        "local model is installed or implemented. See Phase 12J/12I in the "
        "roadmap before wiring one in."
    )


register_provider(
    "claude",
    run_coding_task=_claude_run_coding_task,
    available_fn=_claude_available,
    kind="cloud",
    description="Claude Agent SDK via CloudCLI's /api/agent (Phase 12B)",
)

register_provider(
    "local",
    run_coding_task=_local_not_implemented,
    available_fn=lambda: False,
    kind="local",
    description="Extension point for a future local model (e.g. Ollama) -- not installed",
)
