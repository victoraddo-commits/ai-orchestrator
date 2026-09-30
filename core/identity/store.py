"""Identity registry store.

Reuses the platform's canonical atomic memory primitive:
``core.memory.load`` / ``core.memory.update`` — a ``fcntl.flock`` critical
section with ``.bak`` backup and ``os.replace`` (see ``core/memory.py`` and
``core/repo_registry/registry.py:1-9``). Records persist as
``memory/identity_registry.json`` in the standard schema envelope.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from core.memory import load, update
from core.secret_guard import assert_no_secret_fields

REGISTRY_FILE = "identity_registry.json"


def memory_dir() -> Path:
    """Memory directory resolved at call time so tests can isolate per-run."""
    return Path(os.environ.get("AI_ORCHESTRATOR_MEMORY_DIR", "memory"))


def _records(data) -> list[dict]:
    if isinstance(data, dict):
        data = data.get("records", [])
    return list(data or [])


def read_records() -> list[dict]:
    return _records(load(REGISTRY_FILE, directory=memory_dir()))


def update_records(mutate_fn: Callable[[list[dict]], list[dict]]) -> list[dict]:
    def _mutate(data):
        records = mutate_fn(_records(data))
        assert_no_secret_fields(records)
        return records

    return update(REGISTRY_FILE, _mutate, directory=memory_dir())
