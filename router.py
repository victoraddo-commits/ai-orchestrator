from agents.claude_agent import run as claude
from agents.codex_agent import run as codex


CODE_WORDS = (
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
    "function",
    "class",
)


ARCHITECTURE_WORDS = (
    "architecture",
    "design",
    "proxmox",
    "docker architecture",
    "system design",
    "security",
    "network",
    "infrastructure",
    "deployment",
)


def route(task):

    text = task.lower()

    if any(word in text for word in CODE_WORDS):
        return codex(task)

    if any(word in text for word in ARCHITECTURE_WORDS):
        return claude(task)

    return claude(task)


if __name__ == "__main__":

    task = input("Task: ")

    print(route(task))
