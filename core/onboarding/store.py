"""Onboarding state store.

Same canonical persistence as the identity/account/provider/mail/sms/notify
registries: ``core.memory.load`` / ``core.memory.update`` (fcntl.flock, ``.bak``
backup, ``os.replace``) writing ``memory/onboarding_sessions.json`` in the
standard schema envelope, with the secret-guard at the storage boundary.

Each record is a full :class:`~core.onboarding.schema.OnboardingSession`
snapshot, so a process restart can reload the exact point to resume from.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from core.memory import load, update
from core.secret_guard import assert_no_secret_fields

SESSIONS_FILE = "onboarding_sessions.json"


def memory_dir() -> Path:
    """Memory dir resolved at call time so tests can isolate per-run."""
    return Path(os.environ.get("AI_ORCHESTRATOR_MEMORY_DIR", "memory"))


def _records(data) -> list[dict]:
    if isinstance(data, dict):
        data = data.get("records", [])
    return list(data or [])


def read_sessions() -> list[dict]:
    return _records(load(SESSIONS_FILE, directory=memory_dir()))


def update_sessions(mutate_fn: Callable[[list[dict]], list[dict]]) -> list[dict]:
    def _mutate(data):
        records = mutate_fn(_records(data))
        assert_no_secret_fields(records)
        return records

    return update(SESSIONS_FILE, _mutate, directory=memory_dir())


__all__ = ["SESSIONS_FILE", "memory_dir", "read_sessions", "update_sessions"]
