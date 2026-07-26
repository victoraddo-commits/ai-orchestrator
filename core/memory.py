import json
from pathlib import Path
from datetime import datetime


MEMORY_DIR = Path("memory")

MEMORY_DIR.mkdir(exist_ok=True)


def load(name):

    path = MEMORY_DIR / name

    if not path.exists():
        return {}

    try:
        with open(path, "r") as file:
            return json.load(file)

    except json.JSONDecodeError:
        return {}


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

    state["last_scan"] = datetime.now().isoformat()

    save(
        "system_state.json",
        state
    )

    return state


if __name__ == "__main__":

    print(update_system_scan())
