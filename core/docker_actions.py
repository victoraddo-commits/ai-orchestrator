import subprocess
from datetime import datetime


ALLOWED_ACTIONS = (
    "restart_container",
)



def container_exists(service):

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
