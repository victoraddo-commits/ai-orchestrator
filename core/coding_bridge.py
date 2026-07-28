import json
import os
import subprocess
from pathlib import Path

import requests


API_KEY_PATH = Path.home() / ".ai-orchestrator" / "cloudcli_api_key"

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


class CloudCLINotConfigured(Exception):
    """Raised when the CloudCLI bridge API key hasn't been provisioned yet
    (see the one-time setup that creates an 'ai-orchestrator-bridge' API key
    in CloudCLI and writes it to API_KEY_PATH)."""


def _cloudcli_base_url():
    return os.environ.get("CLOUDCLI_BASE_URL", "http://127.0.0.1:3002")


def _load_bridge_api_key():
    if not API_KEY_PATH.exists():
        raise CloudCLINotConfigured(
            f"No CloudCLI API key found at {API_KEY_PATH}. "
            "Provision one via CloudCLI's API key management and save it there."
        )

    key = API_KEY_PATH.read_text().strip()

    if not key:
        raise CloudCLINotConfigured(f"CloudCLI API key at {API_KEY_PATH} is empty")

    return key


def _parse_sse_data_line(line):
    if not line.startswith("data: "):
        return None

    payload = line[len("data: "):]

    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def _stream_agent_events(project_path, instruction, session_id=None, model=None, timeout=300):
    """Calls CloudCLI's existing headless coding-agent endpoint (POST
    /api/agent) and yields each streamed NormalizedMessage event as a dict.
    This reuses CloudCLI's own Claude Agent SDK integration end-to-end --
    AI Orchestrator never runs its own coding agent."""

    body = {
        "projectPath": project_path,
        "message": instruction,
        "stream": True,
    }

    if session_id:
        body["sessionId"] = session_id

    if model:
        body["model"] = model

    response = requests.post(
        f"{_cloudcli_base_url()}/api/agent",
        headers={
            "X-Api-Key": _load_bridge_api_key(),
            "Content-Type": "application/json",
        },
        json=body,
        stream=True,
        timeout=timeout,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"CloudCLI /api/agent request failed with status {response.status_code}: {response.text[:500]}"
        )

    for raw_line in response.iter_lines(decode_unicode=True):
        event = _parse_sse_data_line(raw_line or "")
        if event is not None:
            yield event


def _git_head(project_path):
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project_path,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return None

    return result.stdout.strip()


def _git_commits_between(project_path, before, after):
    if not before or not after or before == after:
        return []

    result = subprocess.run(
        ["git", "log", f"{before}..{after}", "--pretty=format:%H %s"],
        cwd=project_path,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0 or not result.stdout.strip():
        return []

    commits = []
    for line in result.stdout.strip().splitlines():
        sha, _, message = line.partition(" ")
        commits.append({"sha": sha, "message": message})

    return commits


def _git_uncommitted_files(project_path):
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=project_path,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return []

    files = []
    for line in result.stdout.splitlines():
        path = line[3:].strip()
        if path:
            files.append(path)

    return files


def run_coding_task(project_path, instruction, session_id=None, model=None, timeout=300):
    """Drives CloudCLI's existing headless Claude Agent SDK integration to
    perform one coding task in `project_path`, and returns a structured
    result. The SDK's own streamed events don't carry a files-changed/
    commits-made summary (see Phase 12B audit), so this cross-checks the
    agent's own reported tool_use writes against a before/after git
    inspection of the worktree -- the same "thin post-run git inspection"
    approach CloudCLI's own /api/agent route uses for branch/PR creation."""

    before_head = _git_head(project_path)

    result_session_id = session_id
    response_text_parts = []
    files_touched = []
    tool_errors = []
    success = False
    aborted = False

    for event in _stream_agent_events(
        project_path=project_path,
        instruction=instruction,
        session_id=session_id,
        model=model,
        timeout=timeout,
    ):
        kind = event.get("kind")

        if kind == "session_created":
            result_session_id = event.get("sessionId") or event.get("session_id") or result_session_id

        elif kind == "text":
            response_text_parts.append(event.get("text", ""))

        elif kind == "tool_use":
            if event.get("toolName") in WRITE_TOOLS:
                file_path = (event.get("toolInput") or {}).get("file_path")
                if file_path and file_path not in files_touched:
                    files_touched.append(file_path)

        elif kind == "tool_result":
            if event.get("isError"):
                tool_errors.append({
                    "tool": event.get("toolName"),
                    "content": event.get("content"),
                })

        elif kind == "error":
            tool_errors.append({
                "tool": None,
                "content": event.get("content"),
            })

        elif kind == "complete":
            success = bool(event.get("success", event.get("exitCode") == 0))
            aborted = bool(event.get("aborted", False))

    after_head = _git_head(project_path)
    commits = _git_commits_between(project_path, before_head, after_head)
    uncommitted_files = _git_uncommitted_files(project_path)

    files_changed = sorted(set(files_touched) | set(uncommitted_files))

    return {
        "success": success,
        "aborted": aborted,
        "session_id": result_session_id,
        "response_text": "".join(response_text_parts),
        "files_changed": files_changed,
        "commits": commits,
        "tool_errors": tool_errors,
    }
