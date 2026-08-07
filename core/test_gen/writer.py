"""Writer: write generated test files returned by the coding agent.

The coding agent writes files to the project path during its run.
This module provides utilities to discover what was written and
manage the output.
"""

import os
from pathlib import Path


def discover_generated_tests(test_dir):
    """Return a list of test files found in the given directory.

    Only includes files matching test_*.py pattern, excluding __init__.py.
    """
    if not test_dir.is_dir():
        return []

    files = []
    for entry in sorted(test_dir.iterdir()):
        if entry.is_file() and entry.name.startswith("test_") and entry.suffix == ".py":
            files.append(entry)

    return files


def ensure_test_dir(test_dir):
    """Create the test output directory, including parent dirs."""
    os.makedirs(str(test_dir), exist_ok=True)


def write_test_file(filepath, content):
    """Write a test file atomically (temp file + os.replace)."""
    filepath = Path(filepath)
    tmp = filepath.with_suffix(filepath.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(filepath)
