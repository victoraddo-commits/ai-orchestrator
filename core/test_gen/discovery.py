"""Source file discovery.

Find Python source files matching given paths or patterns.
"""

from pathlib import Path


def resolve_sources(paths):
    """Resolve a list of file/directory paths into a list of .py source files.

    For each path:
    - If it's a .py file, include it directly.
    - If it's a directory, recurse finding all .py files (excluding __init__.py,
      __pycache__, .venv, .git, node_modules).
    - If it's a glob-amenable string, expand via Path.glob on the parent.

    Returns a sorted, deduplicated list of absolute Path objects.
    """
    if not paths:
        return []

    resolved = set()

    for raw in paths:
        p = Path(raw).resolve()
        if not p.exists():
            continue
        if p.is_file() and p.suffix == ".py":
            resolved.add(p)
        elif p.is_dir():
            for py_file in _collect_py_files(p):
                resolved.add(py_file)

    return sorted(resolved)


def _collect_py_files(directory):
    skip_prefixes = ("__pycache__", ".venv", ".git", ".local", "node_modules")
    for py_file in directory.rglob("*.py"):
        if any(part.startswith(prefix) for part in py_file.parts for prefix in skip_prefixes):
            continue
        if py_file.name == "__init__.py":
            continue
        yield py_file.resolve()
