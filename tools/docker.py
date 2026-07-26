import subprocess


def containers():

    return subprocess.check_output(
        [
            "docker",
            "ps"
        ],
        text=True
    )


if __name__ == "__main__":
    print(containers())
