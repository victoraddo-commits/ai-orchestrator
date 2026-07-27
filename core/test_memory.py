from pathlib import Path
import uuid
from datetime import datetime

import core.memory as memory

TEST_MEMORY_DIR = Path("tests/memory")


def create_test_incident(severity, service, issue):

    incidents = memory.load("incidents.json", directory=TEST_MEMORY_DIR)

    if not isinstance(incidents, list):
        incidents = []

    incident = {
        "id": str(uuid.uuid4())[:8],
        "severity": severity,
        "service": service,
        "issue": issue,
        "created": datetime.now().isoformat(),
        "status": "open"
    }

    incidents.append(incident)

    memory.save("incidents.json", incidents, directory=TEST_MEMORY_DIR)

    return incident


if __name__ == "__main__":
    print(
        create_test_incident(
            "critical",
            "test-service",
            "Simulated outage"
        )
    )
