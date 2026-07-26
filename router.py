from agents.claude_agent import run as claude
from agents.codex_agent import run as codex


coding_words = (
    "python",
    "javascript",
    "typescript",
    "java",
    "go",
    "rust",
    "c++",
    "code",
    "program",
    "script",
    "bug",
    "debug",
    "refactor",
    "unit test",
    "pytest",
    "docker",
    "dockerfile",
    "container",
    "kubernetes",
    "proxmox",
    "linux",
    "bash",
)


def route(task):

    try:

        if any(x in task.lower() for x in coding_words):
            return codex(task)

        response = claude(task)

        if (
            "Not logged in" in response
            or "weekly limit" in response.lower()
            or "Please run /login" in response
        ):
            return codex(task)

        return response

    except Exception:
        return codex(task)


if __name__ == "__main__":
    print(route("Analyze my Docker architecture"))
