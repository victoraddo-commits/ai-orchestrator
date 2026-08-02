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
from core.memory import update
from core.repo_manager import create_local_repo


OPENCODE_AUTH_PATH = Path.home() / ".local" / "share" / "opencode" / "auth.json"


_PROVIDERS = {}

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
    if run_text_task is not None:
        capabilities.append("text_task")

    _PROVIDERS[name] = {
        "run_coding_task": run_coding_task,
        "run_text_task": run_text_task,
        "available_fn": available_fn,
        "kind": kind,
        "description": description,
        "capabilities": capabilities,
        "cost_tier": cost_tier,
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
            "cost_tier": entry["cost_tier"],
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


# 13U: DeepSeek V4 Pro routed through OpenRouter via the opencode CLI --
# reuses the shared openrouter credential in the CLI's own auth.json (no
# Zen key involved, no collision risk with the existing opencode/
# opencode_claude routes that use the Zen credential's "opencode" slot).
# This is a separate coding route from the "deepseek" text_task provider
# (which uses DEEPSEEK_OPENROUTER_API_KEY directly, see
# llm_clients.call_deepseek).
OPENCODE_DEEPSEEK_MODEL = "openrouter/deepseek/deepseek-v4-pro"


def _opencode_deepseek_run_coding_task(project_path, instruction, **kwargs):
    kwargs.setdefault("model", OPENCODE_DEEPSEEK_MODEL)
    return opencode_bridge.run_coding_task(project_path, instruction, **kwargs)


# 2026-08-02 operator directive: self-hosted Qwen3-Coder-30B-A3B-Instruct-AWQ
# (RunPod, pay-per-use GPU) as a real coding_agent, not just the text_task
# route already wired on "openai" (core.llm_clients.call_openai). Driven
# through the opencode CLI via a custom provider entry (not the Zen/
# OpenRouter auth.json credential store -- see ~/.config/opencode/
# opencode.jsonc's "qwen3-runpod" provider, an @ai-sdk/openai-compatible
# pointed at VLLM_QWEN3_CODER_BASE_URL/API_KEY). Blocked until this date on
# a server-side gap: the vLLM instance wasn't launched with
# --enable-auto-tool-choice --tool-call-parser qwen3_coder, so opencode's
# tool-use loop 400'd on every attempt ("'auto' tool choice requires
# --enable-auto-tool-choice...") -- confirmed fixed live same day (file-write
# spike succeeded) once the pod's vllm serve command included those flags.
QWEN3_CODING_MODEL = "qwen3-runpod/QuantTrio/Qwen3-Coder-30B-A3B-Instruct-AWQ"


def _qwen3_coding_available():
    if shutil.which("opencode") is None:
        return False
    return bool(os.getenv("VLLM_QWEN3_CODER_API_KEY")) and bool(os.getenv("VLLM_QWEN3_CODER_BASE_URL"))


def _qwen3_coding_run_coding_task(project_path, instruction, **kwargs):
    kwargs.setdefault("model", QWEN3_CODING_MODEL)
    return opencode_bridge.run_coding_task(project_path, instruction, **kwargs)


# 13M: coding-capable Claude routes billed through the OpenRouter account --
# the opencode CLI drives OpenRouter-hosted models with its full agentic
# tool-use loop once an "openrouter" credential exists in its auth.json
# (confirmed live 2026-07-28; see scripts/setup_openrouter_opencode_auth.py,
# since `opencode auth login openrouter` is broken headlessly). These
# preserve the direct CloudCLI/Anthropic subscription's credit: see
# ai.ai_router.ROLE_PROVIDERS["coding"], where they rotate ahead of the
# direct "claude" provider. Opus 4.7 is tried first in that rotation per
# explicit user directive (2026-07-30).
#
# Named openrouter_claude_opus/openrouter_claude_sonnet (not the design
# doc's original "openrouter_claude") because 13V already shipped a
# text_task-only provider under the "openrouter_claude" key (see below) --
# these coding routes must not collide with or overwrite it.
OPENROUTER_CLAUDE_OPUS_MODEL = "openrouter/anthropic/claude-opus-4.7"
OPENROUTER_CLAUDE_SONNET_MODEL = "openrouter/anthropic/claude-sonnet-4.6"


def _openrouter_claude_opus_run_coding_task(project_path, instruction, **kwargs):
    kwargs.setdefault("model", OPENROUTER_CLAUDE_OPUS_MODEL)
    return opencode_bridge.run_coding_task(project_path, instruction, **kwargs)


def _openrouter_claude_sonnet_run_coding_task(project_path, instruction, **kwargs):
    kwargs.setdefault("model", OPENROUTER_CLAUDE_SONNET_MODEL)
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
    "groq",
    run_text_task=_groq_run_text_task,
    available_fn=lambda: bool(os.getenv("GROQ_API_KEY")),
    kind="cloud",
    description="Groq -- fast log/quick analysis, simple tasks",
    cost_tier="free",
)

