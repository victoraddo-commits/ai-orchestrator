# Parallel Build Execution + Claude Oversight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Kai run up to 2 ready builds concurrently (instead of one at a time), add a purely-advisory Claude code-review gate before deploy approval, close a latent race in provider rotation, and bake TDD/verification discipline into every coding-agent instruction.

**Architecture:** `core/build_manager.py::advance_builds()` dispatches ready builds to a `ThreadPoolExecutor`. A new `core.memory.update()` primitive (atomic read-modify-write, reusing the existing `fcntl.flock` lock) makes per-build persistence and provider rotation safe under real concurrency. A new `CODE_REVIEW` build state sits between `GENERATING` and `SECURITY_REVIEW`.

**Tech Stack:** Python 3.12, pytest, `concurrent.futures.ThreadPoolExecutor`, `fcntl` file locking (already in use).

**Spec:** `docs/superpowers/specs/2026-07-28-parallel-build-execution-design.md` — read it first for the full rationale; this plan implements it task by task.

---

### Task 1: `core.memory_manager.update()` — atomic read-modify-write primitive

**Files:**
- Modify: `core/memory_manager.py`
- Test: `tests/test_memory_manager.py`

- [ ] **Step 1: Write the failing tests**

Add to the end of `tests/test_memory_manager.py`:

```python
def test_update_applies_mutate_fn_and_persists_result(tmp_path):
    path = tmp_path / "counters.json"
    mm.write(path, {"count": 1})

    result = mm.update(path, lambda current: {"count": current["count"] + 1}, default={})

    assert result == {"count": 2}
    assert mm.read(path, default={}) == {"count": 2}


def test_update_passes_default_when_file_does_not_exist(tmp_path):
    path = tmp_path / "does_not_exist.json"

    result = mm.update(path, lambda current: {**current, "seen": True}, default={})

    assert result == {"seen": True}
    assert mm.read(path, default={}) == {"seen": True}


def test_update_is_atomic_under_concurrent_calls(tmp_path):
    import threading

    path = tmp_path / "counter.json"
    mm.write(path, {"count": 0})

    def increment():
        mm.update(path, lambda current: {"count": current["count"] + 1}, default={"count": 0})

    threads = [threading.Thread(target=increment) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert mm.read(path, default={})["count"] == 20
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_memory_manager.py -k test_update -v`
Expected: FAIL with `AttributeError: module 'core.memory_manager' has no attribute 'update'`

- [ ] **Step 3: Implement `update()` in `core/memory_manager.py`**

Add this function immediately after the existing `write()` function (after line 76, before `_write_locked`):

```python
def update(path, mutate_fn, default):

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    lock_path = _lock_path(path)

    with open(lock_path, "w") as lock_file:

        fcntl.flock(lock_file, fcntl.LOCK_EX)

        try:
            current = read(path, default)
            updated = mutate_fn(current)
            _write_locked(path, updated)
            return updated
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_memory_manager.py -v`
Expected: PASS (all tests in the file, not just the new ones)

- [ ] **Step 5: Commit**

```bash
git add core/memory_manager.py tests/test_memory_manager.py
git commit -m "Add atomic read-modify-write primitive to memory_manager"
```

---

### Task 2: `core.memory.update()` — wrapper with production-write guard

**Files:**
- Modify: `core/memory.py`
- Test: `tests/test_memory_isolation.py`

- [ ] **Step 1: Write the failing tests**

Add to the end of `tests/test_memory_isolation.py`:

```python
def test_update_blocks_writes_to_production_dir_during_tests():
    with pytest.raises(memory.ProductionMemoryWriteBlocked):
        memory.update(
            "incidents.json",
            lambda current: current,
            directory=memory.PRODUCTION_MEMORY_DIR,
        )


def test_update_applies_mutate_fn_in_isolated_dir(isolated_memory):
    memory.save("counters.json", {"count": 1})

    result = memory.update("counters.json", lambda current: {"count": current["count"] + 1})

    assert result == {"count": 2}
    assert memory.load("counters.json") == {"count": 2}


def test_update_passes_empty_dict_default_when_file_does_not_exist(isolated_memory):
    result = memory.update("brand_new.json", lambda current: {**current, "created": True})

    assert result == {"created": True}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_memory_isolation.py -k test_update -v`
