import json
from pathlib import Path
from datetime import datetime


MEMORY_DIR = Path("memory")


def load(name):

    path = MEMORY_DIR / name

    if not path.exists():
        return {}

    with open(path, "r") as file:
        return json.load(file)


def save(name, data):

    path = MEMORY_DIR / name

    with open(path, "w") as file:
        json.dump(
            data,
            file,
            indent=2
        )


def update_system_scan():

    state = load("system_state.json")

    state["last_scan"] = (
        datetime.now().isoformat()
    )

    save(
        "system_state.json",
        state
    )


def record_incident(message):

    incidents = load(
        "incidents.json"
    )

    incidents[
        datetime.now().isoformat()
    ] = {
        "message": message
    }

    save(
        "incidents.json",
        incidents
    )


if __name__ == "__main__":

    update_system_scan()

    print(
        json.dumps(
            load("system_state.json"),
            indent=2
        )
    )
