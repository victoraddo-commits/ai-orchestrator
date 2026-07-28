# Parallel Build Execution + Claude Oversight

Date: 2026-07-28
Status: Approved, not yet implemented

## Problem

`advance_builds()` (`core/build_manager.py`) processes every ready build
sequentially in a single loop, one `delegate()` call at a time. Each build
already runs isolated (its own git branch per Phase 12D, its own sandbox
per Phase 12E), but nothing takes advantage of that isolation to actually
run builds concurrently. With multiple coding-capable providers now
available (claude, opencode, opencode_claude) and credit loaded on more
than one of them, sequential processing leaves real throughput and real
provider diversity on the table.

Separately, this session's earlier addition of per-role provider rotation
(`core/ai/ai_router.py::_rotate_candidates`) has a latent race: it reads
`provider_rotation.json`, then writes it, as two separate steps. That's
harmless under today's strictly sequential execution, but becomes a real
bug the moment two builds are dispatched at once — both could read the
same rotation index and land on the same provider, defeating the point of
diversifying usage.

## Goals

- Run up to `max_concurrent_builds` (default 2) ready builds at the same
  time, each potentially handled by a different provider.
- Add an automated, purely advisory Claude review step after generation,
  before the existing human deploy-approval gate — genuine oversight, not
  a new auto-approve/auto-block path.
- Make provider rotation atomic so concurrently-dispatched builds
  reliably get different starting providers.
- Every coding-agent instruction Kai sends (regardless of which provider
  ends up handling it) carries the same "write tests, verify before
  claiming done" discipline this development workflow itself follows.
- Preserve all existing single-build sequential behavior exactly —
  parallelism only changes anything when there's more than one ready
  build.

## Non-goals

- Splitting a single build into concurrent sub-tasks worked by different
  agents within the same branch (considered and explicitly rejected — see
  Alternatives).
- Any change to the existing human approval gates
  (`WAITING_FOR_ARCHITECTURE_APPROVAL`, `WAITING_FOR_DEPLOY_APPROVAL`) or
  to Kai's restriction on approving its own production changes
  (`core/kai/policies.py`) — the new review step is additive and advisory
  only.
- True OS-level process isolation between concurrent builds (see
  Alternatives) — not justified given the workload is already I/O-bound
  and out-of-process (subprocess/HTTP calls).

## Architecture

`advance_builds()` splits builds into "ready to advance" (status in
`REQUESTED`/`PLANNING`/`GENERATING`/`DEPLOYING`) and dispatches each to a
`ThreadPoolExecutor(max_workers=max_concurrent_builds)`. Each worker calls
the existing `_run_planning` / `_run_generation` / `_run_deployment`
functions unchanged — only the dispatch loop and how results get
persisted are new.

```python
MAX_CONCURRENT_BUILDS = 2  # config/providers.yaml: max_concurrent_builds

_ACTIONABLE_STATUSES = {"REQUESTED", "PLANNING", "GENERATING", "CODE_REVIEW", "DEPLOYING"}

def _advance_one_build(build):
    try:
        status = build.get("status")
        if status == "REQUESTED":
            transition(build, "PLANNING", BUILD_TRANSITIONS)
            _run_planning(build)
        elif status == "PLANNING":
            _run_planning(build)
        elif status == "GENERATING":
            _run_generation(build)
        elif status == "CODE_REVIEW":
            _run_code_review(build)
        elif status == "DEPLOYING":
            _run_deployment(build)
    except Exception as error:
        transition(build, "FAILED", BUILD_TRANSITIONS)
        build["failure_reason"] = f"Unexpected error: {error}"
        _record_if_terminal(build)

    _persist_build(build)
    return build


def advance_builds():
    builds = load_builds()
    ready = [b for b in builds if b.get("status") in _ACTIONABLE_STATUSES]

    if not ready:
        return builds

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_BUILDS) as pool:
        list(pool.map(_advance_one_build, ready))

    return load_builds()
```

Builds not in `ready` are never touched or resaved, unlike the current
code (which resaves the full list every cycle regardless).

## Persistence: `core.memory.update()`

New primitive in `core/memory.py` / `core/memory_manager.py`, reusing the
existing `fcntl.flock` critical section from `memory_manager.write()` so a
whole read-modify-write happens atomically instead of as two separate
lock-free steps:

