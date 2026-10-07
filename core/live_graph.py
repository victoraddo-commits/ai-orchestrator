"""Capability graph over the live estate (KAI 2.0 upgrade).

Nodes from live_discovery.scan_live_estate(); edges from static import
analysis of core module sources. Handles:
- ``import X`` / ``import core.x``
- ``from X import a, b`` (X == "core" contributes every imported name)
- ``from core.x import ...``
"""
from __future__ import annotations

import re
from pathlib import Path
from collections import Counter

try:
    from core import live_discovery
except ImportError:  # direct sys.path (tests) context
    import live_discovery

_SRC_RE = re.compile(
    r"^\s*(?:from\s+([\w.]+)\s+import\s+\(?([^)\n]*)|import\s+([\w.]+))",
    re.MULTILINE)


def _core_module_names():
    names = set()
    root = Path("/opt/ai-orchestrator/core")
    for p in root.glob("*.py"):
        names.add(p.stem)
    for d in root.iterdir():
        if d.is_dir() and (d / "__init__.py").exists():
            names.add(d.name)
    return names


from pathlib import Path


def _candidates(m) -> list[str]:
    """Imported core-module candidates for one import statement."""
    if m.group(1):
        if m.group(2):
            toks = [t.strip().split()[0].split(".")[0]
                    for t in m.group(2).split(",") if t.strip()]
            if m.group(1) == "core":
                return toks
            dotted = m.group(1)
            if dotted.startswith("core."):
                dotted = dotted[len("core."):]
            return [dotted.split(".")[0]]
        return []
    dotted = m.group(3) or ""
    if dotted.startswith("core."):
        dotted = dotted[len("core."):]
    return [dotted.split(".")[0]] if dotted else []


def build_graph() -> dict:
    caps = live_discovery.scan_live_estate()
    nodes = [c["capability_id"] for c in caps]
    idset = {c["capability_id"] for c in caps}
    known = {n.split(":", 1)[1] for n in nodes if n.startswith("core:")}
    root = Path("/opt/ai-orchestrator/core")
    counter = Counter()
    edges = []
    for p in root.rglob("*.py"):
        if any(s in p.name.lower() for s in ("bak", "backup")):
            continue
        if any(part in ("__pycache__",) for part in p.parts):
            continue
        try:
            src = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        stem = p.stem if p.parent == root else p.parent.name
        for m in _SRC_RE.finditer(src):
            for name in _candidates(m):
                if name in known and name != stem:
                    edges.append((f"core:{stem}", f"core:{name}", "imports"))
                    counter[f"core:{name}"] += 1
    return {"nodes": nodes, "node_ids": idset, "edges": edges,
            "in_degree": dict(counter)}
