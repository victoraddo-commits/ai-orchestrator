from core.docker_observer import inspect_containers


def check_services():

    result = inspect_containers()

    findings = []


    for container in result.get("containers", []):

        name = container.get(
            "Names",
            "unknown"
        )

        state = container.get(
            "State",
            ""
        )


        if state != "running":

            findings.append({

                "severity": "critical",

                "service": name,

                "issue": f"Container state: {state}"

            })


    return findings


if __name__ == "__main__":

    print(check_services())
