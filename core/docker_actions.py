import shutil
import subprocess
from datetime import datetime

from core.security import enforce_action_is_safe


ALLOWED_ACTIONS = (
    "restart_container",
)


def docker_available():
    """True only when a docker CLI is present on this host.

    CT111 (the orchestrator runner) deliberately has no docker. Every
    Docker-touching operation must degrade to a clean "unknown"/unsupported
    result instead of raising FileNotFoundError from subprocess.run -- that
    uncaught error aborted the whole orchestrator cycle on every tick
    (2026-09-18 audit, P0 scheduler wedge).
    """
    return shutil.which("docker") is not None


def container_exists(service):

    if not docker_available():

        return False

    result = subprocess.run(
        [
            "docker",
            "inspect",
            service
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    return result.returncode == 0



def container_status(service):

    if not docker_available():

        return "unknown"

    result = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            "{{.State.Status}}",
            service
        ],
        capture_output=True,
        text=True
    )


    if result.returncode != 0:

        return "unknown"


    return result.stdout.strip()



def restart_container(service):

    if not container_exists(service):

        return {
            "timestamp": datetime.now().isoformat(),
            "service": service,
            "action": "restart_container",
            "status": "failed",
            "reason": "container not found"
        }


    before = container_status(service)


    result = subprocess.run(
        [
            "docker",
            "restart",
            service
        ],
        capture_output=True,
        text=True
    )


    after = container_status(service)


    if result.returncode == 0:

        status = "success"

    else:

        status = "failed"



    return {

        "timestamp": datetime.now().isoformat(),

        "service": service,

        "action": "restart_container",

        "before": before,

        "after": after,

        "status": status

    }



def execute_docker_action(action, service):

    if action not in ALLOWED_ACTIONS:

        return {
            "status": "blocked",
            "reason": "action not allowed"
        }


    if action == "restart_container":

        return restart_container(service)



if __name__ == "__main__":

    print(
        execute_docker_action(
            "restart_container",
            "pulse"
        )
    )
def execute_action(action, service):

    # Security classification still raises SecurityViolation -- enforcement
    # must never be silently swallowed. Only the execution itself is
    # fail-safe so a broken docker call cannot abort the cycle.
    enforce_action_is_safe(action, f"{action} on {service}")

    if action == "restart_container":

        try:

            return restart_container(service)

        except Exception as error:

            return {
                "timestamp": datetime.now().isoformat(),
                "service": service,
                "action": action,
                "status": "failed",
                "reason": f"{type(error).__name__}: {error}"
            }

    return {
        "timestamp": datetime.now().isoformat(),
        "service": service,
        "action": action,
        "status": "blocked"
    }
