import subprocess


def run(prompt):

    result = subprocess.run(
        [
            "codex",
            "exec",
            prompt
        ],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        return result.stderr

    return result.stdout


if __name__ == "__main__":
    print(run("Review this Python project"))
