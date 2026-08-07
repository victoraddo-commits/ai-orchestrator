import os
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path

from core.k3.config import NetworkPolicy


class SandboxUnavailable(Exception):
    pass


class WorkspaceSandbox:
    """Isolated filesystem workspace for sandboxed builds.

    Creates a temporary directory with proper permissions, optional source
    copy, lock file, and cleanup support.  Designed to pair with SandboxRuntime
    (process isolation) — WorkspaceSandbox manages the filesystem side, and
    SandboxRuntime runs commands inside it.
    """

    def __init__(self, base_dir, prefix="k3-workspace-", subdirs=None,
                 copy_source=None):
        base = Path(base_dir).resolve()
        base.mkdir(parents=True, exist_ok=True)

        # Unique name: prefix + random hex
        name = f"{prefix}{secrets.token_hex(8)}"
        self.root = base / name
        self.root.mkdir(mode=0o700, parents=True, exist_ok=False)
        self._base_dir = base
        self._prefix = prefix
        self._copy_source = copy_source

        # Standard subdirectories
        self.work_dir = self.root / "work"
        self.artifact_dir = self.root / "artifacts"
        self.log_dir = self.root / "logs"

        for d in [self.work_dir, self.artifact_dir, self.log_dir]:
            d.mkdir(mode=0o700)

        if subdirs:
            for name in subdirs:
                sub = self.root / name
                sub.mkdir(mode=0o700, exist_ok=True)

        # Lock file with PID
        self.lock_path = self.root / ".k3-lock"
        self.lock_path.write_text(str(os.getpid()))

        # Copy source if requested
        if copy_source:
            self._do_copy_source(Path(copy_source))

    # ── properties ────────────────────────────────────────────────

    def is_locked(self):
        return self.lock_path.exists()

    # ── source copy ────────────────────────────────────────────────

    def _do_copy_source(self, src):
        """Copy source directory contents, skipping .git."""
        for item in src.iterdir():
            if item.name == ".git":
                continue
            dest = self.root / item.name
            if item.is_dir():
                shutil.copytree(item, dest, symlinks=True)
            else:
                shutil.copy2(item, dest)

    # ── cleanup ────────────────────────────────────────────────────

    def cleanup(self, shred=False):
        """Remove the workspace root directory and all contents.

        If shred=True, overwrite files with random bytes before deletion.
        Handles write-protected files.
        """
        if not self.root.exists():
            return

        if shred:
            self._shred_contents()

        # Make everything writable so cleanup doesn't fail on read-only files
        def _make_writable(path):
            try:
                mode = path.stat().st_mode
                if not (mode & 0o200):
                    path.chmod(mode | 0o200)
            except Exception:
                pass

        for dirpath, dirnames, filenames in os.walk(self.root, topdown=False):
            for f in filenames:
                fp = Path(dirpath) / f
                _make_writable(fp)
            for d in dirnames:
                dp = Path(dirpath) / d
                _make_writable(dp)

        shutil.rmtree(self.root, ignore_errors=True)

    def cleanup_all(self):
        """Remove the base directory (including any sibling workspaces)."""
        self.cleanup()
        if self._base_dir.exists():
            shutil.rmtree(self._base_dir, ignore_errors=True)

    def _shred_contents(self):
        """Overwrite all regular files with random data (3-pass)."""
        for dirpath, _, filenames in os.walk(self.root):
            for f in filenames:
                fp = Path(dirpath) / f
                if not fp.is_file():
                    continue
                try:
                    size = fp.stat().st_size
                    for _ in range(3):
                        fp.write_bytes(os.urandom(size))
                except Exception:
                    pass

    # ── context manager ────────────────────────────────────────────

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()
        return False

    # ── serialization ──────────────────────────────────────────────

    def to_dict(self):
        return {
            "root": str(self.root),
            "work_dir": str(self.work_dir),
            "artifact_dir": str(self.artifact_dir),
            "log_dir": str(self.log_dir),
            "locked": self.is_locked(),
            "pid": os.getpid(),
        }

    def __repr__(self):
        return f"WorkspaceSandbox(root={self.root.name}, locked={self.is_locked()})"


class SandboxRuntime:
    def __init__(
        self,
        mountpoint,
        network=NetworkPolicy.NONE,
        env=None,
        memory_limit=None,
        cpu_limit=None,
    ):
        self.mountpoint = Path(mountpoint).resolve()
        self.network = NetworkPolicy(network)
        self.env = dict(env) if env else {}
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self._workspace_name = "workspace"

    def run(self, command, timeout=300):
        if self._bwrap_available():
            return self._run_with_bwrap(command, timeout)
        else:
            return self._run_with_unshare(command, timeout)

    def _run_with_bwrap(self, command, timeout):
        bwrap = shutil.which("bwrap")
        if not bwrap:
            raise SandboxUnavailable("bubblewrap (bwrap) not found on PATH")

        args = [
            bwrap,
            "--unshare-all",
            "--proc", "/proc",
            "--dev", "/dev",
            "--bind", str(self.mountpoint), f"/{self._workspace_name}",
            "--chdir", f"/{self._workspace_name}",
        ]

        if self.network == NetworkPolicy.NONE:
            args.append("--unshare-net")

        for k, v in self.env.items():
            args += ["--setenv", k, v]

        if self.memory_limit:
            args += ["--setenv", "K3_MEMORY_LIMIT", self.memory_limit]
        if self.cpu_limit:
            args += ["--setenv", "K3_CPU_LIMIT", self.cpu_limit]

        args += ["/bin/sh", "-c", " ".join(command)]

        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)

        return SandboxResult(
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            timed_out=False,
        )

    def _run_with_unshare(self, command, timeout):
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(self.mountpoint),
            env={**os.environ, **self.env},
        )

        return SandboxResult(
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            timed_out=False,
        )

    def _bwrap_available(self):
        return shutil.which("bwrap") is not None


class SandboxResult:
    def __init__(self, exit_code, stdout, stderr, timed_out):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out

    @property
    def succeeded(self):
        return self.exit_code == 0 and not self.timed_out

    def to_dict(self):
        return {
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "timed_out": self.timed_out,
            "succeeded": self.succeeded,
        }


def sandbox_available():
    return shutil.which("bwrap") is not None or shutil.which("unshare") is not None
