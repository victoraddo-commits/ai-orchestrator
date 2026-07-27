import json
import os
from pathlib import Path
from datetime import datetime


PRODUCTION_MEMORY_DIR = Path("memory")


class ProductionMemoryWriteBlocked(RuntimeError):
    """Raised when a test tries to write into the real production memory directory."""


def _default_memory_dir():
    override = os.environ.get("AI_ORCHESTRATOR_MEMORY_DIR")
    return Path(override) if override else PRODUCTION_MEMORY_DIR


MEMORY_DIR = _default_memory_dir()

MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def _running_under_test():
    return "PYTEST_CURRENT_TEST" in os.environ


def load(name, directory=None):

    directory = directory or MEMORY_DIR

    path = directory / name

    if not path.exists():
        return {}

    try:
        with open(path, "r") as file:
            return json.load(file)

    except json.JSONDecodeError:
        return {}


def save(name, data, directory=None):

    directory = directory or MEMORY_DIR

    if _running_under_test() and directory.resolve() == PRODUCTION_MEMORY_DIR.resolve():
        raise ProductionMemoryWriteBlocked(
            f"Refusing to write {name!r} to production memory/ during a test run. "
            "Use an isolated memory directory (see tests/conftest.py)."
        )

    directory.mkdir(parents=True, exist_ok=True)

    path = directory / name

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