Expected: FAIL with `AttributeError: module 'core.memory' has no attribute 'update'`

- [ ] **Step 3: Implement `update()` in `core/memory.py`**

Add this function immediately after the existing `save()` function (after line 46, before `update_system_scan`):

```python
def update(name, mutate_fn, directory=None):

    directory = directory or MEMORY_DIR

    if _running_under_test() and directory.resolve() == PRODUCTION_MEMORY_DIR.resolve():
        raise ProductionMemoryWriteBlocked(
            f"Refusing to write {name!r} to production memory/ during a test run. "
            "Use an isolated memory directory (see tests/conftest.py)."
        )

    return memory_manager.update(directory / name, mutate_fn, default={})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_memory_isolation.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/memory.py tests/test_memory_isolation.py
git commit -m "Add core.memory.update() wrapper with production-write guard"
```

---

### Task 3: Make provider rotation atomic

**Files:**
- Modify: `core/ai/ai_router.py`
- Test: `tests/test_ai_router.py`

- [ ] **Step 1: Write the failing test**

Add to the end of `tests/test_ai_router.py`:

```python
def test_rotate_candidates_gives_each_concurrent_caller_a_distinct_starting_point():
    # Before this fix, _rotate_candidates read then wrote the rotation state
    # as two separate steps -- fine sequentially, but two builds dispatched
    # at once could both read the same index and land on the same provider.
    import threading

    candidates = ["a", "b", "c"]
    results = []
    lock = threading.Lock()

    def call():
        rotated = ai_router._rotate_candidates("concurrency_test_role", candidates)
        with lock:
            results.append(rotated[0])

    threads = [threading.Thread(target=call) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == candidates
```

- [ ] **Step 2: Run test to verify it fails or is flaky**

