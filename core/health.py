from core.memory import load
from datetime import datetime


EXPECTED_SERVICES = (
    "pulse",
    "proxdash-backend",
    "proxdash-frontend",
    "mattermost",
)


def analyze():

    state = load("system_state.json")

    findings = []

    docker = state.get("docker", {})

    if not docker.get("available"):
        findings.append({
            "severity": "critical",
            "issue": "Docker unavailable"
        })

        return findings


    containers = docker.get("containers", [])


    for service in EXPECTED_SERVICES:

        if service not in containers:

            findings.append({
                "severity": "warning",
                "issue": f"Missing container: {service}"
            })


    if len(containers) == 0:

        findings.append({
            "severity": "critical",
            "issue": "No containers detected"
        })


    return findings


if __name__ == "__main__":

    print(analyze())
