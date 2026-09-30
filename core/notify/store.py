"""core.notify store — pending human-action requests.

Same canonical persistence as the identity/account/mail/sms registries:
``core.memory`` ``load`` / ``update`` (fcntl.flock, ``.bak`` backup,
``os.replace``) writing JSON in the standard schema envelope, with the
secret-guard at the storage boundary.

One file: ``human_actions.json`` — mission-linked human-action requests. A
record holds only routing metadata (action id/type, mission, provider, the
human-readable instruction and timestamps). It never carries an OTP, a token,
or any credential value.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from core.memory import load, update
from core.secret_guard import assert_no_secret_fields

ACTIONS_FILE = "human_actions.json"


def memory_dir() -> Path:
    """Memory dir resolved at call time so tests can isolate per-run."""
    return Path(os.environ.get("AI_ORCHESTRATOR_MEMORY_DIR", "memory"))


def _records(data) -> list[dict]:
    if isinstance(data, dict):
        data = data.get("records", [])
    return list(data or [])


def read_actions() -> list[dict]:
    return _records(load(ACTIONS_FILE, directory=memory_dir()))


def update_actions(mutate_fn: Callable[[list[dict]], list[dict]]) -> list[dict]:
    def _mutate(data):
        records = mutate_fn(_records(data))
        assert_no_secret_fields(records)
        return records

    return update(ACTIONS_FILE, _mutate, directory=memory_dir())
