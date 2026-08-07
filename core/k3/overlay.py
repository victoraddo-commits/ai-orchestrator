import os
import shutil
import subprocess
import tempfile
from pathlib import Path


class OverlayUnavailable(Exception):
    pass


class OverlayManager:
    def __init__(self, workspace_path):
        self.workspace_path = Path(workspace_path).resolve()
        self._tmpdir = None
        self._lowerdir = None
        self._upperdir = None
        self._workdir = None
        self._mountpoint = None
        self._mounted = False

    @property
    def lowerdir(self):
        return self._lowerdir

    @property
    def upperdir(self):
        return self._upperdir

    @property
    def workdir(self):
        return self._workdir

    @property
    def mountpoint(self):
        return self._mountpoint

    def mount(self):
        if self._mounted:
            return

        self._validate_workspace()
        self._tmpdir = Path(tempfile.mkdtemp(prefix="k3-"))
        self._lowerdir = self.workspace_path
        self._upperdir = self._tmpdir / "upper"
        self._workdir = self._tmpdir / "work"
        self._mountpoint = self._tmpdir / "mnt"

        self._upperdir.mkdir(exist_ok=True)
        self._workdir.mkdir(exist_ok=True)
        self._mountpoint.mkdir(exist_ok=True)

        self._do_mount()
        self._mounted = True

    def _do_mount(self):
        if not _supports_overlay():
            raise OverlayUnavailable(
                "overlay filesystem not available on this system. "
                "K3 requires Linux kernel >= 3.18 with overlay support "
                "and CONFIG_OVERLAY_FS=y."
            )

        mount_opts = (
            f"lowerdir={self._lowerdir},"
            f"upperdir={self._upperdir},"
            f"workdir={self._workdir}"
        )

        result = subprocess.run(
            ["mount", "-t", "overlay", "overlay", "-o", mount_opts, str(self._mountpoint)],
            capture_output=True, text=True, timeout=30,
        )

        if result.returncode != 0:
            raise OverlayUnavailable(
                f"Failed to mount overlay: {result.stderr.strip()}. "
                f"K3 may need to be run with sufficient privileges or via bubblewrap."
            )

    def cleanup(self):
        errors = []

        if self._mounted and self._mountpoint:
            try:
                subprocess.run(
                    ["umount", str(self._mountpoint)],
                    capture_output=True, timeout=30,
                )
            except (subprocess.TimeoutExpired, OSError) as e:
                errors.append(f"umount: {e}")
            finally:
                try:
                    self._mountpoint.rmdir()
                except OSError:
                    pass

        if self._tmpdir and self._tmpdir.exists():
            try:
                shutil.rmtree(self._tmpdir, ignore_errors=True)
            except OSError as e:
                errors.append(f"tmpdir cleanup: {e}")

        self._mounted = False
        return errors

    def _validate_workspace(self):
        if not self.workspace_path.exists():
            raise ValueError(f"Workspace path does not exist: {self.workspace_path}")
        if not self.workspace_path.is_dir():
            raise ValueError(f"Workspace path is not a directory: {self.workspace_path}")

    def __del__(self):
        if self._mounted:
            self.cleanup()


def _supports_overlay():
    try:
        result = subprocess.run(
            ["grep", "-q", "overlay", "/proc/filesystems"],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def overlay_available():
    return _supports_overlay()
