# OpenRouter Expansion: Claude-Preserving Coding Fallback + Text-Model Rotation

Date: 2026-07-28
Status: Approved, not yet implemented

## Update 2026-07-30

User directive: OpenRouter also offers `anthropic/claude-opus-4.7`, not
just `claude-sonnet-4.6`. Add it, and **prioritize** it and
`deepseek/deepseek-v4-flash` specifically (both named explicitly) over the
rest of this design's model set when this phase is built:

- **New provider `openrouter_claude_opus`**: mirrors the `openrouter_claude`
  section below exactly, but `OPENROUTER_CLAUDE_OPUS_MODEL =
  "openrouter/anthropic/claude-opus-4.7"`, gated on the same `"openrouter"`
  opencode credential. In the coding role's alt-Claude rotation (`Section 2`
  below), it should be tried **first** -- i.e. `_rotate_candidates(task_type,
  ["openrouter_claude_opus", "opencode_claude", "openrouter_claude"])` --
  ahead of both existing alt-Claude routes, not just added to the tail.
- **`deepseek/deepseek-v4-flash`** should be moved to the front of
  `OPENROUTER_MODELS` (Section 3 below), so the text-task rotation tries it
  before `openai/gpt-4o-mini`/`z-ai/glm-5`/`openai/gpt-5`, not after.
  `deepseek/deepseek-v4-pro` is already live separately as the `opencode_deepseek`
  provider (`core/ai_provider.py`, `OPENCODE_DEEPSEEK_MODEL`) -- no change
  needed there.
- **Known conflict to resolve during this build**: 13V (built and merged
  2026-07-30, before this phase) already registered a provider named
  `openrouter_claude` in `core/ai_provider.py` -- but as a **text_task-only**
  provider (`_openrouter_claude_run_text_task`, calling
  `llm_clients.call_openrouter_claude()`, model `anthropic/claude-sonnet-4.6`
  -- no `openrouter/` prefix, and no coding-agent capability), used in 13V's
  fixed-order "architecture" planning chain. This design's Section 1 defines
  a *different*, coding-capable `openrouter_claude` (via `opencode_bridge
  .run_coding_task`, model string `openrouter/anthropic/claude-sonnet-4.6`
  with the prefix). These two cannot both be registered under the same
  provider key. Resolve by renaming this design's coding-capable version to
  a distinct key (e.g. `openrouter_claude_coding`) rather than colliding
  with or silently overwriting 13V's already-shipped, already-relied-upon
  text_task provider -- check `core/ai/ai_router.py`'s `"architecture"`
  `ROLE_PROVIDERS` entry (13V) still resolves correctly before/after.

## Problem

Two separate asks from the user (2026-07-28), both routed through OpenRouter:

1. OpenRouter now offers several new models (GLM 5, GPT-5, DeepSeek V4 Flash,
   DeepSeek V4 Pro, Claude Sonnet 4.6) that Kai isn't using. The `openrouter`
   provider (`core/ai_provider.py`) is a plain text-completion role
   (planning/log_analysis/documentation, per `core/llm_clients.py`'s
   docstring) hardcoded to a single model, `openai/gpt-4o-mini`.
2. Separately, and more significantly: the user wants the direct Claude
   subscription's credits preserved. Today, `ROLE_PROVIDERS["coding"]`
   (`core/ai/ai_router.py`) tries `claude` first and only falls back to
   `opencode_claude` (Claude Fable 5, billed through OpenCode Zen) on
   quota/failure. The user now wants Kai to prefer non-subscription Claude
   routes proactively for every coding task, not just after the direct
   subscription is already exhausted -- and wants OpenRouter's Claude
   Sonnet 4.6 added as a second such route alongside Fable 5.