```python
# core/memory_manager.py
def update(path, mutate_fn, default):
    path = Path(path)
    lock_path = _lock_path(path)
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            current = read(path, default)  # existing read(), reused
            updated = mutate_fn(current)
            _write_locked(path, updated)
            return updated
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)

# core/memory.py
def update(name, mutate_fn, directory=None):
    directory = directory or MEMORY_DIR
    if _running_under_test() and directory.resolve() == PRODUCTION_MEMORY_DIR.resolve():
        raise ProductionMemoryWriteBlocked(...)  # same guard as save()
    return memory_manager.update(directory / name, mutate_fn, default={})
```

`_persist_build(build)` in `build_manager.py` uses this to update just one
build's record by id, leaving every other build's on-disk record
untouched:

```python
def _persist_build(build):
    def mutate(records):
        records = records if isinstance(records, list) else []
        for i, existing in enumerate(records):
            if existing["id"] == build["id"]:
                records[i] = build
                return records
        records.append(build)
        return records

    memory.update(BUILDS_FILE, mutate)
```

## Provider rotation atomicity

`_rotate_candidates` in `core/ai/ai_router.py` is rewritten on top of
`core.memory.update()`, closing the read-then-write race:

```python
def _rotate_candidates(task_type, candidates):
    if len(candidates) <= 1:
        return candidates

    captured = {}

    def mutate(state):
        state = state if isinstance(state, dict) else {}
        start = state.get(task_type, 0) % len(candidates)
        captured["start"] = start
        state[task_type] = (start + 1) % len(candidates)
        return state

    update(ROTATION_STATE_FILE, mutate)  # from core.memory import load, save, update

    start = captured["start"]
    return candidates[start:] + candidates[:start]
```

Two builds dispatched into the thread pool at the same time and both
resolving to `task_type="coding"` now deterministically get different
starting candidates (e.g. build A → `claude`, build B →
`opencode_claude`) instead of racing to read the same index.

## Review gate: `CODE_REVIEW`

New state inserted between `GENERATING` and `SECURITY_REVIEW`. This moves
the security-scan work `_run_generation` currently does inline
(`run_all_scans` + the `WAITING_FOR_DEPLOY_APPROVAL` transition +
`_create_deploy_approval`) out of `_run_generation` and into
`_run_code_review`.

The existing code already cascades multiple transitions within a single
function call (`_run_generation` today goes `GENERATING` →
`SECURITY_REVIEW` → `WAITING_FOR_DEPLOY_APPROVAL` in one call;
`_run_planning` goes `PLANNING` → `WAITING_FOR_USER_INPUT` or
`WAITING_FOR_ARCHITECTURE_APPROVAL` in one call), and existing tests
assert a single `advance_builds()` call reaches the approval gate from
`GENERATING`. To preserve that — not silently add a second cycle before a
build reaches approval — `_run_generation` calls `_run_code_review(build)`
directly at the end of a successful generation, in the same call, instead
of leaving the build sitting in `CODE_REVIEW` for a later dispatch cycle
to pick up:

```python
transition(build, "CODE_REVIEW", BUILD_TRANSITIONS)
_run_code_review(build)  # cascades through to WAITING_FOR_DEPLOY_APPROVAL, same call
```

The `CODE_REVIEW` branch in `_advance_one_build`'s dispatch (added above)
stays as a defensive fallback only — e.g. a build somehow persisted
mid-cascade after a crash — not the primary path:

```python
BUILD_TRANSITIONS = {
    ...
    "GENERATING": ["CODE_REVIEW", "TESTING", "SECURITY_REVIEW", "COMPLETED", "FAILED"],  # SECURITY_REVIEW/COMPLETED kept as pre-existing theoretical direct targets, unused by current code either way
    "CODE_REVIEW": ["SECURITY_REVIEW", "FAILED"],
    "SECURITY_REVIEW": ["WAITING_FOR_DEPLOY_APPROVAL", "FAILED"],  # unchanged
    ...
}
```

`_run_code_review(build)`:

- If `build["generated_by"] == "claude"`, skip — records
  `build["code_review"] = {"skipped": True, "reason": "generated by claude"}`
  and transitions straight through. No value in Claude reviewing its own
  work.
- Otherwise calls the `claude` provider directly (`ai_provider.get_provider("claude")`,
  not `delegate(task_type="review")`, since the general review-role
  rotation could hand this to openai/gemini instead — the requirement is
  specifically Claude oversight) with the build's diff/generated files as
  context, and records the findings text.
