"""Duplication + orphan analysis over the live capability graph (KAI 2.0).

A duplicate cluster is a set of modules whose names collapse to the same
normalized stem (singular/plural, handler/manager/route suffixes stripped).
An orphan is a core module that imports nothing from core and is imported
by nothing (zero edges both ways)."""
from __future__ import annotations

import re
from pathlib import Path
from collections import defaultdict

try:
    from core import live_graph
except ImportError:  # direct sys.path (tests) context
    import live_graph

_ROLE_RE = re.compile(
    r"_(handler|manager|route|routes|worker|runner|helper|util|utils)$"
    r"|(handler|manager|worker)$")


def _norm(name: str) -> str:
    n = name.lower()
    n = _ROLE_RE.sub("", n)
    return n[:-1] if n.endswith("s") else n


def find_dupes() -> list[dict]:
    g = live_graph.build_graph()
    clusters = defaultdict(set)
    for node in g["nodes"]:
        if not node.startswith("core:"):
            continue
        clusters[_norm(node.split(":", 1)[1])].add(node)
    out = []
    for stem, members in sorted(clusters.items()):
        if len(members) > 1:
            out.append({"cluster": stem,
                        "members": sorted(members),
                        "action": "AUDIT-MERGE"})
    return out


def find_orphans() -> list[str]:
    g = live_graph.build_graph()
    connected = set()
    for a, b, _ in g["edges"]:
        connected.add(a)
        connected.add(b)
    return sorted(n for n in g["node_ids"]
                  if n.startswith("core:") and n not in connected
                  and n != "core:__init__")
