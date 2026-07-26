import subprocess
import json
from datetime import datetime


def run_command(command):

    try:

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=10
        )

        return result.stdout.strip()

    except Exception as e:

        return str(e)



def inspect_containers():

    output = run_command(
        [
            "docker",
            "ps",
            "--format",
            "{{json .}}"
        ]
    )


    containers = []


    for line in output.splitlines():

        try:

            containers.append(
                json.loads(line)
            )

        except json.JSONDecodeError:

            pass


    return {
        "timestamp": datetime.now().isoformat(),
        "count": len(containers),
        "containers": containers
    }



if __name__ == "__main__":

    print(
        json.dumps(
            inspect_containers(),
            indent=2
        )
    )
