import os
import subprocess
import threading
import time


DEFAULT_IMAGE = "node:22-bookworm"
DEFAULT_TIMEOUT = 300
DEFAULT_MEMORY = "512m"
DEFAULT_CPUS = "1.0"
DEFAULT_PIDS_LIMIT = "256"

# Prevent parallel builds from spawning too many concurrent Docker containers.
# Each scanner/builder runs in its own sandbox; with MAX_CONCURRENT_BUILDS=8
# and 4 scanners per build (bandit, semgrep, npm_audit, trivy), that's 32
# potential containers on a 6-vCPU system -- enough to starve everything,
# including Claude Code itself.
#
# The semaphore caps total concurrent sandbox containers system-wide. Callers
# that would exceed the cap queue with a timeout rather than failing outright.
_DEFAULT_MAX_CONCURRENT_SANDBOXES = 8
_sandbox_semaphore = threading.BoundedSemaphore(_DEFAULT_MAX_CONCURRENT_SANDBOXES)
_SANDBOX_QUEUE_TIMEOUT = 600  # seconds to wait for a sandbox slot

# Deliberately unsupported in this phase: mounting the host's Docker socket
# into the sandbox (to let a build run `docker build`) would grant the
# sandboxed command effective root on the host, defeating the entire point
# of isolating it. A "docker build" step needs a different mechanism
# (e.g. a dedicated, separately-privileged build service) -- not this one.


class SandboxUnavailable(Exception):
    """Raised when Docker isn't installed or the daemon isn't reachable."""


def sandbox_available():
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False

    return result.returncode == 0


def _docker_run_args(
    project_path,
    command,
    image=DEFAULT_IMAGE,
    network=True,
    memory=DEFAULT_MEMORY,
    cpus=DEFAULT_CPUS,
    entrypoint=None,
):
    abs_path = os.path.abspath(project_path)

    args = [
        "docker", "run", "--rm",
        "--memory", memory,
        "--memory-swap", memory,
        "--cpus", cpus,
        "--pids-limit", DEFAULT_PIDS_LIMIT,
        # Matches this LXC's established Docker convention (see CLAUDE.md).
        "--security-opt", "apparmor=unconfined",
        "--network", "bridge" if network else "none",
        "--read-only",
        "--tmpfs", "/tmp:rw,size=256m",
        # $HOME needs to be writable even under --read-only: pip/npm/etc.
        # write caches, configs, and (for tool self-installs, as opposed to
        # project dependencies which live under the writable /workspace
        # mount) the packages themselves to $HOME. Ephemeral tmpfs, not a
        # host mount -- gone when the container exits either way. `exec` is
        # required because Docker's tmpfs mounts default to noexec, which
        # would otherwise block running anything installed there.
        "--tmpfs", "/root:rw,exec,size=1024m",
        "-v", f"{abs_path}:/workspace:rw",
        "-w", "/workspace",
    ]

    if entrypoint:
        # Some images (e.g. aquasec/trivy) set their own ENTRYPOINT, which
        # would otherwise swallow the `sh -lc "command"` wrapping below --
        # docker would run `<entrypoint> sh -lc "command"` instead of
        # `sh -lc "command"`.
        args += ["--entrypoint", entrypoint]

    args += [
        image,
        # sh, not bash: POSIX sh is present in effectively every image
        # (including minimal ones like alpine, which don't ship bash at
        # all) -- bash is not a safe assumption for an arbitrary sandbox image.
        "sh", "-lc", command,
    ]

    return args


