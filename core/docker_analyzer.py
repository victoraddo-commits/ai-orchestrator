from core.docker_observer import inspect_containers


def analyze_docker():

    containers = inspect_containers()

    findings = []


    for container in containers["containers"]:

        name = container.get(
            "Names",
            "unknown"
        )

        state = container.get(
            "State",
            ""
        )

        health = container.get(
            "HealthStatus",
            ""
        )


        if state != "running":

            findings.append({

                "severity": "critical",

                "service": name,

                "issue": "Container stopped"

            })


        elif health == "unhealthy":

            findings.append({

                "severity": "warning",

                "service": name,

                "issue": "Container unhealthy"

            })


    return findings



if __name__ == "__main__":

    print(analyze_docker())
