from agents.claude_agent import run as claude
from agents.codex_agent import run as codex


def route(task):

    coding_words = [
        "code",
        "python",
        "javascript",
        "refactor",
        "bug"
    ]


    if any(x in task.lower() for x in coding_words):
        return codex(task)

    return claude(task)



if __name__ == "__main__":

    print(
        route(
            "Analyze my Docker architecture"
        )
    )