register_provider(
    "openai",
    run_text_task=_openai_run_text_task,
    # 2026-08-02 operator directive: backed by the self-hosted Qwen3-Coder
    # vLLM instance now, not the real OpenAI API -- see
    # core.llm_clients.call_openai. Availability follows the credential it
    # actually uses.
    available_fn=lambda: bool(os.getenv("VLLM_QWEN3_CODER_API_KEY")) and bool(os.getenv("VLLM_QWEN3_CODER_BASE_URL")),
    kind="cloud",
    description="Qwen3-Coder-30B-A3B-Instruct-AWQ, self-hosted vLLM on RunPod (was real OpenAI API until 2026-08-02) -- heavy-lifting text_task capacity, roadmap expediting per operator directive",
    cost_tier="free_or_low_cost",
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

register_provider(
    "opencode_deepseek",
    run_coding_task=_opencode_deepseek_run_coding_task,
    available_fn=_opencode_available,
    kind="cloud",
    description="DeepSeek V4 Pro (openrouter/deepseek/deepseek-v4-pro via opencode CLI) -- OpenRouter credential, no Zen key collision",
    cost_tier="free_or_low_cost",
)

register_provider(
    "qwen3_coding",
    run_coding_task=_qwen3_coding_run_coding_task,
    available_fn=_qwen3_coding_available,
    kind="cloud",
    description="Qwen3-Coder-30B-A3B-Instruct-AWQ, self-hosted vLLM on RunPod (pay-per-use GPU) via a custom opencode provider -- confirmed live tool-calling 2026-08-02 after the pod's vllm serve gained --enable-auto-tool-choice --tool-call-parser qwen3_coder",
    cost_tier="free_or_low_cost",
)

register_provider(
    "openrouter_claude_opus",
    run_coding_task=_openrouter_claude_opus_run_coding_task,
    available_fn=lambda: _opencode_credential_available("openrouter"),
    kind="cloud",
    description="Claude Opus 4.7 (openrouter/anthropic/claude-opus-4.7 via opencode CLI) -- billed through the OpenRouter account, not the CloudCLI/Anthropic subscription; tried first in the coding role's alt-Claude rotation per user directive (2026-07-30)",
    cost_tier="paid",
)

register_provider(
    "openrouter_claude_sonnet",
    run_coding_task=_openrouter_claude_sonnet_run_coding_task,
    available_fn=lambda: _opencode_credential_available("openrouter"),
    kind="cloud",
    description="Claude Sonnet 4.6 (openrouter/anthropic/claude-sonnet-4.6 via opencode CLI) -- billed through the OpenRouter account, not the CloudCLI/Anthropic subscription; coding-capable counterpart of 13V's text-only 'openrouter_claude'",
    cost_tier="paid",
)

register_provider(
    "local",
    run_coding_task=_local_not_implemented,
    available_fn=lambda: False,
    kind="local",
    description="Extension point for a future local model (e.g. Ollama) -- not installed",
    cost_tier="free",
)
