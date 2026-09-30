"""Account registry store.

Same canonical persistence as the identity registry: ``core.memory.load`` /
``core.memory.update`` (fcntl.flock, ``.bak`` backup, ``os.replace``) writing
``memory/account_registry.json`` in the standard schema envelope.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from core.memory import load, update
from core.secret_guard import assert_no_secret_fields

REGISTRY_FILE = "account_registry.json"


def memory_dir() -> Path:
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
