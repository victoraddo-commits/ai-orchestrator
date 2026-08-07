import os
import shutil
from pathlib import Path
from fnmatch import fnmatch

from core.k3.snapshot import WorkspaceSnapshot


class ChangePolicyEngine:
    def __init__(self, upperdir, baseline=None, workspace_path=None):
        self.upperdir = Path(upperdir)
        self.baseline = baseline
        self.workspace_path = Path(workspace_path) if workspace_path else None

    def report(self):
        if self.baseline is None:
            return self._report_from_fs()
        return self.baseline.diff(self.upperdir)

    def _report_from_fs(self):
        from core.k3.snapshot import ChangeSet
        changes = ChangeSet()
        for root, dirs, files in os.walk(self.upperdir):
            for name in files:
                changes.created.append(Path(root) / name)
        return changes

    def commit(self):
        if not self.workspace_path or not self.workspace_path.exists():
            raise ValueError("workspace_path required for commit policy")

        changes = self.report()

        for rel in changes.created:
            src = self.upperdir / rel
            dst = self.workspace_path / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

        for rel in changes.modified:
            src = self.upperdir / rel
            dst = self.workspace_path / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

        for rel in changes.deleted:
            dst = self.workspace_path / rel
            if dst.exists():
                dst.unlink()

    def extract_artifacts(self, patterns, output_dir):
        if not output_dir:
            raise ValueError("output_dir required for artifact extraction")

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        for root, dirs, files in os.walk(self.upperdir):
            for name in files:
                fpath = Path(root) / name
                rel = fpath.relative_to(self.upperdir)

                if self._matches_pattern(rel, patterns):
                    dst = output_dir / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(fpath, dst)

    def _matches_pattern(self, relative_path, patterns):
        path_str = str(relative_path)
        for pattern in patterns:
            if fnmatch(path_str, pattern):
                return True
            if fnmatch(relative_path.name, pattern):
                return True
        return False
