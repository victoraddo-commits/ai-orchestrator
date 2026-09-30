"""Self-contained atomic JSON storage for the browser operator.

The browser engine runs on CT110 as a standalone service; it must not depend on
the orchestrator's heavier ``core.memory`` (second-brain adapters etc.). This
module reproduces the platform's canonical primitive — ``fcntl.flock`` critical
section + ``.bak`` backup + ``os.replace`` — so writes are crash-safe and
concurrent readers never see a torn file. No secrets are ever stored through it
(the secret guard is invoked by callers at their boundary).
"""

from __future__ import annotations

import fcntl
import json
import os
import uuid
from pathlib import Path
from typing import Any, Callable


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path) as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def atomic_write_json(path: Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2, default=str)
        fh.flush()
        os.fsync(fh.fileno())
    if path.exists():
        try:
            path.replace(path.with_suffix(path.suffix + ".bak"))
        except OSError:
            pass
    os.replace(tmp, path)


def update_json(path: Path, mutate_fn: Callable[[Any], Any], default: Any = None) -> Any:
    """Atomic read-modify-write under an exclusive flock on a sidecar lock."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with open(lock_path, "w") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        try:
            current = read_json(path, default if default is not None else {})
            updated = mutate_fn(current)
            atomic_write_json(path, updated)
            return updated
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
