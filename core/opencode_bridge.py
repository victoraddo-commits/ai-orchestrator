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
"""

import json
import shutil
import subprocess


OPENCODE_DEFAULT_MODEL = "opencode/deepseek-v4-pro"
DEFAULT_TIMEOUT = 600


def _run_opencode_process(project_path, instruction, model, timeout):
    opencode_path = shutil.which("opencode") or "opencode"
    return subprocess.run(
        [opencode_path, "run", instruction, "--dir", project_path, "--model", model, "--format", "json", "--auto"],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


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
        }

    events = _parse_events(result.stdout)

    response_text_parts = []
    files_changed = []
    commits = []
    tool_errors = []

    for event in events:
        etype = event.get("type")
        part = event.get("part", {})

        if etype == "text":
            response_text_parts.append(part.get("text", ""))
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

    return {
        "success": success,
        "response_text": "".join(response_text_parts),
        "files_changed": files_changed,
        "commits": commits,
        "tool_errors": tool_errors,
    }
