"""Kai Backup & Disaster Recovery (roadmap 27E).

Application-state backup for the Kai orchestrator ("Ultimate Backup & DR"
directive): produce a consistent, checksummed, versioned archive of Kai's
critical state, verify it, restore it on a non-production target, and apply a
retention policy. stdlib-only so it runs anywhere the orchestrator runs.

Scope note: this covers Kai *application state* (memory, config, second-brain
stores, SQLite databases). Full CT/VM image backups (Proxmox ``vzdump``/PBS) are
the node-side layer and are documented in ``docs/BACKUP_DR.md`` — they are not
performed from inside the container.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import tarfile
import time
from pathlib import Path

SCHEMA = 1
DEFAULT_BASE = os.environ.get("KAI_HOME", "/opt/ai-orchestrator")
DEFAULT_TARGET = os.environ.get("KAI_BACKUP_DIR", str(Path(DEFAULT_BASE) / "backups"))
DEFAULT_SOURCES = ("memory", "config", "core/second_brain")

_EXCLUDE_DIRS = {".venv", "__pycache__", "node_modules", ".git", "backups"}
_EXCLUDE_SUFFIXES = (".lock", ".tmp")
_EXCLUDE_PARTS = {".pytest_cache", ".mypy_cache"}


def _excluded(name: str) -> bool:
    parts = name.split("/")
    if any(p in _EXCLUDE_DIRS or p in _EXCLUDE_PARTS for p in parts):
        return True
    if name.endswith(_EXCLUDE_SUFFIXES):
        return True
    if ".tmp." in name:  # memory writes leave .tmp.<pid>.<n> fragments
        return True
    return False


def _sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _tar_filter(ti: tarfile.TarInfo):
    if _excluded(ti.name):
        return None
    ti.uid = ti.gid = 0
    ti.uname = ti.gname = "root"
    return ti


def _add_tree(tar: tarfile.TarFile, path: Path, arcname: str) -> bool:
    """Add ``path`` to ``tar``, skipping entries that vanish mid-walk.

    ``tarfile.add`` stats each member as it recurses. Under concurrent writers
    (Kai's ``memory/`` churns via atomic tmp+rename) a member can disappear
    between the directory listing and the stat, raising ``FileNotFoundError``
    and aborting the entire backup (TOCTOU, observed 2026-09-18 on
    ``memory/builds.json.tmp.*``). Walking the tree ourselves lets us skip only
    the vanished entry and keep the rest of the archive.
    """
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    if path.is_dir() and not path.is_symlink():
        try:
            tar.add(str(path), arcname=arcname, filter=_tar_filter, recursive=False)
        except FileNotFoundError:
            return False
        try:
            children = sorted(path.iterdir(), key=lambda c: c.name)
        except FileNotFoundError:
            return False
        for child in children:
            _add_tree(tar, child, f"{arcname}/{child.name}")
        return True
    try:
        tar.add(str(path), arcname=arcname, filter=_tar_filter, recursive=False)
    except FileNotFoundError:
        return False
    return True


def create_backup(sources=DEFAULT_SOURCES, target: str | Path = DEFAULT_TARGET,
                  tag: str = "kai", base: str | Path = DEFAULT_BASE) -> dict:
    """Create ``<target>/<tag>-<utc>.tar.gz`` + a ``.manifest.json`` beside it.

    Atomic: writes to ``.part`` then renames. Returns the manifest dict.
    """
    base = Path(base)
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    name = f"{tag}-{ts}.tar.gz"
    part = target / (name + ".part")
    archive = target / name

    included = []
    with tarfile.open(part, "w:gz") as tar:
        for src in sources:
            p = Path(src)
            if not p.is_absolute():
                p = base / src
            if not p.exists():
                continue
            arc = str(p.relative_to(base)) if str(p).startswith(str(base)) else p.name
            if _add_tree(tar, p, arc):
                included.append(arc)

    os.replace(part, archive)
    manifest = {
        "schema": SCHEMA,
        "name": name,
        "tag": tag,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "created_epoch": time.time(),
        "host": socket.gethostname(),
        "base": str(base),
        "sources": included,
        "sha256": _sha256(archive),
        "size_bytes": archive.stat().st_size,
    }
    (target / (name + ".manifest.json")).write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def list_backups(target: str | Path = DEFAULT_TARGET) -> list:
    """Newest-first list of {manifest..., "verified": bool} (checksum recomputed)."""
    target = Path(target)
    out = []
    for mf in sorted(target.glob("*.manifest.json"), reverse=True):
        try:
            m = json.loads(mf.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        archive = target / m.get("name", "")
        m["verified"] = archive.exists() and _sha256(archive) == m.get("sha256")
        out.append(m)
    out.sort(key=lambda m: m.get("created_epoch", 0), reverse=True)
    return out


def verify_backup(name: str, target: str | Path = DEFAULT_TARGET) -> dict:
    """Recompute the archive digest and confirm it matches its manifest."""
    target = Path(target)
    mf = target / (name + ".manifest.json")
    if not mf.exists():
        return {"ok": False, "name": name, "error": "manifest not found"}
    m = json.loads(mf.read_text())
    archive = target / m["name"]
    if not archive.exists():
        return {"ok": False, "name": name, "error": "archive not found"}
    actual = _sha256(archive)
    return {"ok": actual == m["sha256"], "name": name,
            "sha256": actual, "expected": m["sha256"]}


def restore_backup(name: str, dest: str | Path, target: str | Path = DEFAULT_TARGET,
                   verify: bool = True) -> dict:
    """Extract an archive into ``dest`` (a NON-production path). Refuses /.

    Verifies the checksum first (unless ``verify=False``), extracts with a
    data filter (blocks path traversal / symlink escapes), and returns the list
    of top-level entries restored.
    """
    dest = Path(dest)
    if dest == Path("/") or str(dest) in ("", "/"):
        raise ValueError("refusing to restore into '/'")
    target = Path(target)
    if verify:
        v = verify_backup(name, target)
        if not v.get("ok"):
            return {"restored": False, "name": name, "error": "checksum mismatch", "detail": v}
    archive = target / name
    if not archive.exists():
        return {"restored": False, "name": name, "error": "archive not found"}
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        try:
            tar.extractall(dest, filter="data")  # py>=3.12 safe filter
        except TypeError:  # older python: manual guard
            for m in tar.getmembers():
                if m.name.startswith("/") or ".." in Path(m.name).parts:
                    raise ValueError(f"unsafe path in archive: {m.name}")
            tar.extractall(dest)
    restored = sorted({p.name for p in dest.iterdir()})
    return {"restored": True, "name": name, "dest": str(dest), "entries": restored}


def prune_backups(target: str | Path = DEFAULT_TARGET, keep: int = 7) -> dict:
    """Keep the ``keep`` newest archives (+ manifests); delete the rest."""
    target = Path(target)
    backups = list_backups(target)  # newest first
    removed = []
    for m in backups[keep:]:
        for p in (target / m["name"], target / (m["name"] + ".manifest.json")):
            if p.exists():
                p.unlink()
                removed.append(p.name)
    return {"kept": min(len(backups), keep), "removed": removed}


def latest(target: str | Path = DEFAULT_TARGET) -> dict | None:
    backups = list_backups(target)
    return backups[0] if backups else None


if __name__ == "__main__":  # python -m core.backup_manager [keep]
    import sys

    _keep = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    _m = create_backup()
    _p = prune_backups(keep=_keep)
    print(json.dumps({"created": _m["name"], "sha256": _m["sha256"],
                      "size_bytes": _m["size_bytes"], "pruned": _p["removed"]}))

