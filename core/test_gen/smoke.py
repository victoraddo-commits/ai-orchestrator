"""Smoke test: end-to-end fixture for verifying coding generation.

Provides a fixed fixture source file plus a complete generate-verify
pipeline that can be run as a canary test.

The fixture is a self-contained module with known-answer functions.
The smoke test generates tests for it and verifies they pass.
"""

import os
from pathlib import Path


SMOKE_FIXTURE = r'''"""Smoke-test fixture module for test-gen verification.

This module provides simple, known-answer functions that a coding agent
should be able to generate correct tests for.
"""


def add(a, b):
    """Return the sum of a and b."""
    return a + b


def multiply(a, b):
    """Return the product of a and b."""
    return a * b


def divide(a, b):
    """Return a divided by b.

    Raises ValueError if b is zero.
    """
    if b == 0:
        raise ValueError("Cannot divide by zero")
    return a / b


def reverse_string(s):
    """Return the reversed version of string s."""
    return s[::-1]


def is_palindrome(s):
    """Return True if s reads the same forwards and backwards.

    Case-sensitive. Empty string returns True.
    """
    return s == s[::-1]
'''


def write_smoke_fixture(target_path):
    """Write the smoke test fixture module to disk."""
    os.makedirs(str(target_path.parent), exist_ok=True)
    target_path.write_text(SMOKE_FIXTURE, encoding="utf-8")


def run_smoke(provider_name="opencode_claude", timeout=None, keep_output=False):
    """Run the full end-to-end smoke test.

    1. Write the fixture module.
    2. Generate tests via the coding provider.
    3. Verify generated tests pass.

    Returns dict with keys: fixture_module, generation_result, verification_result, passed.
    """
    import shutil
    import tempfile
    from core.test_gen.generator import generate_tests
    from core.test_gen.verifier import run_tests

    scratch_dir = Path(tempfile.mkdtemp(prefix="test_gen_smoke_"))
    fixture_dir = scratch_dir / "fixture_src"
    test_dir = scratch_dir / "tests"

    try:
        fixture_dir.mkdir(parents=True, exist_ok=True)
        test_dir.mkdir(parents=True, exist_ok=True)

        fixture_path = fixture_dir / "math_utils.py"
        fixture_path.write_text(SMOKE_FIXTURE, encoding="utf-8")

        (test_dir / "__init__.py").write_text("")

        project_path = str(scratch_dir)
        gen_result = generate_tests(
            source_files=[fixture_path],
            test_dir=test_dir,
            provider_name=provider_name,
            timeout=timeout,
            project_path=project_path,
        )

        if not gen_result.get("success"):
            return {
                "fixture_module": str(fixture_path),
                "generation_result": gen_result,
                "verification_result": None,
                "passed": False,
            }

        verify_result = run_tests(test_dir, cwd=project_path)

        passed = verify_result.get("passed", False)

        return {
            "fixture_module": str(fixture_path),
            "generation_result": gen_result,
            "verification_result": verify_result,
            "passed": passed,
        }
    finally:
        if not keep_output:
            shutil.rmtree(scratch_dir, ignore_errors=True)
