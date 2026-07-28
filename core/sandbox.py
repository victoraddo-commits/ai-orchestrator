import os
import subprocess


DEFAULT_IMAGE = "node:22-bookworm"
DEFAULT_TIMEOUT = 300
DEFAULT_MEMORY = "512m"
DEFAULT_CPUS = "1.0"
DEFAULT_PIDS_LIMIT = "256"

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
):
    abs_path = os.path.abspath(project_path)

    return [
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
        "-v", f"{abs_path}:/workspace:rw",
        "-w", "/workspace",
        image,
        # sh, not bash: POSIX sh is present in effectively every image
        # (including minimal ones like alpine, which don't ship bash at
        # all) -- bash is not a safe assumption for an arbitrary sandbox image.
        "sh", "-lc", command,
    ]


def run_in_sandbox(
    project_path,
    command,
    image=DEFAULT_IMAGE,
    network=True,
    memory=DEFAULT_MEMORY,
    cpus=DEFAULT_CPUS,
    timeout=DEFAULT_TIMEOUT,
):
    """Runs `command` inside a disposable, resource-limited Docker container
    with only `project_path` mounted (read-write, at /workspace) and a
    read-only root filesystem otherwise. Used to isolate build-time
    operations (npm/pip install, test runs, migrations) from the control
    plane host -- NOT for `docker build` (see module docstring above)."""

    if not sandbox_available():
        raise SandboxUnavailable(
            "Docker is not available (binary missing or daemon unreachable) -- "
            "cannot run sandboxed commands."
        )

    args = _docker_run_args(
        project_path, command, image=image, network=network, memory=memory, cpus=cpus
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
