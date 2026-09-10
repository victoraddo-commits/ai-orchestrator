"""Local coding bridge — text-to-code generation using Kai's local Ollama models.

The KAI MODEL TEAM assigns coding roles to local Ollama models on VM104
(kai.coder.fast = Qwen2.5-Coder-7B via ``kai-coder:7b``, and kai.brain =
GLM-4.7-Flash via ``kai-brain:latest``). Those models are text-completion
models — they have no agentic tool-use loop of their own — so this bridge is
the deterministic harness that turns their output into real file writes and
git commits, satisfying the same ``run_coding_task(project_path, instruction,
timeout) -> dict`` contract as :mod:`core.coding_bridge` (which drives
CloudCLI's Claude Agent SDK).

The model is asked to emit every file as a fenced code block whose opening
info string is the file's project-relative path. The harness parses those
blocks, writes them (rejecting any path that escapes the project root), and
commits the result. A run that produces no parseable files is a failure, not
a no-op, so the router's fallback can try the next coding candidate.
"""

import os
import re
import subprocess

OLLAMA_URL = os.environ.get("KAI_OLLAMA_URL", "http://localhost:11434")

# Opening fence: ``` or ~~~, with an optional info string on the same line.
_FENCE_OPEN = re.compile(r"^(```+|~~~+)\s*(\S.*)?$")

# Info strings we treat as file paths rather than language tags. A path is
# accepted if it contains a "/" (nested), matches a known code extension, or
# is a well-known filename with no extension.
_CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs", ".json", ".md",
    ".txt", ".sh", ".bash", ".yml", ".yaml", ".toml", ".cfg", ".ini",
    ".html", ".htm", ".css", ".scss", ".less", ".go", ".rs", ".java", ".c",
    ".h", ".cpp", ".hpp", ".sql", ".env", ".conf", ".service", ".xml",
    ".gradle", ".kt", ".swift", ".rb", ".php", ".vue", ".svelte",
    ".dockerfile", ".gitignore", ".lock",
}
_SPECIAL_FILENAMES = {
    "dockerfile", "makefile", "readme", "license", "requirements",
    "gemfile", "rakefile", "procfile", "justfile", "changelog", "authors",
}

# System prompt that pins the model to the parseable file-block convention.
_CODING_SYSTEM = (
    "You are Kai's local coding worker. Implement the task by producing "
    "complete file contents. For each file you create or modify, emit a "
    "fenced code block whose opening fence line is the file's project-"
    "relative path — with NO language tag. Example:\n\n"
    "```src/main.py\n"
    "print('hello')\n"
    "```\n\n"
    "Rules:\n"
    "- The fence info string MUST be the file path (e.g. src/main.py), never "
    "a language name like python or bash.\n"
    "- Output every file the task needs, with complete contents (do not "
    "abbreviate with comments like '... rest unchanged').\n"
    "- Do not wrap the whole answer in an outer fence.\n"
    "- Put nothing outside the file blocks except at most one short summary "
    "line at the very end.\n"
)


def _looks_like_path(info):
    info = (info or "").strip()
    if not info:
        return False
    if "/" in info:
        return True
    lower = info.lower()
    if lower in _SPECIAL_FILENAMES:
        return True
    _, ext = os.path.splitext(info)
    return ext.lower() in _CODE_EXTENSIONS


def parse_file_blocks(text):
    """Extract ``[(rel_path, content), ...]`` from a model response.

    Returns an empty list when nothing parseable is present, so callers can
    treat a fence-free response as a failed generation rather than a silent
    no-op.
    """
    if not text:
        return []

    lines = text.splitlines()
    files = []
    i = 0
    n = len(lines)

    while i < n:
        m = _FENCE_OPEN.match(lines[i])
        if not m:
            i += 1
            continue

        fence = m.group(1)
        info = m.group(2)
        j = i + 1
        body = []
        closed = False
        while j < n:
            stripped = lines[j].strip()
            if stripped.startswith(fence) and set(stripped) <= set(fence):
                closed = True
                break
            body.append(lines[j])
            j += 1

        if closed and _looks_like_path(info):
            files.append((info.strip(), "\n".join(body) + "\n"))

        i = j + 1

    return files


def _resolve_target(root, rel):
    """Resolve a project-relative path against ``root``, rejecting escapes.

    Absolute paths (POSIX ``/…`` and Windows ``C:\\…``) are rejected outright
    rather than silently rewritten as relative — a model must only ever emit
    project-relative paths.
    """
    rel = (rel or "").strip()
    if not rel:
        return None
    if rel.startswith("/") or rel.startswith("\\"):
        return None
    if len(rel) >= 2 and rel[1] == ":" and rel[0].isalpha():
        return None
    target = os.path.normpath(os.path.join(root, rel))
    if target != root and not target.startswith(root + os.sep):
        return None
    return target


def _commit(root, message):
    """Commit all changes under ``root``. Returns a list of commit dicts."""
    try:
        add = subprocess.run(
            ["git", "add", "-A"], cwd=root,
            capture_output=True, text=True, timeout=60,
        )
        if add.returncode != 0:
            return []
        commit = subprocess.run(
            ["git", "commit", "-m", message], cwd=root,
            capture_output=True, text=True, timeout=60,
        )
        if commit.returncode != 0:
            return []
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root,
            capture_output=True, text=True, timeout=30,
        )
        sha = head.stdout.strip()
        if not sha:
            return []
        return [{"sha": sha, "message": message}]
    except (subprocess.SubprocessError, OSError):
        return []


def _commit_message(instruction):
    text = (instruction or "").strip()
    if not text:
        return "Kai: implement changes"
    first_line = text.splitlines()[0].strip()
    return (first_line[:70] or "Kai: implement changes")


def run_coding_task(project_path, instruction, model="kai-coder:7b", timeout=1200):
    """Run one coding task with a local Ollama model.

    Generates code via the model, writes the emitted files into
    ``project_path``, commits them, and returns the coding_bridge-compatible
    result shape. Raises RuntimeError only when the model itself cannot be
    reached; a model that returns no parseable files is a returned failure
    (``success=False``) so the router falls through to the next candidate.
    """
    import requests

    prompt = _CODING_SYSTEM + "\n\nTask:\n" + instruction

    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.1, "top_p": 0.95},
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        text = resp.json().get("response", "")
    except Exception as error:
        raise RuntimeError(f"local coding model ({model}) call failed: {error}")

    files = parse_file_blocks(text)
    if not files:
        return {
            "success": False,
            "aborted": False,
            "session_id": None,
            "response_text": text,
            "files_changed": [],
            "commits": [],
            "tool_errors": [
                {"tool": None,
                 "content": "model returned no parseable file blocks"}
            ],
        }

    root = os.path.abspath(project_path)
    written = []
    for rel, content in files:
        target = _resolve_target(root, rel)
        if target is None:
            continue
        os.makedirs(os.path.dirname(target) or root, exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(content)
        written.append(rel)

    if not written:
        return {
            "success": False,
            "aborted": False,
            "session_id": None,
            "response_text": text,
            "files_changed": [],
            "commits": [],
            "tool_errors": [
                {"tool": None,
                 "content": "all file blocks were rejected (path escape or empty)"}
            ],
        }

    commits = _commit(root, _commit_message(instruction))

    return {
        "success": True,
        "aborted": False,
        "session_id": None,
        "response_text": text,
        "files_changed": written,
        "commits": commits,
        "tool_errors": [],
    }
