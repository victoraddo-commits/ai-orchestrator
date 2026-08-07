"""Juris Kai Legal Assistant Tests - Security Boundary Validation

This file validates that juris_kai maintains the same security boundaries as law_tutor.
"""

import ast
import os
import sys
from pathlib import Path

import pytest

# Test constants (same as law_tutor tests)
JURIS_KAI_DIR = Path(__file__).resolve().parent.parent / "core" / "juris_kai"
FORBIDDEN_MODULES = {"core.build_manager", "core.approval", "core.deployment_manager"}

def _imported_modules(path):
    """Parse actual import statements - same as law_tutor test."""
    tree = ast.parse(path.read_text(), filename=str(path))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules

def test_no_forbidden_operational_imports_anywhere_in_the_source():
    """Validate no forbidden operational imports (same as law_tutor)."""
    for path in sorted(JURIS_KAI_DIR.glob("*.py")):
        imported = _imported_modules(path)
        overlap = imported & FORBIDDEN_MODULES
        assert not overlap, f"{path.name} imports forbidden operational module(s): {overlap}"

def test_no_forbidden_operational_modules_get_imported_at_runtime():
    """Validate no forbidden modules leaked into sys.modules (same as law_tutor)."""
    import subprocess

    script = (
        "import sys, core.juris_kai.bot; "
        "forbidden = {'core.build_manager', 'core.approval', 'core.deployment_manager'}; "
        "leaked = forbidden & set(sys.modules); "
        "print(','.join(sorted(leaked)))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    leaked = result.stdout.strip()
    assert not leaked, f"operational modules leaked into sys.modules: {leaked}"

# Additional juris_kai specific tests could be added here