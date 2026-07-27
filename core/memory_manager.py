import fcntl
import json
import os
import shutil
from pathlib import Path


SCHEMA_VERSION = 1


def _lock_path(path):
    return path.with_suffix(path.suffix + ".lock")


def _backup_path(path):
    return path.with_suffix(path.suffix + ".bak")


def _tmp_path(path):
    return path.with_suffix(path.suffix + f".tmp.{os.getpid()}.{id(path)}")


def _wrap(records):
    return {"schema_version": SCHEMA_VERSION, "records": records}


def _unwrap(payload, default):
    if isinstance(payload, dict) and "schema_version" in payload and "records" in payload:
        return payload["records"]

    return payload


def _load_json(path):
    with open(path, "r") as f:
        return json.load(f)


def read(path, default):

    path = Path(path)

    if not path.exists():
        return default

    try:
        return _unwrap(_load_json(path), default)

    except (json.JSONDecodeError, OSError):

        backup = _backup_path(path)

        if backup.exists():
            try:
                return _unwrap(_load_json(backup), default)
            except (json.JSONDecodeError, OSError):
                pass

        return default


def write(path, records):

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    lock_path = _lock_path(path)

    with open(lock_path, "w") as lock_file:

        fcntl.flock(lock_file, fcntl.LOCK_EX)

        try:
            _write_locked(path, records)
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _write_locked(path, records):

    if path.exists():
        try:
            shutil.copyfile(path, _backup_path(path))
        except OSError:
            pass

    tmp = _tmp_path(path)

    try:
        with open(tmp, "w") as f:
            json.dump(_wrap(records), f, indent=2)
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp, path)

    finally:
        if tmp.exists():
            tmp.unlink()