Verified live during design (2026-07-28, this session): `opencode` CLI (the
same tool already driving the `opencode`/`opencode_claude` fallbacks) can
drive OpenRouter-hosted models with full agentic tool-use/file-write
capability once an `"openrouter"` credential exists in its own credential
store (`~/.local/share/opencode/auth.json`) -- confirmed by writing a real
`{"type": "api", "key": "..."}` entry and running `opencode run --model
openrouter/anthropic/claude-sonnet-4.6 ...`, which wrote a real file. All 5
requested model IDs (`z-ai/glm-5`, `openai/gpt-5`, `deepseek/deepseek-v4-flash`,
`deepseek/deepseek-v4-pro`, `anthropic/claude-sonnet-4.6`) were also confirmed
live against OpenRouter's plain chat-completions API. `opencode auth login
openrouter` itself is broken in this headless environment (`fetch() URL is
invalid`) -- the direct `auth.json` write is the only confirmed-working setup
path.

## Goals

- Add `openrouter_claude`, a new coding-capable provider: OpenRouter's
  `anthropic/claude-sonnet-4.6` driven through the `opencode` CLI, alongside
  the existing `opencode_claude` (Fable 5).
- Reorder the `coding` role so both alt-Claude routes (`opencode_claude`,
  `openrouter_claude`) are always tried before the direct `claude` provider
  -- direct `claude` becomes a last-resort fallback, not the primary.
- Expand the plain-text `openrouter` provider's model to rotate across 5
  models (`openai/gpt-4o-mini` plus the 4 new non-Claude models) instead of
  a single hardcoded one, reusing the existing rotation pattern.
- Make the OpenRouter-in-opencode credential setup a re-runnable script, not
  a one-off manual edit, since `auth.json` isn't otherwise app-managed.

## Non-goals

- Claude Sonnet 4.6 does **not** join the text-task `openrouter` rotation --
  its role in this design is the coding fallback only, keeping the
  cheap/plain-text role cheap.
- No change to the `planning`, `log_analysis`, `documentation`, or `review`
  roles' provider order -- only their shared `openrouter` provider gains
  more models to rotate through.
- No new generic tool-use loop against OpenRouter's raw API. The `opencode`
  CLI already provides the agentic loop; this only adds a new model target
  for it, exactly like `opencode_claude` did for Fable 5.
- No automatic migration of the `OPENROUTER_API_KEY` env var into opencode's
  credential store at app-startup time -- follows the existing convention
  (the Zen credential was also set up out-of-band, once, and just read by
  the app) rather than adding new implicit startup behavior.

## Architecture

### 1. New provider: `openrouter_claude`

`core/ai_provider.py`, mirroring `opencode_claude` exactly except for the
credential it depends on and the model string:

```python
OPENROUTER_CLAUDE_MODEL = "openrouter/anthropic/claude-sonnet-4.6"


