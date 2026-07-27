from core.docker_analyzer import analyze_docker
from core.test_memory import create_test_incident


def simulate():

    findings = analyze_docker()

    if not findings:

        finding = {
            "severity": "critical",
            "service": "test-service",
            "issue": "Simulated outage"
        }

    else:

        finding = findings[0]


    incident = create_test_incident(
        finding["severity"],
        finding.get("service", "unknown"),
        finding["issue"]
    )


    return incident


if __name__ == "__main__":

    print(simulate())


# NOTE:
# Test failures should not write into production memory.
# Use isolated test fixtures before running failure simulations.
