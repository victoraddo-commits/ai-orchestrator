"""core.sms stores.

Same canonical persistence as the identity/account/mail registries:
``core.memory`` ``load`` / ``update`` (fcntl.flock, ``.bak`` backup,
``os.replace``) writing JSON in the standard schema envelope, with the
secret-guard at the storage boundary.

Three files:

* ``sms_lines.json``  — SIM/line configs (vault *references* only)
* ``sms_inbox.json``  — normalized inbound SMS (untrusted data; OTP redacted)
* ``sms_seen.json``   — idempotency ledger (message key -> claimed_at)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from core.memory import load, update
from core.secret_guard import assert_no_secret_fields

LINES_FILE = "sms_lines.json"
INBOX_FILE = "sms_inbox.json"
SEEN_FILE = "sms_seen.json"
SENDERS_FILE = "sms_senders.json"


def memory_dir() -> Path:
    """Memory dir resolved at call time so tests can isolate per-run."""
    return Path(os.environ.get("AI_ORCHESTRATOR_MEMORY_DIR", "memory"))


def _records(data) -> list[dict]:
    if isinstance(data, dict):
        data = data.get("records", [])
    return list(data or [])


def _read(name: str) -> list[dict]:
    return _records(load(name, directory=memory_dir()))


def _update(name: str, mutate_fn: Callable[[list[dict]], list[dict]]) -> list[dict]:
    def _mutate(data):
        records = mutate_fn(_records(data))
        assert_no_secret_fields(records)
        return records

    return update(name, _mutate, directory=memory_dir())


# -- lines ---------------------------------------------------------------------

def read_lines() -> list[dict]:
    return _read(LINES_FILE)


def update_lines(mutate_fn) -> list[dict]:
    return _update(LINES_FILE, mutate_fn)


# -- inbox ---------------------------------------------------------------------

def read_inbox() -> list[dict]:
    return _read(INBOX_FILE)


def update_inbox(mutate_fn) -> list[dict]:
    return _update(INBOX_FILE, mutate_fn)


# -- known official senders ----------------------------------------------------

def read_senders() -> list[dict]:
    return _read(SENDERS_FILE)


def update_senders(mutate_fn) -> list[dict]:
    return _update(SENDERS_FILE, mutate_fn)


# -- idempotency ledger --------------------------------------------------------

def read_seen() -> list[dict]:
    return _read(SEEN_FILE)


def update_seen(mutate_fn) -> list[dict]:
    return _update(SEEN_FILE, mutate_fn)


def claim_message(message_key: str) -> bool:
    """Atomically claim *message_key*. Returns True if newly claimed.

    The read-check-append runs inside one ``fcntl.flock`` critical section, so
    two webhook deliveries racing on the same message can never both claim it.
    """
    claimed = {"value": False}

    def _mutate(records: list[dict]) -> list[dict]:
        for rec in records:
            if rec.get("message_key") == message_key:
                return records
        records.append({"message_key": message_key, "claimed_at": _now()})
        claimed["value"] = True
        return records

    update_seen(_mutate)
    return claimed["value"]


def _now() -> str:
    from core.sms.schema import now_iso

    return now_iso()
