import subprocess


def run(prompt):

    result = subprocess.run(
        [
            "claude",
            "-p",
            prompt
        ],
        capture_output=True,
        text=True
    )

    return result.stdout


if __name__ == "__main__":
    print(run("Analyze this system architecture"))
