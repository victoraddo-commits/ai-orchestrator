"""Verifier: run generated tests and report pass/fail."""

import subprocess
import sys
from pathlib import Path


def run_tests(test_dir, runner=None, flags=None, cwd=None):
    """Execute the test runner against the generated test directory.

    Args:
        test_dir: Path to the generated test directory.
        runner: Test runner command (default: 'pytest').
        flags: List of flags to pass, e.g. ['-xvs'].
        cwd: Working directory for the test runner. Defaults to test_dir's
             parent's parent (project root in a standard layout).

    Returns:
        dict with keys: passed, returncode, stdout, stderr, test_files_checked.
    """
    from core.test_gen.config import DEFAULT_TEST_RUNNER, DEFAULT_TEST_FLAGS
    from core.test_gen.writer import discover_generated_tests

    if runner is None:
        runner = DEFAULT_TEST_RUNNER
    if flags is None:
        flags = DEFAULT_TEST_FLAGS

    test_files = discover_generated_tests(test_dir)
    if not test_files:
        return {
            "passed": False,
            "returncode": -1,
            "stdout": "",
            "stderr": "No generated test files found in " + str(test_dir),
            "test_files_checked": [],
        }

    if cwd is None:
        cwd = str(Path(test_dir).parent.parent)

    cmd = [sys.executable, "-m", runner, *flags, str(test_dir)]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300,
        cwd=cwd,
    )

    return {
        "passed": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "test_files_checked": [str(f) for f in test_files],
    }
