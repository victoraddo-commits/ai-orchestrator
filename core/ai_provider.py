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

# Load .env file to ensure environment variables are available for provider checks
# This is critical for Qwen3 and other providers that depend on environment variables
try:
    from dotenv import load_dotenv
    # Load from ai-orchestrator root
    ai_orch_path = Path(__file__).parent.parent
    env_path = ai_orch_path / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    # dotenv not available, continue with existing environment
    pass

from core.coding_bridge import run_coding_task as _claude_run_coding_task
import core.coding_bridge as coding_bridge
import core.llm_clients as llm_clients
import core.ai.provider_health as provider_health
import core.opencode_bridge as opencode_bridge
from core.memory import update
from core.repo_manager import create_local_repo


OPENCODE_AUTH_PATH = Path.home() / ".local" / "share" / "opencode" / "auth.json"


_PROVIDERS = {}

# Persisted enabled/disabled state so toggles survive process restarts.
# Stored in the memory/ directory alongside other runtime state.
_PROVIDER_STATE_PATH = Path(__file__).parent.parent / "memory" / "provider_state.json"

def _load_provider_state():
    """Load persisted enabled/disabled overrides, if any."""
    try:
        if _PROVIDER_STATE_PATH.exists():
            return json.loads(_PROVIDER_STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        pass
    return {}

def _save_provider_state(state):
    """Atomically persist enabled/disabled state."""
    tmp = _PROVIDER_STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(_PROVIDER_STATE_PATH)

# 13W: static cost classification per provider -- assigned at registration,
# never computed. Valid values, per the user's own labeling request:
#   "free"             -- free-tier API keys (gemini, groq)
#   "free_or_low_cost" -- cheap/credit-pool models (minimax, deepseek, Zen's
#                         default routes)
#   "paid"             -- real per-call billing against a paid account
#                         (openai, claude subscription, openrouter) or
#                         materially expensive models (Sonnet/Opus escalation
#                         tiers -- Fable 5 was chosen for the default Zen
#                         route precisely because it's the cheap one).
COST_TIERS = ("free", "free_or_low_cost", "paid")

# Ad-hoc text-only Claude calls (e.g. router-delegated planning/review tasks
# with no build attached) need *some* real directory for CloudCLI's
# /api/agent to operate against -- this one is created once, lazily, and
# reused rather than requiring every caller to supply a project.
_SCRATCH_WORKSPACE = os.path.join(os.path.expanduser("~"), ".ai-orchestrator", "text-task-scratch")


def register_provider(name, run_coding_task=None, run_text_task=None, available_fn=None, kind="cloud", description="", cost_tier="free_or_low_cost"):
    if cost_tier not in COST_TIERS:
        raise ValueError(f"cost_tier must be one of {COST_TIERS}, got {cost_tier!r}")

    capabilities = []
    if run_coding_task is not None:
        capabilities.append("coding_agent")
        # 17R: file-access-aware routing -- coding agents inherently have
        # filesystem access; text_task providers do not. This is registered
        # as an explicit capability so delegate() can filter/deprioritize on
        # requires_file_access without guessing from the run_* function shape.
        capabilities.append("file_access")
    if run_text_task is not None:
        capabilities.append("text_task")

    _persisted = _load_provider_state()

    _PROVIDERS[name] = {
        "run_coding_task": run_coding_task,
        "run_text_task": run_text_task,
        "available_fn": available_fn,
        "kind": kind,
        "description": description,
        "capabilities": capabilities,
        "cost_tier": cost_tier,
        "enabled": _persisted.get(name, True),  # default on unless explicitly disabled
    }


def get_provider(name):
    return _PROVIDERS.get(name)


def list_providers():
    return {
        name: {
            "kind": entry["kind"],
            "description": entry["description"],
            "available": bool(entry["available_fn"]()),
            "enabled": entry.get("enabled", True),
            "capabilities": entry["capabilities"],
            "cost_tier": entry["cost_tier"],
        }
        for name, entry in _PROVIDERS.items()
    }


def deregister_provider(name: str) -> bool:
    """Remove a provider from the registry and persisted state.

    Returns True if the provider was found and removed, False if not found.
    This only removes from the registry -- callers must also remove the
    provider from ai_router.ROLE_PROVIDERS and any other routing structures.
    """
    entry = _PROVIDERS.pop(name, None)
    if entry is None:
        return False
    state = _load_provider_state()
    state.pop(name, None)
    _save_provider_state(state)
    return True


def set_provider_enabled(name: str, enabled: bool) -> bool:
    """Toggle a provider on or off.  Persisted to memory/provider_state.json
    so the setting survives process restarts.  Returns True if the provider
    exists, False if not found."""
    entry = _PROVIDERS.get(name)
    if entry is None:
        return False
    entry["enabled"] = enabled
    state = _load_provider_state()
    state[name] = enabled
    _save_provider_state(state)
    return True


def get_provider_enabled(name: str) -> bool:
    """Check if a provider is enabled (default True for all providers)."""
    entry = _PROVIDERS.get(name)
    return entry.get("enabled", True) if entry else False


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

    # Confirmed live 2026-08-01: a long/complex prompt got success=True back
    # from the underlying coding_bridge run with an EMPTY response_text --
    # not a real answer, but not flagged as a failure either, so delegate()
    # had no reason to fall through to the next candidate and a caller got
    # silently handed "". Treat that the same as any other failure so the
    # router's fallback logic actually engages instead of returning nothing.
    empty_response = not (result.get("response_text") or "").strip()

    if not result.get("success") or empty_response:
        # Surface whatever actually went wrong verbatim -- this is where a
        # real "usage limit reached" message would show up, but we don't
        # pattern-match for that specific wording since it's never been
        # observed/verified from this account; any failure gets recorded
        # and re-raised so the router's fallback logic engages.
        errors = result.get("tool_errors") or []
        detail = "; ".join(e.get("content", "") for e in errors) or "coding_bridge run did not succeed"
        if empty_response and result.get("success"):
            detail = f"succeeded but returned an empty response_text ({detail})"
        provider_health.capture_provider_error("claude", detail=detail)
        raise RuntimeError(f"Claude text task failed: {detail}")

    return result.get("response_text", "")


def _opencode_credential_available(key):
    """True when the opencode CLI is on PATH and its own credential store
    (~/.local/share/opencode/auth.json) holds an entry under `key` -- e.g.
    "opencode" for the OpenCode Zen account, "openrouter" for the OpenRouter
    account (see scripts/setup_openrouter_opencode_auth.py)."""
    if shutil.which("opencode") is None:
        return False

    try:
        auth = json.loads(OPENCODE_AUTH_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return False

    return key in auth


def _opencode_available():
    return _opencode_credential_available("opencode")


def _opencode_run_coding_task(project_path, instruction, **kwargs):
    return opencode_bridge.run_coding_task(project_path, instruction, **kwargs)


# Billed through OpenCode Zen's own account, entirely separate from the
# CloudCLI/Anthropic subscription -- confirmed useful live: that subscription
# hit its weekly limit tonight, but Zen's Claude access has its own quota
# pool and would have kept working. Same availability check as "opencode"
# (same CLI, same Zen credential) since model choice doesn't affect that.
# Fable 5 chosen deliberately over Sonnet 5 to maximize the Zen account's
# usage headroom (per-account credit, not shared with the CloudCLI
# subscription) while still staying on a real Claude model for this route.
OPENCODE_CLAUDE_MODEL = "opencode/claude-fable-5"


def _opencode_claude_run_coding_task(project_path, instruction, **kwargs):
    kwargs.setdefault("model", OPENCODE_CLAUDE_MODEL)
    return opencode_bridge.run_coding_task(project_path, instruction, **kwargs)


# 2026-08-02 operator directive: give Fable 5 a text_task route too (Q&A --
# e.g. Kai's operator chat, task_type="planning") alongside its existing
# coding_agent one, same "text only, don't touch files" wrapper pattern as
# _claude_run_text_task above (reuses the coding-task path with an
# instruction prefix + the same scratch workspace, since opencode has no
# separate non-agentic text endpoint). NOT wired into any approval flow --
# approvals stay human-only (see core.build_manager.approve_architecture/
# approve_deploy and tests/test_kai_identity.py's structural guarantee that
# nothing under core/kai/ calls them); "answer questions" is the entire
# scope of this route.
def _opencode_claude_run_text_task(prompt, timeout=60, project_path=None):
    if project_path is None:
        create_local_repo(_SCRATCH_WORKSPACE)
        project_path = _SCRATCH_WORKSPACE

    instruction = (
        "Answer the following as text only. Do NOT write, edit, or modify "
        "any files, and do not run commands that change anything.\n\n"
        f"{prompt}"
    )
    result = _opencode_claude_run_coding_task(project_path, instruction, timeout=timeout)

    if not result.get("success"):
        errors = result.get("tool_errors") or []
        detail = "; ".join(e.get("content", "") for e in errors) or "opencode run did not succeed"
        raise RuntimeError(f"opencode_claude text task failed: {detail}")

    return result.get("response_text", "")


# Escalation tier above Fable 5: same Zen credential/billing, confirmed live
# via `opencode models` to be valid model strings (2026-07-29, user directive
# after Claude's weekly subscription limit hit mid-session). Fable 5 stays
# the first Zen attempt (cheapest, maximizes account headroom per the
# existing directive above) -- these are for when Fable 5 itself is also
# unavailable/failing, before falling all the way to a non-Claude model
# (plain "opencode"/DeepSeek). See ai.ai_router.ROLE_PROVIDERS["coding"]
# for the actual fallback order.
OPENCODE_CLAUDE_SONNET_MODEL = "opencode/claude-sonnet-5"
OPENCODE_CLAUDE_OPUS_MODEL = "opencode/claude-opus-5"


def _opencode_claude_sonnet_run_coding_task(project_path, instruction, **kwargs):
    kwargs.setdefault("model", OPENCODE_CLAUDE_SONNET_MODEL)
    return opencode_bridge.run_coding_task(project_path, instruction, **kwargs)


def _opencode_claude_opus_run_coding_task(project_path, instruction, **kwargs):
    kwargs.setdefault("model", OPENCODE_CLAUDE_OPUS_MODEL)
    return opencode_bridge.run_coding_task(project_path, instruction, **kwargs)


# 13T: minimax-m2.7 pinned as its own routable coding-agent provider rather
# than left as an implicit historical default of "opencode". Two reasons it
# has to be a separate registry entry:
#
#   * "opencode" now means deepseek-v4-pro (2026-07-29), so routing to
#     minimax at all requires an explicit model pin.
#   * every usage-history entry is keyed by *provider*, not model -- the
#     "opencode" aggregate is already a blend of the two models, and 13T had
#     to reconstruct which was which from the opencode CLI session store to
#     get real per-model counts. A distinct name means the next review reads
#     minimax's coding-agent record straight out of
#     core.ai.provider_evidence with no external attribution step.
#
# The evidence for having this route at all (see ai.ai_router.ROLE_PROVIDERS
# ["coding"] and the 13T lesson records): 3/3 recorded coding_agent runs,
# zero hallucinated-tool-call/timeout/tool-error events -- unlike the
# tools-less text_task path, which is 0/4 usable and stays unrouted.
OPENCODE_MINIMAX_MODEL = "opencode/minimax-m2.7"


def _opencode_minimax_run_coding_task(project_path, instruction, **kwargs):
    kwargs.setdefault("model", OPENCODE_MINIMAX_MODEL)
    return opencode_bridge.run_coding_task(project_path, instruction, **kwargs)


# 17S: Dedicated per-model OpenCode Zen keys (2026-08-08).
# Each key is scoped to exactly one model on the platform side, with its own
# isolated XDG_DATA_HOME/auth.json so the shared 'opencode' credential's other
# models are never reachable through these routes (operator directive).
OPENCODE_FABLE5_MODEL = "opencode/claude-fable-5"
OPENCODE_GEMINI_PRO_MODEL = "opencode/gemini-3.1-pro"


def _opencode_fable5_run_coding_task(project_path, instruction, **kwargs):
    """Dedicated CLAUDE_FABLE5_OPENCODE_ZEN_API_KEY — isolated auth."""
    return opencode_bridge.run_opencode_fable5(project_path, instruction, **kwargs)


def _opencode_fable5_run_text_task(prompt, timeout=60, project_path=None):
    """Fable 5 text-task route via dedicated key. Same 'text only' wrapper as
    _opencode_claude_run_text_task but with isolated auth."""
    from core.build_manager import create_local_repo
    if project_path is None:
        create_local_repo(_SCRATCH_WORKSPACE)
        project_path = _SCRATCH_WORKSPACE
    instruction = (
        "Answer the following as text only. Do NOT write, edit, or modify "
        "any files, and do not run commands that change anything.\n\n"
        f"{prompt}"
    )
    result = _opencode_fable5_run_coding_task(project_path, instruction, timeout=timeout)
    if not result.get("success"):
        errors = result.get("tool_errors") or []
        detail = "; ".join(e.get("content", "") for e in errors) or "opencode run did not succeed"
        raise RuntimeError(f"opencode_fable5 text task failed: {detail}")
    return result.get("response_text", "")


def _opencode_gemini_pro_run_text_task(prompt, timeout=60, project_path=None):
    """Dedicated GEMINI_3_1_PRO_OPENCODE_ZEN_API_KEY — isolated auth.
    Text-only route: Gemini 3.1 Pro is a reasoning model, not a coding agent."""
    from core.build_manager import create_local_repo
    if project_path is None:
        create_local_repo(_SCRATCH_WORKSPACE)
        project_path = _SCRATCH_WORKSPACE
    instruction = (
        "Answer the following as text only. Do NOT write, edit, or modify "
        "any files, and do not run commands that change anything.\n\n"
        f"{prompt}"
    )
    result = opencode_bridge.run_opencode_gemini_pro(project_path, instruction, timeout=timeout)
    if not result.get("success"):
        errors = result.get("tool_errors") or []
        detail = "; ".join(e.get("content", "") for e in errors) or "opencode run did not succeed"
        raise RuntimeError(f"opencode_gemini_pro text task failed: {detail}")
    return result.get("response_text", "")


# 2026-08-03 operator directive: OmniRoute (localhost:20128) as Kai's
# always-on fallback provider. The gateway exposes OpenAI-compatible /v1
# endpoints with auto/ routes that pick the best upstream per request, so
# coding through the opencode CLI (--model omniroute/auto/best-coding) and
# text through llm_clients.call_omniroute both survive any single upstream's
# outage/credit state. See llm_clients.OMNIROUTE_* and
# ~/.config/opencode/opencode.jsonc's "omniroute" provider block.
OMNIROUTE_CODING_MODEL = llm_clients.OMNIROUTE_CODING_MODEL


def _omniroute_run_coding_task(project_path, instruction, **kwargs):
    kwargs.setdefault("model", f"omniroute/{OMNIROUTE_CODING_MODEL}")
    return opencode_bridge.run_coding_task(project_path, instruction, **kwargs)


def _omniroute_run_text_task(prompt, timeout=60, project_path=None):
    return llm_clients.call_omniroute(prompt, model=llm_clients.OMNIROUTE_TEXT_MODEL, timeout=timeout)


def _omniroute_available():
    return llm_clients._omniroute_key() is not None



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


# 2026-08-02: second Gemini account ("GeminiX") -- see llm_clients.call_geminix.
def _geminix_run_text_task(prompt, timeout=60, project_path=None):
    return llm_clients.call_geminix(prompt, timeout=timeout)


def _groq_run_text_task(prompt, timeout=60, project_path=None):
    return llm_clients.call_groq(prompt, timeout=timeout)


# 13M: the plain-text openrouter provider rotates across
# llm_clients.OPENROUTER_MODELS instead of always using the single
# OPENROUTER_DEFAULT_MODEL (which remains the default for direct
# call_openrouter callers). Same rotation-state shape as ai_router's
# provider_rotation.json, keyed by a fixed "index" since this provider has
# no per-role model split. The read-increment-write goes through
# core.memory.update()'s flock critical section (same reasoning as
# ai_router._rotate_candidates, 13R): concurrent delegate() calls must not
# read the same index and land on the same model.
OPENROUTER_MODEL_ROTATION_FILE = "openrouter_model_rotation.json"


def _next_openrouter_model():
    models = llm_clients.OPENROUTER_MODELS
    captured = {}

    def mutate(state):
        state = state if isinstance(state, dict) else {}
        start = state.get("index", 0) % len(models)
        captured["start"] = start
        state["index"] = (start + 1) % len(models)
        return state

    update(OPENROUTER_MODEL_ROTATION_FILE, mutate)

    return models[captured["start"]]


def _openrouter_run_text_task(prompt, timeout=60, project_path=None):
    return llm_clients.call_openrouter(prompt, model=_next_openrouter_model(), timeout=timeout)


def _openrouter_claude_run_text_task(prompt, timeout=60, project_path=None):
    return llm_clients.call_openrouter_claude(prompt, timeout=timeout)


def _minimax_run_text_task(prompt, timeout=60, project_path=None):
    return llm_clients.call_minimax(prompt, timeout=timeout)


def _deepseek_run_text_task(prompt, timeout=60, project_path=None):
    return llm_clients.call_deepseek(prompt, timeout=timeout)


register_provider(
    "claude",
    run_coding_task=_claude_run_coding_task,
    run_text_task=_claude_run_text_task,
    available_fn=_claude_available,
    kind="cloud",
    description="Claude Agent SDK via CloudCLI's /api/agent (Phase 12B) -- senior engineer: coding, implementation, hard debugging",
    cost_tier="paid",
)

register_provider(
    "gemini",
    run_text_task=_gemini_run_text_task,
    available_fn=lambda: bool(os.getenv("GEMINI_API_KEY")),
    kind="cloud",
    description="Google Gemini -- planning, architecture review, documentation",
    cost_tier="free",
)

register_provider(
    "geminix",
    run_text_task=_geminix_run_text_task,
    available_fn=lambda: bool(os.getenv("GEMINIX_API_KEY")),
    kind="cloud",
    description="Google Gemini, second account (GeminiX) -- immediate fallback right after 'gemini' in every role it serves",
    cost_tier="free",
)

register_provider(
    "groq",
    run_text_task=_groq_run_text_task,
    available_fn=lambda: bool(os.getenv("GROQ_API_KEY")),
    kind="cloud",
    description="Groq -- fast log/quick analysis, simple tasks",
    cost_tier="free",
)

register_provider(
    "openrouter",
    run_text_task=_openrouter_run_text_task,
    available_fn=lambda: bool(os.getenv("OPENROUTER_API_KEY")),
    kind="cloud",
    description="OpenRouter (rotates llm_clients.OPENROUTER_MODELS, deepseek-v4-flash first) -- planning/research fallback, reduces Claude-credit usage",
    cost_tier="paid",
)

# 13V: text-capable Claude Sonnet route via OpenRouter, for the Chief
# Architect fallback chain (see ai.ai_router.ROLE_PROVIDERS["architecture"]).
# Same OPENROUTER_API_KEY as "openrouter" but a distinct provider key, so its
# health/quota snapshots and usage history don't blend with the gpt-4o-mini
# route. This is the 13V-approved "reuse the plain openrouter provider pinned
# to anthropic/claude-sonnet-4.6" option -- 13M's opencode-CLI coding route
# can still land separately later.
register_provider(
    "openrouter_claude",
    run_text_task=_openrouter_claude_run_text_task,
    available_fn=lambda: bool(os.getenv("OPENROUTER_API_KEY")),
    kind="cloud",
    description="Claude Sonnet 4.6 (anthropic/claude-sonnet-4.6 via OpenRouter) -- Chief Architect chain fallback, Claude-family answers surviving the Anthropic subscription's quota",
    cost_tier="paid",
)

register_provider(
    "minimax",
    run_text_task=_minimax_run_text_task,
    available_fn=lambda: bool(os.getenv("MINIMAX_API_KEY")),
    kind="cloud",
    description="MiniMax-M2 (tools-less chat completion) -- registered but deliberately unrouted: 13T's usage review found 0/4 usable text_task outputs. Its coding-agent counterpart is 'opencode_minimax'",
    cost_tier="free_or_low_cost",
)

register_provider(
    "deepseek",
    run_text_task=_deepseek_run_text_task,
    available_fn=lambda: bool(os.getenv("DEEPSEEK_OPENROUTER_API_KEY")),
    kind="cloud",
    description="DeepSeek V4 Pro via OpenRouter (dedicated DEEPSEEK_OPENROUTER_API_KEY) -- planning/documentation/review",
    cost_tier="free_or_low_cost",
)


def _deepseek_native_pro_run_text_task(prompt, timeout=60, project_path=None):
    return llm_clients.call_deepseek_native_pro(prompt, timeout=timeout)


def _deepseek_native_flash_run_text_task(prompt, timeout=60, project_path=None):
    return llm_clients.call_deepseek_native_flash(prompt, timeout=timeout)


# 17R item 1: direct api.deepseek.com calls (not proxied through OpenRouter
# or OpenCode Zen), so these have no shared-quota exposure to any OpenRouter
# key or the Zen account -- confirmed live 2026-08-02 while every OpenRouter-
# routed candidate (openrouter, deepseek, both Claude-family OpenRouter
# routes) and Gemini were simultaneously credit/quota-exhausted.
register_provider(
    "deepseek_native_pro",
    run_text_task=_deepseek_native_pro_run_text_task,
    available_fn=lambda: bool(os.getenv("DEEPSEEK_NATIVE_PRO_API_KEY")),
    kind="cloud",
    description="DeepSeek V4 Pro via native api.deepseek.com (DEEPSEEK_NATIVE_PRO_API_KEY) -- no OpenRouter/Zen quota exposure",
    cost_tier="free_or_low_cost",
)

register_provider(
    "deepseek_native_flash",
    run_text_task=_deepseek_native_flash_run_text_task,
    available_fn=lambda: bool(os.getenv("DEEPSEEK_NATIVE_FLASH_API_KEY")),
    kind="cloud",
    description="DeepSeek V4 Flash via native api.deepseek.com (DEEPSEEK_NATIVE_FLASH_API_KEY) -- fast, no OpenRouter/Zen quota exposure",
    cost_tier="free_or_low_cost",
)


register_provider(
    "opencode",
    run_coding_task=_opencode_run_coding_task,
    available_fn=_opencode_available,
    kind="cloud",
    description="OpenCode (DeepSeek V4 Pro via OpenCode Zen by default, was MiniMax-m2.7 until 2026-07-29) -- sandboxed coding agent, second code-writing worker alongside Claude",
    cost_tier="free_or_low_cost",
)

register_provider(
    "opencode_claude",
    run_coding_task=_opencode_claude_run_coding_task,
    run_text_task=_opencode_claude_run_text_task,
    available_fn=_opencode_available,
    kind="cloud",
    description="Claude Fable 5 (opencode/claude-fable-5 via OpenCode Zen) -- billed separately from the CloudCLI/Anthropic subscription, survives that subscription's own outages/quota limits; also handles Q&A (text_task) since 2026-08-02",
    cost_tier="free_or_low_cost",
)

register_provider(
    "opencode_claude_sonnet",
    run_coding_task=_opencode_claude_sonnet_run_coding_task,
    available_fn=_opencode_available,
    kind="cloud",
    description="Claude Sonnet 5 (opencode/claude-sonnet-5 via OpenCode Zen) -- escalation above Fable 5 when it's also unavailable/failing, same separate Zen billing",
    cost_tier="paid",
)

register_provider(
    "opencode_claude_opus",
    run_coding_task=_opencode_claude_opus_run_coding_task,
    available_fn=_opencode_available,
    kind="cloud",
    description="Claude Opus 5 (opencode/claude-opus-5 via OpenCode Zen) -- top escalation tier above Fable 5/Sonnet 5, same separate Zen billing",
    cost_tier="paid",
)

register_provider(
    "opencode_minimax",
    run_coding_task=_opencode_minimax_run_coding_task,
    available_fn=_opencode_available,
    kind="cloud",
    description="MiniMax-m2.7 (opencode/minimax-m2.7 via OpenCode Zen) -- coding_agent only, restored 2026-07-29 by 13T's usage review: 3/3 recorded runs through opencode CLI's real tool-use loop, none of the text_task path's hallucinated tool-call failures",
    cost_tier="free_or_low_cost",
)

# 17S: Dedicated per-model OpenCode Zen providers (2026-08-08).
# Each has its own isolated auth.json via XDG_DATA_HOME, cleanly separating
# the shared 'opencode' credential's multi-model slot from these single-model
# keys. Per operator directive: highest priority for eligible roles.
register_provider(
    "opencode_fable5",
    run_coding_task=_opencode_fable5_run_coding_task,
    run_text_task=_opencode_fable5_run_text_task,
    available_fn=_opencode_available,
    kind="cloud",
    description="Claude Fable 5 via dedicated OpenCode Zen key (CLAUDE_FABLE5_OPENCODE_ZEN_API_KEY) -- isolated from shared 'opencode' credential, highest priority per operator directive 2026-08-02",
    cost_tier="paid",
)

register_provider(
    "opencode_gemini_pro",
    run_text_task=_opencode_gemini_pro_run_text_task,
    available_fn=_opencode_available,
    kind="cloud",
    description="Gemini 3.1 Pro via dedicated OpenCode Zen key (GEMINI_3_1_PRO_OPENCODE_ZEN_API_KEY) -- isolated from shared 'opencode' credential, text-only route per operator directive, highest priority for text roles",
    cost_tier="paid",
)


register_provider(
    "omniroute",
    run_coding_task=_omniroute_run_coding_task,
    run_text_task=_omniroute_run_text_task,
    available_fn=_omniroute_available,
    kind="cloud",
    description="OmniRoute self-hosted AI gateway (localhost:20128, v16.2.12) -- always-on fallback aggregating multiple upstreams behind auto/ routes; added 2026-08-03 per operator directive so Kai keeps working when individual providers run out of credit",
    cost_tier="free_or_low_cost",
)


def _omniroute_deepseek_flash_run_text_task(prompt, timeout=60, project_path=None):
    return llm_clients.call_omniroute_deepseek_flash(prompt, timeout=timeout)


register_provider(
    "omniroute_deepseek_flash",
    run_text_task=_omniroute_deepseek_flash_run_text_task,
    available_fn=_omniroute_available,
    kind="cloud",
    description="DeepSeek V4 Flash via OmniRoute (ds/deepseek-v4-flash) — dedicated operator-keyed route through self-hosted gateway, verified live 2026-08-06",
    cost_tier="free_or_low_cost",
)



# GPU.ai Gemma 4 31B IT — vision-capable, OpenAI-compatible.
# Hosted on gpu.ai with multimodal support: image understanding, OCR, document
# analysis, table extraction, UI screenshots, and all visual capabilities of
# Gemma 4's capability registry.  Uses stored secrets (core.ai.secrets) rather
# than env vars, same as the other post-17Z providers.
GPUAI_GEMMA_MODEL = "gpuai/gemma-4-31b-it"


def _gpuai_gemma_run_text_task(prompt, timeout=120, project_path=None):
    return llm_clients.call_gpuai_gemma(prompt, timeout=timeout)


def _gpuai_gemma_available():
    try:
        from core.ai.secrets import get_api_key
        return get_api_key("gpuai") is not None
    except ImportError:
        return False


register_provider(
    "gpuai_gemma",
    run_text_task=_gpuai_gemma_run_text_task,
    available_fn=_gpuai_gemma_available,
    kind="cloud",
    description="Gemma 4 31B IT via GPU.ai — vision-capable: OCR, document analysis, image understanding, table extraction, UI screenshots",
    cost_tier="paid",  # $0.35/hr GPU rental, pay-per-use
)


# 2026-08-09: Dedicated DeepSeek coding agent via OmniRoute gateway.
# Per operator directive: "use deepseek" — routes through OmniRoute's
# auto/best-coding slot which prefers DeepSeek models. This is Kai's
# PRIMARY coding agent, backed by the same opencode CLI sandbox all
# coding agents use, so DeepSeek powers the build/roadmap pipeline.
OMNIROUTE_DEEPSEEK_CODING_MODEL = "auto/best-coding"


def _omniroute_deepseek_coding_run_task(project_path, instruction, **kwargs):
    kwargs.setdefault("model", f"omniroute/{OMNIROUTE_DEEPSEEK_CODING_MODEL}")
    return opencode_bridge.run_coding_task(project_path, instruction, **kwargs)


register_provider(
    "omniroute_deepseek_coding",
    run_coding_task=_omniroute_deepseek_coding_run_task,
    available_fn=_omniroute_available,
    kind="cloud",
    description="DeepSeek via OmniRoute gateway (auto/best-coding) — PRIMARY coding agent per operator directive 2026-08-09, routes through self-hosted AI gateway",
    cost_tier="free_or_low_cost",
)


register_provider(
    "local",
    run_coding_task=_local_not_implemented,
    available_fn=lambda: False,
    kind="local",
    description="Extension point for a future local model (e.g. Ollama) -- not installed",
    cost_tier="free",
)

