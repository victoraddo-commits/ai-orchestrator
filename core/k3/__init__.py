"""K3 — Isolated self-modifying build workspace.

Uses overlay filesystem + process sandbox to give builds a private, writable
workspace while keeping the original workspace untouched.

Usage:
    from core.k3 import run_build, K3Config, PersistPolicy

    config = K3Config(
        workspace_path="./src",
        command=["make", "build"],
        persist=PersistPolicy.DISCARD,
    )
    result = run_build(config)
"""

__version__ = "1.0.0"

from core.k3.config import K3Config, PersistPolicy, NetworkPolicy
from core.k3.overlay import OverlayManager, OverlayUnavailable
from core.k3.sandbox import SandboxRuntime, SandboxUnavailable
from core.k3.policy import ChangePolicyEngine
from core.k3.snapshot import WorkspaceSnapshooter
from core.k3.exceptions import K3Error, K3BuildError, K3CleanupError

def run_build(config):
    """Run a build in an isolated overlay workspace.

    Returns a K3Result with exit_code, stdout, stderr, and changes.
    """
    from core.k3.overlay import OverlayManager
    from core.k3.sandbox import SandboxRuntime
    from core.k3.snapshot import WorkspaceSnapshooter
    from core.k3.policy import ChangePolicyEngine

    snapshooter = WorkspaceSnapshooter(config.workspace_path)
    baseline = snapshooter.capture()

    overlay = None
    try:
        overlay = OverlayManager(config.workspace_path)
        overlay.mount()

        runtime = SandboxRuntime(
            mountpoint=overlay.mountpoint,
            network=config.network,
            env=config.env,
            memory_limit=config.memory_limit,
            cpu_limit=config.cpu_limit,
        )
        sandbox_result = runtime.run(config.command, timeout=config.timeout)

        engine = ChangePolicyEngine(
            overlay.upperdir,
            baseline=baseline,
            workspace_path=config.workspace_path,
        )

        if config.persist == PersistPolicy.COMMIT:
            engine.commit()
        elif config.persist == PersistPolicy.ARTIFACTS:
            engine.extract_artifacts(config.artifact_patterns, config.artifact_output_dir)

        changes = engine.report() if config.persist in (PersistPolicy.REPORT, PersistPolicy.COMMIT, PersistPolicy.ARTIFACTS) else None

        return K3Result(
            exit_code=sandbox_result.exit_code,
            stdout=sandbox_result.stdout,
            stderr=sandbox_result.stderr,
            timed_out=sandbox_result.timed_out,
            changes=changes,
            mountpoint=overlay.mountpoint,
        )
    finally:
        if overlay:
            overlay.cleanup()


class K3Result:
    def __init__(self, exit_code, stdout, stderr, timed_out, changes, mountpoint):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out
        self.changes = changes
        self.mountpoint = mountpoint

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
            "changes": self._serialize_changes(),
            "mountpoint": str(self.mountpoint) if self.mountpoint else None,
        }

    def _serialize_changes(self):
        if self.changes is None:
            return None
        return {
            "created": [str(p) for p in self.changes.created],
            "modified": [str(p) for p in self.changes.modified],
            "deleted": [str(p) for p in self.changes.deleted],
        }

    def __repr__(self):
        return f"K3Result(exit_code={self.exit_code}, timed_out={self.timed_out}, changes={len(self.changes.created) if self.changes else 0} created, {len(self.changes.modified) if self.changes else 0} modified, {len(self.changes.deleted) if self.changes else 0} deleted)"
