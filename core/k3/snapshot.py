import hashlib
import os
import stat
from pathlib import Path


class ChangeSet:
    def __init__(self, created=None, modified=None, deleted=None):
        self.created = list(created) if created else []
        self.modified = list(modified) if modified else []
        self.deleted = list(deleted) if deleted else []

    def has_changes(self):
        return bool(self.created or self.modified or self.deleted)

    def total_changes(self):
        return len(self.created) + len(self.modified) + len(self.deleted)

    def to_dict(self):
        return {
            "created": [str(p) for p in self.created],
            "modified": [str(p) for p in self.modified],
            "deleted": [str(p) for p in self.deleted],
        }

    def __repr__(self):
        return f"ChangeSet(created={len(self.created)}, modified={len(self.modified)}, deleted={len(self.deleted)})"


class WorkspaceSnapshooter:
    def __init__(self, workspace_path, follow_symlinks=False):
        self.workspace_path = Path(workspace_path).resolve()
        self.follow_symlinks = follow_symlinks

    def capture(self):
        entries = {}
        for path in self._walk():
            rel = path.relative_to(self.workspace_path)
            entries[str(rel)] = self._file_info(path)
        return WorkspaceSnapshot(self.workspace_path, entries)

    def _walk(self):
        for entry in self.workspace_path.rglob("*"):
            if entry.is_symlink() and not self.follow_symlinks:
                yield entry
            elif entry.is_file():
                yield entry

    def _file_info(self, path):
        try:
            st = os.lstat(path)
            info = {
                "mode": st.st_mode,
                "uid": st.st_uid,
                "gid": st.st_gid,
                "size": st.st_size,
                "mtime": st.st_mtime_ns,
            }

            if stat.S_ISLNK(st.st_mode):
                info["type"] = "symlink"
                info["target"] = os.readlink(str(path))
            elif stat.S_ISREG(st.st_mode):
                info["type"] = "file"
                info["hash"] = self._hash_file(path)
            else:
                info["type"] = "other"

            return info
        except (OSError, PermissionError):
            return {"type": "inaccessible"}

    def _hash_file(self, path):
        h = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    h.update(chunk)
            return h.hexdigest()
        except (OSError, PermissionError):
            return None


class WorkspaceSnapshot:
    def __init__(self, workspace_path, entries=None):
        self.workspace_path = Path(workspace_path)
        self.entries = dict(entries) if entries else {}

    def get(self, relative_path):
        return self.entries.get(str(relative_path))

    def diff(self, upperdir):
        upperdir = Path(upperdir)
        changes = ChangeSet()

        for root, dirs, files in os.walk(upperdir):
            for name in files:
                fpath = Path(root) / name
                rel = fpath.relative_to(upperdir)

                if self._is_whiteout(fpath):
                    changes.deleted.append(rel)
                    continue

                if not self.entries.get(str(rel)):
                    changes.created.append(rel)
                else:
                    baseline = self.entries[str(rel)]
                    if baseline.get("type") == "file":
                        current_hash = self._hash_file(fpath)
                        if current_hash != baseline.get("hash"):
                            changes.modified.append(rel)

        return changes

    def _is_whiteout(self, path):
        if path.name.startswith(".wh."):
            return True
        if path.is_char_device() or path.is_block_device():
            return True
        return False

    def _hash_file(self, path):
        import hashlib
        h = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    h.update(chunk)
            return h.hexdigest()
        except (OSError, PermissionError):
            return None

    def to_dict(self):
        return {
            "workspace_path": str(self.workspace_path),
            "file_count": len(self.entries),
        }
