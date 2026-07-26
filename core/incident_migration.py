from core.memory import load, save
from datetime import datetime
import uuid


def migrate():

    incidents = load("incidents.json") or []

    migrated = []

    for old in incidents:

        incident = {
            "id": str(old.get("id") or uuid.uuid4().hex[:8]),

            "service": old.get(
                "service",
                "unknown"
            ),

            "issue": old.get(
                "issue",
                "unknown"
            ),

            "severity": old.get(
                "severity",
                "warning"
            ),

            "occurrences": old.get(
                "occurrences",
                1
            ),

            "status": old.get(
                "status",
                "open"
            ),

            "created": old.get(
                "created",
                old.get(
                    "timestamp",
                    datetime.now().isoformat()
                )
            ),

            "updated": datetime.now().isoformat()
        }

        migrated.append(
            incident
        )


    save(
        "incidents.json",
        migrated
    )

    return migrated



if __name__ == "__main__":

    print(
        migrate()
    )