- If Claude itself is unavailable or the call fails, records
  `{"skipped": True, "reason": "<error>"}` and proceeds anyway — advisory
  checks must never stall the pipeline, matching the existing
  "must not stall Kai" pattern used throughout `ai_router.py`.
- In every case (findings, skip, or failure) `_run_code_review` then does
  what `_run_generation` used to do inline: transitions to
  `SECURITY_REVIEW`, runs `build["security_report"] = run_all_scans(...)`,
  transitions to `WAITING_FOR_DEPLOY_APPROVAL`, and calls
  `_create_deploy_approval(build)` — exactly today's sequence, just
  triggered one state later. `build["code_review"]` is surfaced at that
  same human gate alongside `build["security_report"]` — never used to
  auto-fail or auto-approve.

## Worker instruction discipline

```python
GENERATION_DISCIPLINE_PREAMBLE = (
    "Follow disciplined engineering practice: write tests for new "
    "behavior before or alongside the implementation, run them, and "
    "verify they actually pass before considering any part of this "
    "done. Do not claim something works without having run it.\n\n"
)
```

Prepended inside `_generation_prompt(build)`'s returned instruction, so it
applies uniformly regardless of which provider (claude / opencode /
opencode_claude) ends up generating for a given build.

## Error handling

- `_advance_one_build` catches any unexpected exception, marks that
  build `FAILED` with the error recorded, and always calls
  `_persist_build` — one build's crash can't lose track of another
  build's concurrently-computed result inside the same `pool.map` call.
- `_run_code_review` never raises to its caller.
- `core.memory.update()`'s lock is OS-level (`fcntl.flock`), so it's safe
  across threads *and* processes — the CloudCLI dashboard reading
  `builds.json` while a worker writes it stays safe too, same guarantee
  `memory_manager.write()` already provides for a single full-file write.

## Testing

- `core/memory_manager.py` / `core/memory.py`: `update()` — no lost
  updates across interleaved read-modify-writes, correct default when the
  file doesn't exist yet, respects the production-write test guard.
- `core/build_manager.py`:
  - `advance_builds()` with two builds simultaneously in `GENERATING`,
    each provider stubbed to a distinct, distinguishable result — both
    builds' `generated_by` end up correct with no cross-talk.
  - `_run_code_review`: findings attached + build reaches
    `WAITING_FOR_DEPLOY_APPROVAL` when `generated_by != "claude"`; skipped
    when `generated_by == "claude"`; skipped gracefully (build still
    proceeds) when Claude is unavailable at review time.
  - `_generation_prompt()` includes `GENERATION_DISCIPLINE_PREAMBLE`.
  - All existing single-build sequential tests continue to pass
    unchanged — the refactor is a no-op when there is only one ready
    build, and the `GENERATING` → `WAITING_FOR_DEPLOY_APPROVAL` cascade
    (now passing through `CODE_REVIEW` internally) still completes within
    one `advance_builds()` call, exactly as today.
- `core/ai/ai_router.py`: existing rotation tests continue to pass;
  `_rotate_candidates` now goes through `core.memory.update()`.

## Alternatives considered

**Split one build into concurrent sub-tasks worked by different agents on
the same branch.** Rejected: requires new task-decomposition and
merge/conflict-resolution logic within a single build, and none of the
existing isolation (separate branch, separate sandbox) applies within one
build. Cross-build parallelism gets the same "multiple agents working at
once" outcome for far less risk, since builds are already isolated by
design.

**Process-per-build (`ProcessPoolExecutor` / subprocess-per-build)
instead of threads.** Rejected for now: the actual bottleneck is I/O
(subprocess/HTTP calls to opencode/CloudCLI), so threads already give
real concurrency without the GIL being a factor. Processes would add
build-state serialization, process lifecycle handling (timeouts, orphan
cleanup), and log-capture machinery this codebase doesn't have anywhere
else today, for isolation this workload doesn't need — every
cross-boundary interaction already goes through subprocess/HTTP, which is
itself the isolation boundary.

**Route the review gate through `delegate(task_type="review")` instead of
calling Claude directly.** Rejected: the general review-role rotation
(`["openai", "gemini", "claude"]`) could hand the review to a non-Claude
provider, which doesn't satisfy "Claude specifically oversees" the way
the user asked for it.