def run_in_sandbox(
    project_path,
    command,
    image=DEFAULT_IMAGE,
    network=True,
    memory=DEFAULT_MEMORY,
    cpus=DEFAULT_CPUS,
    timeout=DEFAULT_TIMEOUT,
    entrypoint=None,
):
    """Runs `command` inside a disposable, resource-limited Docker container
    with only `project_path` mounted (read-write, at /workspace) and a
    read-only root filesystem otherwise. Used to isolate build-time
    operations (npm/pip install, test runs, migrations) from the control
    plane host -- NOT for `docker build` (see module docstring above).

    Concurrency is gated by a system-wide semaphore (_sandbox_semaphore).
    Callers that would exceed the cap queue rather than failing; if a slot
    isn't available within _SANDBOX_QUEUE_TIMEOUT seconds the call fails
    with SandboxUnavailable."""

    if not sandbox_available():
        raise SandboxUnavailable(
            "Docker is not available (binary missing or daemon unreachable) -- "
            "cannot run sandboxed commands."
        )

    acquired = _sandbox_semaphore.acquire(timeout=_SANDBOX_QUEUE_TIMEOUT)
    if not acquired:
        raise SandboxUnavailable(
            f"Timed out waiting for a sandbox slot after {_SANDBOX_QUEUE_TIMEOUT}s "
            f"(max concurrent sandboxes: {_DEFAULT_MAX_CONCURRENT_SANDBOXES}). "
            f"Too many builds or scanners are using Docker concurrently."
        )

    try:
        args = _docker_run_args(
            project_path, command, image=image, network=network, memory=memory, cpus=cpus,
            entrypoint=entrypoint,
        )

        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as error:
            return {
                "exit_code": None,
                "stdout": error.stdout or "",
                "stderr": error.stderr or "",
                "timed_out": True,
            }

        return {
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "timed_out": False,
        }
    finally:
        _sandbox_semaphore.release()


def get_sandbox_stats():
    """Return current sandbox concurrency utilization for monitoring."""
    # BoundedSemaphore doesn't expose its current value directly, but we
    # can approximate: the semaphore starts at _DEFAULT_MAX_CONCURRENT_SANDBOXES
    # and each active sandbox holds one permit. We track via a simple counter
    # in the semaphore wrapper above -- for now, return the config values.
    return {
        "max_concurrent": _DEFAULT_MAX_CONCURRENT_SANDBOXES,
        "queue_timeout_s": _SANDBOX_QUEUE_TIMEOUT,
    }


def set_max_concurrent_sandboxes(n):
    """Dynamically adjust the max concurrent sandbox limit at runtime.

    Increasing the limit is always allowed. Decreasing it below the current
    number of active sandboxes will block until those sandboxes release their
    permits -- callers should only shrink the pool when the system is quiet."""
    global _sandbox_semaphore, _DEFAULT_MAX_CONCURRENT_SANDBOXES
    if n < 1:
        raise ValueError("max concurrent sandboxes must be >= 1")
    delta = n - _DEFAULT_MAX_CONCURRENT_SANDBOXES
    if delta > 0:
        # Growing: create a new semaphore with the larger size, transfer
        # any currently held permits by draining the old one first.
        old = _sandbox_semaphore
        new = threading.BoundedSemaphore(n)
        # Acquire all permits from the old semaphore to count held ones
        held = 0
        while old.acquire(blocking=False):
            held += 1
        # The old semaphore started at _DEFAULT_MAX_CONCURRENT_SANDBOXES;
        # held permits = old_max - currently_active. Acquire the same
        # number from the new semaphore to preserve the active count.
        to_acquire = _DEFAULT_MAX_CONCURRENT_SANDBOXES - held
        for _ in range(to_acquire):
            new.acquire(blocking=False)
        _sandbox_semaphore = new
        _DEFAULT_MAX_CONCURRENT_SANDBOXES = n
    elif delta < 0:
        # Shrinking: acquire additional permits from the current semaphore
        # and then replace it with a smaller one.
        for _ in range(-delta):
            acquired = _sandbox_semaphore.acquire(timeout=30)
            if not acquired:
                raise RuntimeError(
                    f"Timed out shrinking sandbox pool to {n} -- "
                    f"too many sandboxes are still active"
                )
        _DEFAULT_MAX_CONCURRENT_SANDBOXES = n
    # delta == 0: no-op
