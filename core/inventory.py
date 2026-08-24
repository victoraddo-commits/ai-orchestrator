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


    # JARVIS P13: host resource metrics for the predictive engine.
    # health_observatory extracts snapshot['host']['cpu_percent'] etc.; the
    # scan never provided them, so host_*_pct series were all-zero since
    # inception. psutil is best-effort — absence must not break scan().
    try:

        import psutil

        mem = psutil.virtual_memory()

        du = psutil.disk_usage("/")

        inventory["host"] = {

            "hostname": inventory["hostname"],

            "cpu_percent": psutil.cpu_percent(interval=None),

            "memory_percent": mem.percent,

            "disk_percent": round(du.used / du.total * 100, 1),

        }

    except Exception:

        pass




    save(
        "system_state.json",
        inventory
    )


    return inventory



if __name__ == "__main__":

    print(collect())
