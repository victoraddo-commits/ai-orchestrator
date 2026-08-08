"""13J: Subprocess watchdog for generation subprocess calls.

Wraps a subprocess (opencode run or similar) with:
- Wall-clock timeout
- Near-zero CPU idle detection (checks /proc/<pid>/stat for CPU-time
  progression over an interval -- the exact failure signature from the
  2026-08-02 live incident)
- Process-group kill on timeout (SIGTERM -> grace -> SIGKILL)
- Typed GenerationTimeoutError so the scheduler can mark the phase
  failed/reset rather than hanging forever

Deliberately uses /proc/<pid>/stat (no psutil dependency) since that
file is available on every Linux system this project ever targets --
no reason to add a dependency for one read.
"""

import logging
import os
import signal
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class GenerationTimeoutError(subprocess.TimeoutExpired):
    """Raised when a generation subprocess exceeds its time budget.

    Inherits TimeoutExpired so existing ``except subprocess.TimeoutExpired``
    handlers in opencode_bridge.run_coding_task still catch it without
    changes -- the extra ``cpu_idle`` attribute lets callers that care
    distinguish a wall-clock expiry from a zero-CPU hang.
    """

    def __init__(self, cmd, timeout, output=None, stderr=None, cpu_idle=False):
        super().__init__(cmd, timeout, output=output, stderr=stderr)
        self.cpu_idle = cpu_idle


class SubprocessWatchdog:
    """Launch a subprocess with process-group isolation and dual timeouts.

    Parameters
    ----------
    args : list
        Command line (list of strings).
    wall_timeout_seconds : float
        Maximum wall-clock time before the process group is killed.
    idle_cpu_seconds : float or None
        If set, kill the process group when its cumulative CPU-time has not
        grown by at least 0.01 s over this interval (checked every second).
    grace_kill_seconds : float
        After sending SIGTERM, wait this long before SIGKILL.
    check_interval_seconds : float
        How often the monitor thread wakes to check CPU progression.

    On timeout the whole process group is killed, partial stdout/stderr
    captured up to that point, and a ``GenerationTimeoutError`` is raised.
    """

    def __init__(
        self,
        args,
        *,
        wall_timeout_seconds,
        idle_cpu_seconds=None,
        grace_kill_seconds=5,
        check_interval_seconds=1.0,
        env=None,
    ):
        self.args = [str(a) for a in args]
        self.wall_timeout_seconds = wall_timeout_seconds
        self.idle_cpu_seconds = idle_cpu_seconds
        self.grace_kill_seconds = grace_kill_seconds
        self.check_interval_seconds = max(check_interval_seconds, 0.1)
        self.env = env  # optional env override dict for Popen

    def run(self):
        """Start the subprocess, monitor, and wait.

        Returns
        -------
        subprocess.CompletedProcess
            With ``stdout``, ``stderr``, ``returncode`` -- partial if the
            process was killed.
        """
        proc = subprocess.Popen(
            self.args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            env=self.env,
        )

        start_time = time.monotonic()
        last_cpu_time = None
        last_cpu_check = start_time

        try:
            while True:
                remaining = self.wall_timeout_seconds - (time.monotonic() - start_time)
                if remaining <= 0:
                    self._kill_process_group(proc, start_time, cpu_idle=False)
                    raise GenerationTimeoutError(
                        cmd=self.args,
                        timeout=self.wall_timeout_seconds,
                        output=proc.stdout.read() if proc.stdout else "",
                        stderr=proc.stderr.read() if proc.stderr else "",
                        cpu_idle=False,
                    )

                wait_for = min(remaining, self.check_interval_seconds)

                try:
                    proc.wait(timeout=wait_for)
                    return self._completed(proc)
                except subprocess.TimeoutExpired:
                    pass

                if self.idle_cpu_seconds is not None:
                    cpu_time = self._read_cpu_time(proc.pid)
                    if cpu_time is not None:
                        if last_cpu_time is not None:
                            delta_cpu = cpu_time - last_cpu_time
                            elapsed_idle = time.monotonic() - last_cpu_check
                            if delta_cpu < 0.01 and elapsed_idle >= self.idle_cpu_seconds:
                                self._kill_process_group(proc, start_time, cpu_idle=True)
                                raise GenerationTimeoutError(
                                    cmd=self.args,
                                    timeout=self.idle_cpu_seconds,
                                    output=proc.stdout.read() if proc.stdout else "",
                                    stderr=proc.stderr.read() if proc.stderr else "",
                                    cpu_idle=True,
                                )
                            if delta_cpu >= 0.01:
                                last_cpu_time = cpu_time
                                last_cpu_check = time.monotonic()
                        else:
                            last_cpu_time = cpu_time
                            last_cpu_check = time.monotonic()

        except BaseException:
            self._kill_process_group(proc, start_time, cpu_idle=False)
            raise

    def _read_cpu_time(self, pid):
        """Read cumulative CPU time from /proc/<pid>/stat (utime + stime in
        jiffies). Returns None when the pid is gone or unreadable."""
        try:
            stat = (Path("/proc") / str(pid) / "stat").read_text()
        except (OSError, ValueError):
            return None

        fields = stat.rsplit(")", 1)
        if len(fields) != 2:
            return None

        try:
            numbers = fields[1].split()
            utime = int(numbers[11])
            stime = int(numbers[12])
        except (IndexError, ValueError):
            return None

        return (utime + stime) / os.sysconf(os.sysconf_names["SC_CLK_TCK"])

    def _kill_process_group(self, proc, start_time, cpu_idle):
        """Send SIGTERM to the process group, wait grace period, then SIGKILL."""
        elapsed = time.monotonic() - start_time

        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass

        grace_deadline = time.monotonic() + self.grace_kill_seconds
        sigkilled = False

        while time.monotonic() < grace_deadline:
            if proc.poll() is not None:
                break
            time.sleep(0.1)

        if proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                sigkilled = True
            except (OSError, ProcessLookupError):
                pass
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass

        reason = "cpu_idle" if cpu_idle else "wall_clock"
        logger.warning(
            "generation subprocess %s killed after %.1fs (%s, pid=%d, sigkill=%s)",
            self.args[0],
            elapsed,
            reason,
            proc.pid,
            sigkilled,
        )

    def _completed(self, proc):
        return subprocess.CompletedProcess(
            args=self.args,
            returncode=proc.returncode,
            stdout=proc.stdout.read() if proc.stdout else "",
            stderr=proc.stderr.read() if proc.stderr else "",
        )
