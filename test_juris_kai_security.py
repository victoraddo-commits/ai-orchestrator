#!/usr/bin/env python3
"""Test that juris_kai security boundaries match law_tutor."""

import subprocess
import sys
from pathlib import Path

def test_security_boundaries():
    """Verify juris_kai maintains same security boundaries as law_tutor."""
    
    # Test that no forbidden modules are imported in juris_kai
    test_script = """
import ast
import sys
from pathlib import Path

# Define forbidden modules (same as law_tutor)
FORBIDDEN_MODULES = {"core.build_manager", "core.approval", "core.deployment_manager"}

# Check each juris_kai module
juris_kai_dir = Path(__file__).resolve().parent / "core" / "juris_kai"
for py_file in juris_kai_dir.glob("*.py"):
    with open(py_file, 'r') as f:
        content = f.read()
    
    # Parse AST to find imports
    tree = ast.parse(content, filename=str(py_file))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    
    # Check for forbidden imports
    overlap = modules & FORBIDDEN_MODULES
    if overlap:
        print(f"ERROR: {py_file.name} imports forbidden modules: {overlap}")
        sys.exit(1)

print("SUCCESS: No forbidden modules imported in juris_kai")
"""
    
    # Run the test
    result = subprocess.run([
        sys.executable, "-c", test_script
    ], cwd="/root/.ai-orchestrator/self-build-workspaces/c22d92a35839", 
       capture_output=True, text=True)
    
    if result.returncode != 0:
        print("Security test failed:")
        print(result.stdout)
        print(result.stderr)
        sys.exit(1)
    
    print("Security boundaries validated successfully!")

if __name__ == "__main__":
    test_security_boundaries()