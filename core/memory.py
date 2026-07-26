import json
from pathlib import Path
from datetime import datetime


BASE_DIR = Path(__file__).resolve().parent.parent
MEMORY_DIR = BASE_DIR / "memory"


DEFAULT_FILES = {
    "system_state.json": {
        "hostname": "",
        "services": [],
        "last_scan": ""
    },
    "decisions.json": [],
    "incidents.json": []
}


def ensure_memory():

    MEMORY_DIR.mkdir(exist_ok=True)

    for filename, default in DEFAULT_FILES.items():

        path = MEMORY_DIR / filename

        if not path.exists():

            with open(path, "w") as file:
                json.dump(
                    default,
                    file,
                    indent=2
                )


def load(name):

    ensure_memory()

    path = MEMORY_DIR / name

    with open(path, "r") as file:
        return json.load(file)



def save(name, data):

    ensure_memory()

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


