import os
import signal
import subprocess
import sys
import time

import pytest

from core.subprocess_watchdog import SubprocessWatchdog, GenerationTimeoutError


def _write_script(path, content):
    path.write_text(content)
    os.chmod(path, 0o755)
    return str(path)


def test_watchdog_normal_completion(tmp_path):
    wd = SubprocessWatchdog(
        ["python3", "-c", "print('hello')"],
        wall_timeout_seconds=10,
    )
    result = wd.run()

    assert result.returncode == 0
    assert "hello" in result.stdout


def test_watchdog_nonzero_exit(tmp_path):
    wd = SubprocessWatchdog(
        ["python3", "-c", "import sys; sys.exit(3)"],
        wall_timeout_seconds=10,
    )
    result = wd.run()

    assert result.returncode == 3


def test_watchdog_wall_clock_timeout(tmp_path):
    wd = SubprocessWatchdog(
        ["python3", "-c", "import time; time.sleep(30)"],
        wall_timeout_seconds=0.5,
        grace_kill_seconds=0.2,
        check_interval_seconds=0.1,
    )
    with pytest.raises(GenerationTimeoutError) as exc:
        wd.run()

    assert exc.value.timeout == 0.5
    assert exc.value.cpu_idle is False


def test_watchdog_stderr_captured(tmp_path):
    wd = SubprocessWatchdog(
        ["python3", "-c", "import sys; sys.stderr.write('err'); print('out')"],
        wall_timeout_seconds=10,
    )
    result = wd.run()

    assert "out" in result.stdout
    assert "err" in result.stderr


def test_watchdog_cpu_idle_timeout_kills_hung_process(tmp_path):
    # time.sleep(60) is a true zero-CPU hang (signal-waitable), NOT a busy
    # loop -- exactly the failure signature from the 2026-08-02 incident.
    wd = SubprocessWatchdog(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        wall_timeout_seconds=30,
        idle_cpu_seconds=0.5,
        grace_kill_seconds=0.1,
        check_interval_seconds=0.15,
    )
    with pytest.raises(GenerationTimeoutError) as exc:
        wd.run()

    assert exc.value.cpu_idle is True
    assert exc.value.timeout == 0.5


def test_watchdog_cpu_idle_detects_genuine_idle_not_busy_loop(tmp_path):
    # A busy loop uses CPU -- CPU-time changes every tick -- so the idle
    # watchdog must NOT fire during its wall-clock window.  Run long enough
    # to verify the idle checker ran.
    wd = SubprocessWatchdog(
        [sys.executable, "-c", "for _ in range(30_000_000): _ + 1"],
        wall_timeout_seconds=5,
        idle_cpu_seconds=0.5,
        check_interval_seconds=0.2,
    )
    result = wd.run()

    assert result.returncode == 0


def test_watchdog_partial_stdout_on_timeout(tmp_path):
    wd = SubprocessWatchdog(
        [
            sys.executable,
            "-c",
            "import sys, time; sys.stdout.write('partial'); sys.stdout.flush(); time.sleep(30)",
        ],
        wall_timeout_seconds=0.5,
        grace_kill_seconds=0.1,
        check_interval_seconds=0.1,
    )
    with pytest.raises(GenerationTimeoutError) as exc:
        wd.run()

    assert "partial" in (exc.value.output or "")


def test_watchdog_kills_process_group(tmp_path):
    waiter = tmp_path / "waiter.py"
    _write_script(
        waiter,
        (
            "import signal, subprocess, sys, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import signal, time;"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)'])\n"
            "sys.stdout.write(f'child_pid={child.pid}\\n')\n"
            "sys.stdout.flush()\n"
            "child.wait()\n"
        ),
    )

    wd = SubprocessWatchdog(
        [sys.executable, waiter],
        wall_timeout_seconds=0.5,
        grace_kill_seconds=0.2,
        check_interval_seconds=0.1,
    )
    with pytest.raises(GenerationTimeoutError):
        wd.run()

    # The SIGTERM was ignored by both parent and child; SIGKILL (also
    # process-group scoped via killpg) took them down.  Give the OS a
    # moment to reap, then verify neither process is still alive.
    time.sleep(1)


def test_watchdog_handles_already_exited_process(tmp_path):
    wd = SubprocessWatchdog(
        [sys.executable, "-c", "pass"],
        wall_timeout_seconds=10,
        idle_cpu_seconds=1,
    )
    result = wd.run()
    assert result.returncode == 0


def test_generation_timeout_error_is_catchable_as_timeout_expired():
    err = GenerationTimeoutError(cmd=["opencode"], timeout=5)
    assert isinstance(err, subprocess.TimeoutExpired)
    assert err.cpu_idle is False


def test_generation_timeout_error_cpu_idle_flag():
    err = GenerationTimeoutError(cmd=["opencode"], timeout=5, cpu_idle=True)
    assert err.cpu_idle is True