def _opencode_credential_available(key):
    if shutil.which("opencode") is None:
        return False
    try:
        auth = json.loads(OPENCODE_AUTH_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    return key in auth


def _opencode_available():
    return _opencode_credential_available("opencode")


def _openrouter_claude_run_coding_task(project_path, instruction, **kwargs):
    kwargs.setdefault("model", OPENROUTER_CLAUDE_MODEL)
    return opencode_bridge.run_coding_task(project_path, instruction, **kwargs)


register_provider(
    "openrouter_claude",
    run_coding_task=_openrouter_claude_run_coding_task,
    available_fn=lambda: _opencode_credential_available("openrouter"),
    kind="cloud",
    description="Claude Sonnet 4.6 (openrouter/anthropic/claude-sonnet-4.6 via opencode CLI) -- billed through the OpenRouter account, not the CloudCLI/Anthropic subscription",
)
```

`_opencode_available` is refactored to call the new generalized helper with
`"opencode"` so its existing behavior (and the `opencode`/`opencode_claude`
providers that depend on it) is unchanged.

### 2. Coding-role order: alt-Claude routes first, direct Claude last-resort

`core/ai/ai_router.py`. Uniformly rotating the whole 4-candidate list (as
every other role does) would put direct `claude` first on 1-in-4 calls,
defeating the credit-preservation goal. Instead, `_candidates_for` special-
cases `coding`: only the two alt-Claude routes rotate against each other;
`claude` and `opencode` are a fixed tail, always tried last and in that
order.

```python
ROLE_PROVIDERS = {
    "coding": ["opencode_claude", "openrouter_claude", "claude", "opencode"],
    ...  # planning/log_analysis/documentation/review unchanged
}

CODING_FIXED_TAIL = ["claude", "opencode"]


def _candidates_for(task_type):
    if task_type == "coding":
        rotating = _rotate_candidates(task_type, ["opencode_claude", "openrouter_claude"])
        return rotating + CODING_FIXED_TAIL
    return ROLE_PROVIDERS.get(task_type, ["claude"])
```

`delegate()` itself is unchanged -- it already just walks whatever
`_candidates_for` returns via `_rotate_candidates`'s wrapping call in
`delegate()`. Note `_candidates_for("coding")` now returns an
already-rotated list; `delegate()`'s existing
`_rotate_candidates(resolved_type, _candidates_for(resolved_type))` call
would double-rotate it, so `delegate()` calls `_candidates_for` directly
for `coding` and skips the outer rotation for that one role:

```python
def delegate(description, task_type=None, timeout=60, project_path=None, capability="text_task"):
    resolved_type = task_type or classify_task(description)
    candidates = (
        _candidates_for(resolved_type) if resolved_type == "coding"
        else _rotate_candidates(resolved_type, _candidates_for(resolved_type))
    )
    ...  # unchanged from here
```

Every other role's behavior (full-list rotation) is unaffected.

### 3. Text-task `openrouter` model rotation

`core/llm_clients.py`:

```python
OPENROUTER_MODELS = [
    "openai/gpt-4o-mini",
    "z-ai/glm-5",
    "openai/gpt-5",
    "deepseek/deepseek-v4-flash",
    "deepseek/deepseek-v4-pro",
]
```

`core/ai_provider.py`'s `_openrouter_run_text_task` picks the next model via
a new rotation state file (same shape as `provider_rotation.json`), keyed by
a fixed key since this provider has no per-role model split:

```python
OPENROUTER_MODEL_ROTATION_FILE = "openrouter_model_rotation.json"


def _next_openrouter_model():
    state = load(OPENROUTER_MODEL_ROTATION_FILE) or {}
    start = state.get("index", 0) % len(llm_clients.OPENROUTER_MODELS)
    state["index"] = (start + 1) % len(llm_clients.OPENROUTER_MODELS)
    save(OPENROUTER_MODEL_ROTATION_FILE, state)
    return llm_clients.OPENROUTER_MODELS[start]


def _openrouter_run_text_task(prompt, timeout=60, project_path=None):
    return llm_clients.call_openrouter(prompt, model=_next_openrouter_model(), timeout=timeout)
```

`call_openrouter`'s existing `model=OPENROUTER_DEFAULT_MODEL` default stays
as a fallback for direct callers/tests that don't go through
`_openrouter_run_text_task` -- `OPENROUTER_DEFAULT_MODEL` remains
`"openai/gpt-4o-mini"`, unchanged, just no longer the *only* model the
provider ever uses in production.

### 4. Credential setup script

New `scripts/setup_openrouter_opencode_auth.py`, idempotent, no external
dependencies beyond stdlib:

```python
"""Registers OPENROUTER_API_KEY with opencode's own credential store so
openrouter_claude (core.ai_provider) can drive OpenRouter models through
the opencode CLI. Re-run safely any time -- e.g. after a fresh deploy, or
if auth.json is reset. `opencode auth login openrouter` cannot be used for
this: it fails headlessly with "fetch() URL is invalid" (confirmed
2026-07-28)."""

import json
import os
import sys
from pathlib import Path

AUTH_PATH = Path.home() / ".local" / "share" / "opencode" / "auth.json"


def main():
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        print("OPENROUTER_API_KEY is not set", file=sys.stderr)
        return 1

    auth = json.loads(AUTH_PATH.read_text()) if AUTH_PATH.exists() else {}
    auth["openrouter"] = {"type": "api", "key": key}
    AUTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUTH_PATH.write_text(json.dumps(auth))
    print(f"openrouter credential written to {AUTH_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Documented as a one-time (or re-run-as-needed) setup step in
`OPERATIONS.md`, alongside wherever the OpenCode Zen credential's manual
setup is already noted -- not invoked automatically by the app itself, per
Non-goals.

## Error handling

- `openrouter_claude`'s `available_fn` returns `False` (candidate skipped,
  not attempted) whenever the `"openrouter"` key is absent from `auth.json`
  or the `opencode` binary isn't on `PATH` -- same "not available (no
  credentials configured)" failure message path every other provider
  already produces in `delegate()`.
- A failed/erroring `openrouter_claude` coding run records to
  `ai_usage_history.json` and `provider_health` exactly like
  `opencode_claude` does today (`_record_coding_failure_health`,
  unchanged) -- quota-exceeded detection, error capture, and the
  `AllProvidersFailed` fallback-exhaustion path all apply unmodified.
