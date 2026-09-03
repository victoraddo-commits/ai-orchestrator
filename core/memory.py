import os
from pathlib import Path
from datetime import datetime

from core import memory_manager
from core.second_brain.stores.operational.adapters.memory_adapter import sync_to_second_brain as _sb_sync


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

    return memory_manager.read(directory / name, default={})


def save(name, data, directory=None):

    directory = directory or MEMORY_DIR

    if _running_under_test() and directory.resolve() == PRODUCTION_MEMORY_DIR.resolve():
        raise ProductionMemoryWriteBlocked(
            f"Refusing to write {name!r} to production memory/ during a test run. "
            "Use an isolated memory directory (see tests/conftest.py)."
        )

    memory_manager.write(directory / name, data)

    # Mirror to Second Brain (additive — failures are silent)
    try:
        _sb_sync(name, data)
    except Exception:
        pass


def update(name, mutate_fn, directory=None):
    """Atomic read-modify-write of a memory file.

    Unlike load() + save() (two separate lock-free steps), the whole
    read -> mutate_fn -> write sequence runs inside one fcntl.flock
    critical section (memory_manager.update), so concurrent updaters --
    threads in the build pool or separate processes like the CloudCLI
    dashboard -- can never lose each other's changes.
    """

    directory = directory or MEMORY_DIR

    if _running_under_test() and directory.resolve() == PRODUCTION_MEMORY_DIR.resolve():
        raise ProductionMemoryWriteBlocked(
            f"Refusing to write {name!r} to production memory/ during a test run. "
            "Use an isolated memory directory (see tests/conftest.py)."
        )

    return memory_manager.update(directory / name, mutate_fn, default={})


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
