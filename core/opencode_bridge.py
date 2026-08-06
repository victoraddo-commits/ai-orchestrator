"""K5: OpenCode coding-agent bridge.

OpenCode (github.com/sst/opencode) is a real, separate agentic coding
harness -- its own sandboxed file-write/tool-use/git-commit loop, distinct
from CloudCLI's Claude Agent SDK integration. `opencode run` spins up its own
local server, runs one task non-interactively, and exits -- no long-lived
server process to manage, no shared-workspace risk, since `--dir` scopes
every invocation to a specific directory (the same isolated-clone pattern
core.roadmap_manager already uses for Claude, see K3).

Deliberately defaults to a non-Claude model rather than a Claude model
available through the same OpenCode Zen gateway -- routing "OpenCode" tasks
to Claude-through-a-different-door would add zero real diversification and
defeat the point of reducing Claude-credit dependency. Was minimax-m2.7 until
2026-07-29 (paused after two live incidents of hallucinated tool-call markup
on the unrelated text_task path -- see core.ai.ai_router's ROLE_PROVIDERS
comment; this coding-agent path never actually showed that failure, but was
paused right alongside it per user directive). Replaced with deepseek-v4-pro,
confirmed live against the same OpenCode Zen credential (full account access,
not narrowly scoped -- also authenticated fine for opencode/claude-fable-5).

13T's usage-history review (2026-07-29) then found minimax-m2.7 to be 3/3 on
this coding-agent path against deepseek-v4-pro's 2/4, so minimax is routable
again -- but as its own explicitly pinned provider ("opencode_minimax", see
ai_provider.OPENCODE_MINIMAX_MODEL), not by changing this default. 3 attempts
is below core.ai.provider_evidence.MIN_SAMPLE_SIZE, which is enough to earn a
slot in the rotation and nothing more; deepseek-v4-pro stays the default for
an unpinned run.

13J (2026-08-02): replaced plain subprocess.run(timeout=N) with a
SubprocessWatchdog that enforces wall-clock and idle-CPU timeouts, runs the
subprocess in its own process group so children are killed on timeout, and
captures partial output on kill. The 2026-08-02 live incident was a 4+ hour
near-zero-CPU subprocess hang blocking the entire scheduler -- a plain
subprocess.run timeout fires eventually but leaves child processes alive, and
a stuck process can still deadlock the scheduler's ThreadPoolExecutor.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

from core.subprocess_watchdog import SubprocessWatchdog, GenerationTimeoutError


OPENCODE_DEFAULT_MODEL = "opencode/deepseek-v4-pro"
OPENCODE_AUTH_PATH = Path.home() / ".local" / "share" / "opencode" / "auth.json"
DEFAULT_TIMEOUT = 600
DEFAULT_IDLE_CPU_TIMEOUT = int(os.environ.get(
    "GENERATION_SUBPROCESS_IDLE_CPU_TIMEOUT_SECONDS", "300"
))
DEFAULT_GRACE_KILL_SECONDS = int(os.environ.get(
    "GENERATION_SUBPROCESS_GRACE_KILL_SECONDS", "5"
))


def credential_exists(key):
    """True when the opencode CLI is on PATH and its credential store holds an
    entry under `key` — e.g. "omniroute" for the omniroute gateway config."""
    if shutil.which("opencode") is None:
        return False

    try:
        auth = json.loads(OPENCODE_AUTH_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return False

    return key in auth


def _run_opencode_process(project_path, instruction, model, timeout):
    opencode_path = shutil.which("opencode") or "opencode"
    wd = SubprocessWatchdog(
        [opencode_path, "run", instruction, "--dir", project_path, "--model", model, "--format", "json", "--auto"],
        wall_timeout_seconds=timeout,
        idle_cpu_seconds=DEFAULT_IDLE_CPU_TIMEOUT,
        grace_kill_seconds=DEFAULT_GRACE_KILL_SECONDS,
    )
    return wd.run()


def _parse_events(stdout):
    events = []

    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    return events


def run_coding_task(project_path, instruction, model=None, timeout=DEFAULT_TIMEOUT):
    try:
        result = _run_opencode_process(project_path, instruction, model or OPENCODE_DEFAULT_MODEL, timeout)
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "response_text": "",
            "files_changed": [],
            "commits": [],
            "tool_errors": [{"tool": None, "content": f"opencode run exceeded {timeout}s wall-clock timeout"}],
            "cost": None,
        }

    events = _parse_events(result.stdout)

    response_text_parts = []
    files_changed = []
    commits = []
    tool_errors = []
    # 13W: real per-call cost. OpenCode's step_finish events carry the actual
    # billed cost per step (confirmed live, e.g. cost: 0.0139422, and in the
    # CLI's own session store where every step-finish part has a top-level
    # "cost") -- sum them for the run's total. Stays None if no step reported
    # one: never fabricated from token counts.
    cost = None

    for event in events:
        etype = event.get("type")
        part = event.get("part", {})

        if etype == "text":
            response_text_parts.append(part.get("text", ""))
            continue

        # The stream and the session store spell part types differently
        # (tool_use vs tool), so accept both separators for step finish.
        if etype in ("step_finish", "step-finish") or part.get("type") == "step-finish":
            step_cost = part.get("cost", event.get("cost"))
            if isinstance(step_cost, (int, float)) and not isinstance(step_cost, bool):
                cost = (cost or 0.0) + step_cost
            continue

        if etype != "tool_use":
            continue

        state = part.get("state", {})
        tool = part.get("tool")

        if state.get("status") == "error":
            tool_errors.append({"tool": tool, "content": state.get("error") or state.get("output", "")})
            continue

        tool_input = state.get("input") or {}

        if tool == "write":
            file_path = tool_input.get("filePath")
            if file_path and file_path not in files_changed:
                files_changed.append(file_path)

        elif tool == "bash":
            command = tool_input.get("command", "")
            if "git commit" in command:
                commits.append({"sha": None, "message": (state.get("output") or "").strip()})

    success = result.returncode == 0 and not tool_errors

    # Confirmed live 2026-07-30: a top-level CLI failure (auth/billing/
    # network -- the process exits non-zero before ever emitting a
    # tool_use event) left tool_errors empty, so the real reason was
    # silently discarded here and callers only ever saw the generic
    # "generation did not succeed" fallback (core.ai.ai_router).  That
    # made a real credit-exhaustion message indistinguishable from any
    # other opaque failure -- neither a human nor _classify_failure_reason
    # could tell them apart. subprocess.run's capture_output=True already
    # captures stderr/stdout; surface it instead of dropping it.
    if not success and not tool_errors:
        detail = (result.stderr or "").strip() or (result.stdout or "").strip()
        if detail:
            tool_errors = [{"tool": None, "content": detail}]

    return {
        "success": success,
        "response_text": "".join(response_text_parts),
        "files_changed": files_changed,
        "commits": commits,
        "tool_errors": tool_errors,
        "cost": cost,
    }