- `_next_openrouter_model()` has no failure mode of its own (same as
  today's `_rotate_candidates`) -- if `memory/openrouter_model_rotation.json`
  is missing or corrupt, `load()` returning `None`/falsy already defaults to
  `{}`, same as `_rotate_candidates`'s existing handling.
- `setup_openrouter_opencode_auth.py` exits non-zero with a clear message if
  `OPENROUTER_API_KEY` isn't set; never partially writes `auth.json` (reads
  full dict, mutates one key, writes full dict back).

## Testing

- `core/ai_provider.py`:
  - `openrouter_claude` registered with `coding_agent` capability only (no
    `text_task`), matching `opencode_claude`.
  - `_opencode_credential_available` returns `True`/`False` correctly for
    each of `"opencode"`/`"openrouter"` independently based on `auth.json`
    contents (existing `_opencode_available` tests continue to pass
    unchanged since it's now a thin wrapper).
  - `_openrouter_claude_run_coding_task` passes `OPENROUTER_CLAUDE_MODEL` as
    the default `model` kwarg to `opencode_bridge.run_coding_task`, and
    respects an explicit override.
- `core/ai/ai_router.py`:
  - `_candidates_for("coding")` returns a 4-item list ending exactly in
    `["claude", "opencode"]`, with the first two elements being some
    rotation of `["opencode_claude", "openrouter_claude"]`.
  - Across repeated calls, the front pair's order alternates (rotation
    state advances) while the fixed tail's order never changes.
  - `delegate()` with `task_type="coding"` and both `opencode_claude` and
    `openrouter_claude` unavailable falls through to `claude`, then
    `opencode`, in that order -- existing `AllProvidersFailed` test pattern
    extended with the two new candidates stubbed unavailable.
  - Every existing non-coding role's rotation test continues to pass
    unchanged.
- `core/llm_clients.py`:
  - `OPENROUTER_MODELS` contains exactly the 5 expected model strings.
  - `call_openrouter` still defaults to `OPENROUTER_DEFAULT_MODEL` when no
    `model` kwarg is passed (direct-call/back-compat case).
- `core/ai_provider.py` (rotation):
  - `_next_openrouter_model()` cycles through all 5 models across 5 calls
    before repeating, persists across calls via
    `openrouter_model_rotation.json`, and starts from index 0 when the
    state file doesn't exist yet.
- `scripts/setup_openrouter_opencode_auth.py`:
  - Writes a new `"openrouter"` entry when `auth.json` doesn't exist yet
    (creates parent dirs).
  - Merges into an existing `auth.json` without disturbing other keys
    (e.g. the existing `"opencode"` entry survives).
  - Exits 1 with no file write when `OPENROUTER_API_KEY` is unset.
- Live-call tests (matching the existing `test_call_openrouter_against_real_api`
  pattern, skipped unless credentials are present): one of the 4 new
  `openrouter` text models, and `openrouter_claude`'s coding path, both
  confirmed against the real APIs -- already manually verified live during
  this design session; codifying that verification as a real (skippable)
  test matches this codebase's existing "confirmed live" convention rather
  than just leaving it as a comment.

## Alternatives considered

**Build a custom tool-use/agentic loop directly against OpenRouter's chat-
completions API**, instead of reusing `opencode` CLI as the execution
engine. Rejected: `opencode` already provides a working, tested sandboxed
file-write/tool-use loop and already supports arbitrary model targets
(`--model <provider>/<model>`) -- confirmed live that it drives OpenRouter
models exactly as it drives OpenCode Zen models. A custom loop would
duplicate that work for no capability gain.

**Automatically sync `OPENROUTER_API_KEY` into opencode's `auth.json` at
app startup**, instead of a separate manual/re-runnable script. Rejected:
no existing code path manages `auth.json` today (the Zen credential is
read-only from the app's perspective); adding implicit startup
credential-writing would be new, unreviewed-by-precedent behavior for a
file that also holds other real credentials. A script keeps the write
explicit and auditable, consistent with how the Zen credential already
works.

**Rotate `claude` into the coding role's rotating front group too** (treat
all three Claude-capable routes as equally rotatable) instead of a fixed
tail. Rejected: directly contradicts the user's explicit goal of preserving
the direct subscription's credit -- a fixed tail is the only way to
guarantee `claude` is never the first attempt.
