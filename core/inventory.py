import platform
import subprocess
import shutil
from datetime import datetime

from core.memory import save


def run_command(command):

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=5
        )

        return result.stdout.strip()

    except Exception as e:
        return str(e)



def collect():

    inventory = {

        "hostname": platform.node(),

        "os": platform.platform(),

        "cpu": platform.processor(),

        "docker": {},

        "timestamp": datetime.now().isoformat()

    }


    if shutil.which("docker"):

        inventory["docker"]["available"] = True

        inventory["docker"]["containers"] = run_command(
            [
                "docker",
                "ps",
                "--format",
                "{{.Names}}"
            ]
        ).splitlines()

    else:

        inventory["docker"]["available"] = False



    save(
        "system_state.json",
        inventory
    )


    return inventory



if __name__ == "__main__":

    print(collect())