Run: `python3 -m pytest tests/test_ai_router.py -k test_rotate_candidates_gives_each_concurrent -v --count=10` (if `pytest-repeat` isn't installed, run the plain command 10 times in a loop instead: `for i in $(seq 10); do python3 -m pytest tests/test_ai_router.py -k test_rotate_candidates_gives_each_concurrent -v || break; done`)
Expected: Fails at least once across repeated runs (duplicate starting points), demonstrating the race. It may occasionally pass by chance — that's expected for a race condition, not a reason to skip Step 3.

- [ ] **Step 3: Make `_rotate_candidates` atomic**

In `core/ai/ai_router.py`, change the import line near the top:

```python
from core.memory import load, save
```

to:

```python
from core.memory import load, save, update
```

Then replace the existing `_rotate_candidates` function body:

```python
def _rotate_candidates(task_type, candidates):
    if len(candidates) <= 1:
        return candidates

    state = load(ROTATION_STATE_FILE) or {}
    start = state.get(task_type, 0) % len(candidates)

    state[task_type] = (start + 1) % len(candidates)
    save(ROTATION_STATE_FILE, state)

    return candidates[start:] + candidates[:start]
```

with:

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

    update(ROTATION_STATE_FILE, mutate)

    start = captured["start"]
    return candidates[start:] + candidates[:start]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_ai_router.py -v`
Expected: PASS, including 10 repeated runs of the new concurrency test with no flakiness:
`for i in $(seq 10); do python3 -m pytest tests/test_ai_router.py -k test_rotate_candidates_gives_each_concurrent -q || break; done`

- [ ] **Step 5: Commit**

```bash
git add core/ai/ai_router.py tests/test_ai_router.py
git commit -m "Make provider rotation atomic to fix race under concurrent dispatch"
```

---

### Task 4: Generation discipline preamble

**Files:**
- Modify: `core/build_manager.py`
- Test: `tests/test_build_manager.py`

- [ ] **Step 1: Write the failing test**

Add to the end of `tests/test_build_manager.py`:

```python
def test_generation_prompt_includes_discipline_preamble():
    build = build_manager.create_build("todo-app", "desc", "/tmp/proj")

    prompt = build_manager._generation_prompt(build)

    assert prompt.startswith(build_manager.GENERATION_DISCIPLINE_PREAMBLE)
    assert "write tests" in build_manager.GENERATION_DISCIPLINE_PREAMBLE.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_build_manager.py -k test_generation_prompt_includes_discipline_preamble -v`
Expected: FAIL with `AttributeError: module 'core.build_manager' has no attribute 'GENERATION_DISCIPLINE_PREAMBLE'`

- [ ] **Step 3: Add the preamble and prepend it in `_generation_prompt`**

In `core/build_manager.py`, add this constant after `GENERATION_TIMEOUT = 600` (line 24), before the `BUILDS_FILE` comment block:

```python

# Every coding-agent instruction carries this regardless of which provider
# (claude / opencode / opencode_claude) ends up handling the build -- the
# same discipline this development workflow itself follows, applied at the
# point the work is actually produced rather than left to chance per-provider.
GENERATION_DISCIPLINE_PREAMBLE = (
    "Follow disciplined engineering practice: write tests for new "
    "behavior before or alongside the implementation, run them, and "
    "verify they actually pass before considering any part of this "
    "done. Do not claim something works without having run it.\n\n"
)
```

Then change the existing `_generation_prompt` function:

```python
def _generation_prompt(build):
    return (
        "The following architecture plan was reviewed and approved by the "
        "requester. Implement it now: write the code, and commit your work "
        "with git as you go.\n\n"
        f"Application: {build['name']}\n"
        f"Description: {build['description']}\n"
        + _template_context(build)
        + f"Approved plan:\n{build.get('plan') or ''}"
    )
```

to:

```python
def _generation_prompt(build):
    return (
        GENERATION_DISCIPLINE_PREAMBLE
        + "The following architecture plan was reviewed and approved by the "
        "requester. Implement it now: write the code, and commit your work "
        "with git as you go.\n\n"
        f"Application: {build['name']}\n"
        f"Description: {build['description']}\n"
        + _template_context(build)
        + f"Approved plan:\n{build.get('plan') or ''}"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_build_manager.py -v`
Expected: PASS (all tests, including pre-existing ones — this only adds a prefix, existing tests don't assert exact prompt equality)

- [ ] **Step 5: Commit**

```bash
git add core/build_manager.py tests/test_build_manager.py
git commit -m "Prepend TDD/verification discipline preamble to every generation instruction"
```

---

### Task 5: `CODE_REVIEW` gate — advisory Claude oversight

**Files:**
- Modify: `core/build_manager.py`
- Test: `tests/test_build_manager.py`

- [ ] **Step 1: Write the failing tests**

Add to the end of `tests/test_build_manager.py`:

```python
def test_code_review_is_skipped_when_claude_generated_the_build(monkeypatch):
    build = build_manager.create_build("todo-app", "Build a todo app", "/tmp/proj")
    build["generated_by"] = "claude"
    build["generation_result"] = {"files_changed": [], "commits": []}
    _force_status(build["id"], "GENERATING")

    monkeypatch.setattr(
        build_manager, "delegate",
        lambda description, **kwargs: {
            "provider": "claude", "task_type": "coding", "duration_ms": 10,
            "response": {"success": True, "response_text": "Done.", "files_changed": [], "commits": [], "tool_errors": []},
        },
    )
    monkeypatch.setattr(build_manager, "run_all_scans", lambda project_path: {"scanners": {}, "total_findings": 0, "highest_severity": None})

    build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "WAITING_FOR_DEPLOY_APPROVAL"
    assert updated["code_review"] == {"skipped": True, "reason": "generated by claude"}


def test_code_review_calls_claude_when_a_non_claude_provider_generated_the_build(monkeypatch):
    import core.ai_provider as ai_provider

    build = build_manager.create_build("todo-app", "Build a todo app", "/tmp/proj")
    _force_status(build["id"], "GENERATING")

    monkeypatch.setattr(
        build_manager, "delegate",
        lambda description, **kwargs: {
            "provider": "opencode", "task_type": "coding", "duration_ms": 10,
            "response": {"success": True, "response_text": "Done.", "files_changed": ["a.py"], "commits": [], "tool_errors": []},
        },
    )
    monkeypatch.setattr(build_manager, "run_all_scans", lambda project_path: {"scanners": {}, "total_findings": 0, "highest_severity": None})

    claude = ai_provider.get_provider("claude")
    monkeypatch.setitem(claude, "available_fn", lambda: True)
    monkeypatch.setitem(
        claude, "run_text_task",
        lambda prompt, timeout=60, project_path=None: "Looks reasonable, no concerns.",
    )

    build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    assert updated["status"] == "WAITING_FOR_DEPLOY_APPROVAL"
    assert updated["code_review"] == {
        "skipped": False, "reviewer": "claude", "findings": "Looks reasonable, no concerns.",
    }


def test_code_review_proceeds_anyway_when_claude_is_unavailable(monkeypatch):
    import core.ai_provider as ai_provider

    build = build_manager.create_build("todo-app", "Build a todo app", "/tmp/proj")
    _force_status(build["id"], "GENERATING")

    monkeypatch.setattr(
        build_manager, "delegate",
        lambda description, **kwargs: {
            "provider": "opencode", "task_type": "coding", "duration_ms": 10,
            "response": {"success": True, "response_text": "Done.", "files_changed": [], "commits": [], "tool_errors": []},
        },
    )
    monkeypatch.setattr(build_manager, "run_all_scans", lambda project_path: {"scanners": {}, "total_findings": 0, "highest_severity": None})

    claude = ai_provider.get_provider("claude")
    monkeypatch.setitem(claude, "available_fn", lambda: False)

    build_manager.advance_builds()

    updated = build_manager.get_build(build["id"])
    # Advisory only -- an unavailable reviewer must never stall the build.
    assert updated["status"] == "WAITING_FOR_DEPLOY_APPROVAL"
    assert updated["code_review"]["skipped"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_build_manager.py -k test_code_review -v`
Expected: FAIL — `test_code_review_is_skipped_when_claude_generated_the_build` fails on the `code_review` assertion (key doesn't exist yet, since `_run_code_review` doesn't exist and `_run_generation` still writes straight to `WAITING_FOR_DEPLOY_APPROVAL` without ever setting `build["code_review"]`); the other two fail the same way.

- [ ] **Step 3: Add the `CODE_REVIEW` state and implement `_run_code_review`**

In `core/build_manager.py`, add this import near the top, after the existing `from core.ai.ai_router import delegate, AllProvidersFailed` line:

```python
import core.ai_provider as ai_provider
```

Change the `GENERATING` line in `BUILD_TRANSITIONS` (currently `"GENERATING": ["TESTING", "SECURITY_REVIEW", "COMPLETED", "FAILED"],`) and add a new `CODE_REVIEW` entry right after it:

```python
    "GENERATING": ["CODE_REVIEW", "TESTING", "SECURITY_REVIEW", "COMPLETED", "FAILED"],
    "CODE_REVIEW": ["SECURITY_REVIEW", "FAILED"],
```

Add these two functions right before `_run_generation`:

```python
def _code_review_prompt(build):
    result = build.get("generation_result") or {}
    files_changed = result.get("files_changed") or []
    commits = result.get("commits") or []

    return (
        "Review the following code generation result for a build. This is "
        "an advisory review only -- you are not approving or blocking "
        "anything, a human makes that decision separately at deploy time. "
        "Note any real concerns (correctness, missing tests, security) "
        "briefly, or say it looks fine.\n\n"
        f"Application: {build['name']}\n"
        f"Description: {build['description']}\n"
        f"Files changed: {', '.join(files_changed) or 'none recorded'}\n"
        f"Commits: {'; '.join(c.get('message', '') for c in commits) or 'none recorded'}\n"
        f"Generated by: {build.get('generated_by')}\n"
    )


def _run_code_review(build):
    if build.get("generated_by") == "claude":
        # No value in Claude reviewing its own work.
        build["code_review"] = {"skipped": True, "reason": "generated by claude"}
    else:
        claude = ai_provider.get_provider("claude")
        try:
            if not claude["available_fn"]():
                raise RuntimeError("claude provider not available")

            findings = claude["run_text_task"](
                _code_review_prompt(build),
                timeout=PLANNING_TIMEOUT,
                project_path=build["project_path"],
            )
            build["code_review"] = {"skipped": False, "reviewer": "claude", "findings": findings}
        except Exception as error:
            # Advisory only -- an unavailable/failing reviewer must never
            # stall the pipeline, matching every other "must not stall Kai"
            # fallback pattern in this codebase.
            build["code_review"] = {"skipped": True, "reason": str(error)}

    transition(build, "SECURITY_REVIEW", BUILD_TRANSITIONS)
    build["security_report"] = run_all_scans(build["project_path"])
    transition(build, "WAITING_FOR_DEPLOY_APPROVAL", BUILD_TRANSITIONS)
    _create_deploy_approval(build)
```

Then change the tail of `_run_generation` — replace:

```python
    # Security findings are surfaced for a human to review via
    # WAITING_FOR_DEPLOY_APPROVAL, never used to silently auto-fail the
    # build -- the same human-in-the-loop pattern as every other approval
    # gate here.
    transition(build, "SECURITY_REVIEW", BUILD_TRANSITIONS)
    build["security_report"] = run_all_scans(build["project_path"])
    transition(build, "WAITING_FOR_DEPLOY_APPROVAL", BUILD_TRANSITIONS)
    _create_deploy_approval(build)
```

with:

```python
    # Claude's review is advisory and, like the security scan it precedes,
    # never used to silently auto-fail the build -- the same human-in-the-
    # loop pattern as every other approval gate here.
    transition(build, "CODE_REVIEW", BUILD_TRANSITIONS)
    _run_code_review(build)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_build_manager.py -v`
Expected: PASS (all tests, including every pre-existing generation test — they all use `provider: "claude"`, which takes the skip branch and reaches `WAITING_FOR_DEPLOY_APPROVAL` exactly as before)

- [ ] **Step 5: Commit**

```bash
git add core/build_manager.py tests/test_build_manager.py
git commit -m "Add advisory Claude code-review gate between generation and security review"
```

---

### Task 6: Parallel dispatch via `ThreadPoolExecutor`

**Files:**
- Modify: `core/build_manager.py`
- Test: `tests/test_build_manager.py`

- [ ] **Step 1: Write the failing tests**

Add to the end of `tests/test_build_manager.py`:

```python
def test_persist_build_only_updates_the_matching_record(tmp_path):
    build_a = build_manager.create_build("app-a", "desc", str(tmp_path / "proj-a"))
    build_b = build_manager.create_build("app-b", "desc", str(tmp_path / "proj-b"))

    build_a["status"] = "GENERATING"
    build_manager._persist_build(build_a)

    reloaded_a = build_manager.get_build(build_a["id"])
    reloaded_b = build_manager.get_build(build_b["id"])
    assert reloaded_a["status"] == "GENERATING"
    assert reloaded_b["status"] == "REQUESTED"


def test_advance_builds_with_no_ready_builds_is_a_noop():
    build = build_manager.create_build("todo-app", "desc", "/tmp/proj")
    _force_status(build["id"], "COMPLETED")

    result = build_manager.advance_builds()

    assert result[0]["status"] == "COMPLETED"


def test_advance_builds_processes_two_generating_builds_concurrently_without_cross_talk(monkeypatch, tmp_path):
    import core.ai_provider as ai_provider

    # Keep the new code-review gate deterministic -- no real network/CLI call.
    claude = ai_provider.get_provider("claude")
    monkeypatch.setitem(claude, "available_fn", lambda: False)

    build_a = build_manager.create_build("app-a", "Build app A", str(tmp_path / "proj-a"))
    build_b = build_manager.create_build("app-b", "Build app B", str(tmp_path / "proj-b"))
    _force_status(build_a["id"], "GENERATING")
    _force_status(build_b["id"], "GENERATING")

    def fake_delegate(description, **kwargs):
        project_path = kwargs.get("project_path")
        provider = "claude" if project_path.endswith("proj-a") else "opencode"
        return {
            "provider": provider, "task_type": "coding", "duration_ms": 10,
            "response": {"success": True, "response_text": "Done.", "files_changed": [], "commits": [], "tool_errors": []},
        }

    monkeypatch.setattr(build_manager, "delegate", fake_delegate)
    monkeypatch.setattr(build_manager, "run_all_scans", lambda project_path: {"scanners": {}, "total_findings": 0, "highest_severity": None})

    build_manager.advance_builds()

    updated_a = build_manager.get_build(build_a["id"])
    updated_b = build_manager.get_build(build_b["id"])

    assert updated_a["generated_by"] == "claude"
    assert updated_b["generated_by"] == "opencode"
    assert updated_a["status"] == "WAITING_FOR_DEPLOY_APPROVAL"
    assert updated_b["status"] == "WAITING_FOR_DEPLOY_APPROVAL"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_build_manager.py -k "test_persist_build or test_advance_builds_with_no_ready_builds or test_advance_builds_processes_two_generating" -v`
Expected: FAIL — `AttributeError: module 'core.build_manager' has no attribute '_persist_build'` for the first, and the concurrency test fails because today's sequential `advance_builds()` still passes (it would actually already pass functionally, since sequential processing also avoids cross-talk) — the point of this task isn't to fix a failure in that test, it's to prove parallel dispatch still behaves correctly once introduced. Confirm the first two tests fail before continuing; the third should already pass and must keep passing after Step 3.

- [ ] **Step 3: Implement `_persist_build`, `_advance_one_build`, and rewrite `advance_builds()`**

In `core/build_manager.py`, add this import at the very top of the file, before `from core.memory import load, save`:

```python
from concurrent.futures import ThreadPoolExecutor
```

Add this constant right after `BUILDS_FILE = "builds.json"` (line 32):

```python

# opencode and opencode_claude share the same OpenCode Zen credential, so 2
# genuinely independent concurrent workers (e.g. claude + one opencode-family
# provider) is the safe ceiling before providers start contending with
# themselves for the same account.
MAX_CONCURRENT_BUILDS = 2

_ACTIONABLE_STATUSES = {"REQUESTED", "PLANNING", "GENERATING", "CODE_REVIEW", "DEPLOYING"}
```

Replace the entire existing `advance_builds()` function:

```python
def advance_builds():
    builds = load_builds()

    for build in builds:
        status = build.get("status")

        if status == "REQUESTED":
            transition(build, "PLANNING", BUILD_TRANSITIONS)
            _run_planning(build)
        elif status == "PLANNING":
            _run_planning(build)
        elif status == "GENERATING":
            _run_generation(build)
        elif status == "DEPLOYING":
            _run_deployment(build)

    save_builds(builds)

    return builds
```

with:

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

    from core.memory import update as _update_memory
    _update_memory(BUILDS_FILE, mutate)


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
            # Defensive fallback only -- _run_generation cascades straight
            # through _run_code_review in the normal path, so a build
            # shouldn't usually be found sitting here between cycles.
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

Note: the `from core.memory import update as _update_memory` import inside `_persist_build` is deliberately local, not moved to the top-level `from core.memory import load, save` line — `update` is only used in this one function, and keeping it local avoids a naming collision with the module-level `_update` helper already defined earlier in this file (line 125) for a different purpose (single-field mutation via `get_build`/`_update`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_build_manager.py -v`
Expected: PASS (all tests, including every pre-existing single-build test — `advance_builds()` with exactly one ready build still processes it and returns builds with that one build's final state, since `ThreadPoolExecutor(max_workers=2)` handles a single submitted item exactly the same as processing it directly)

- [ ] **Step 5: Commit**

```bash
git add core/build_manager.py tests/test_build_manager.py
git commit -m "Run ready builds concurrently via ThreadPoolExecutor"
```

---

### Task 7: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `python3 -m pytest -q`
Expected: All tests pass (517 existing + tests added in Tasks 1-6), zero failures, zero errors.

- [ ] **Step 2: Run the new concurrency-sensitive tests repeatedly to check for flakiness**

Run:
```bash
for i in $(seq 20); do
  python3 -m pytest tests/test_ai_router.py -k test_rotate_candidates_gives_each_concurrent -q \
    tests/test_build_manager.py -k test_advance_builds_processes_two_generating \
    tests/test_memory_manager.py -k test_update_is_atomic \
    || { echo "FLAKY on iteration $i"; break; }
done
```
Expected: All 20 iterations pass with no failure. If any iteration fails, that's a real race — do not proceed; go back and re-examine the lock scope in the failing area.

- [ ] **Step 3: Confirm no leftover `.tmp` lock artifacts**

Run: `find memory -name "*.tmp.*" 2>/dev/null; echo "done"`
Expected: No output before `done` — `_write_locked`'s `finally` cleanup (already existing code, untouched by this plan) always removes temp files even under concurrent access.
