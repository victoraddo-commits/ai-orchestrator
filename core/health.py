from core.memory import load
from core.docker_analyzer import analyze_docker


EXPECTED_SERVICES = (
    "pulse",
    "proxdash-backend",
    "proxdash-frontend",
    "mattermost",
)


def analyze():

    findings = []

    state = load("system_state.json")

    docker = state.get(
        "docker",
        {}
    )


    if not docker.get("available"):

        findings.append({
            "severity": "critical",
            "service": "docker",
            "issue": "Docker unavailable"
        })

        return findings


    containers = docker.get(
        "containers",
        []
    )


    for service in EXPECTED_SERVICES:

        if service not in containers:

            findings.append({
                "severity": "warning",
                "service": service,
                "issue": f"Missing container: {service}"
            })


    if len(containers) == 0:

        findings.append({
            "severity": "critical",
            "service": "docker",
            "issue": "No containers detected"
        })


    docker_findings = analyze_docker()

    findings.extend(
        docker_findings
    )

from core.service_monitor import check_services
findings.extend(
    check_services()
)

    return findings



if __name__ == "__main__":

    print(analyze())
