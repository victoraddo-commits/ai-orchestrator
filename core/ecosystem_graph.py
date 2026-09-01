"""Ecosystem graph — runtime state for KAI capability registry.

Follows the same patterns as core/network_knowledge.py:
- _get_memory_dir() resolves to memory/ relative to this file
- Atomic save via temp file + os.replace
- .bak backup
- load_graph() returns empty structure if no file
"""

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Path helpers ──────────────────────────────────────────────────────────────

def _get_memory_dir() -> Path:
    env = os.environ.get("AI_ORCHESTRATOR_MEMORY_DIR")
    if env:
        return Path(env)
    return Path(__file__).parent.parent / "memory"

def _get_graph_file() -> Path:
    return _get_memory_dir() / "ecosystem_graph.json"

def _get_bak_file() -> Path:
    return _get_memory_dir() / "ecosystem_graph.json.bak"

# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA_VERSION = 1

def _empty_graph() -> dict:
    return {
        "schema_version": _SCHEMA_VERSION,
        "entities": {},      # id -> entity dict
        "capabilities": {},   # id -> capability dict
        "relationships": [], # list of relationship dicts
        "last_updated": None,
    }

# ── Load / Save ───────────────────────────────────────────────────────────────

def load_graph() -> dict:
    graph_file = _get_graph_file()
    if not graph_file.exists():
        return _empty_graph()
    try:
        with open(graph_file) as f:
            return json.load(f)
    except json.JSONDecodeError:
        return _empty_graph()

def save_graph(graph: dict) -> None:
    graph["schema_version"] = _SCHEMA_VERSION
    graph["last_updated"] = datetime.now(timezone.utc).isoformat()
    graph_file = _get_graph_file()
    tmp_file = graph_file.with_suffix(f".tmp.{os.getpid()}.{id(graph_file)}")
    bak_file = _get_bak_file()
    # Atomic write: temp file + replace
    with open(tmp_file, "w") as f:
        json.dump(graph, f, indent=2)
    shutil.move(str(tmp_file), str(graph_file))
    # Backup
    if bak_file.exists():
        bak_file.unlink()
    shutil.copy(str(graph_file), str(bak_file))

def update_graph(mutate_fn) -> dict:
    """Atomic read-modify-write via caller-provided mutate function.

    Uses flock to prevent concurrent writes from racing (e.g. the Telegram
    poller and the orchestrator cycle both touching the graph simultaneously).
    """
    import fcntl
    graph_file = _get_graph_file()
    lock_file = graph_file.with_suffix(graph_file.suffix + ".lock")
    graph = load_graph()
    result = mutate_fn(graph)
    with open(lock_file, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            save_graph(graph)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)
    return result

# ── Entity operations ─────────────────────────────────────────────────────────

def add_entity(entity: dict) -> None:
    if "id" not in entity:
        raise ValueError("entity must have an 'id' field")
    def _mutate(g):
        g["entities"][entity["id"]] = entity
    update_graph(_mutate)

def update_entity(entity_id: str, updates: dict) -> bool:
    found = False
    def _mutate(g):
        nonlocal found
        if entity_id in g["entities"]:
            g["entities"][entity_id].update(updates)
            found = True
    update_graph(_mutate)
    return found

def get_entity(entity_id: str) -> dict | None:
    graph = load_graph()
    return graph["entities"].get(entity_id)

def list_entities(status: str | None = None) -> list[dict]:
    graph = load_graph()
    if status is None:
        return list(graph["entities"].values())
    return [e for e in graph["entities"].values() if e.get("status") == status]

# ── Capability operations ──────────────────────────────────────────────────────

def add_capability(cap: dict) -> None:
    if "id" not in cap:
        raise ValueError("entity must have an 'id' field")
    def _mutate(g):
        g["capabilities"][cap["id"]] = cap
    update_graph(_mutate)

def get_capability(cap_id: str) -> dict | None:
    graph = load_graph()
    return graph["capabilities"].get(cap_id)

def list_capabilities(status: str | None = None) -> list[dict]:
    graph = load_graph()
    if status is None:
        return list(graph["capabilities"].values())
    return [c for c in graph["capabilities"].values() if c.get("status") == status]

# ── Relationship operations ───────────────────────────────────────────────────

def add_relationship(rel: dict) -> None:
    def _mutate(g):
        # Avoid duplicates
        if not any(r.get("from") == rel.get("from") and r.get("to") == rel.get("to") and r.get("type") == rel.get("type") for r in g["relationships"]):
            g["relationships"].append(rel)
    update_graph(_mutate)

def get_relationships(from_entity: str | None = None, to_entity: str | None = None, rel_type: str | None = None) -> list[dict]:
    graph = load_graph()
    results = graph["relationships"]
    if from_entity:
        results = [r for r in results if r.get("from") == from_entity]
    if to_entity:
        results = [r for r in results if r.get("to") == to_entity]
    if rel_type:
        results = [r for r in results if r.get("type") == rel_type]
    return results

# ── Change detection ───────────────────────────────────────────────────────────

def detect_changes(old_graph: dict, new_graph: dict) -> dict:
    """Return dict with added/removed/changed entities, capabilities, relationships."""
    old_ents = old_graph.get("entities", {})
    new_ents = new_graph.get("entities", {})
    old_caps = old_graph.get("capabilities", {})
    new_caps = new_graph.get("capabilities", {})
    old_rels = old_graph.get("relationships", [])
    new_rels = new_graph.get("relationships", [])

    def rel_key(r): return (r.get("from"), r.get("to"), r.get("type"))

    added_ents = {k: v for k, v in new_ents.items() if k not in old_ents}
    removed_ents = {k: v for k, v in old_ents.items() if k not in new_ents}
    changed_ents = {
        k: v for k, v in new_ents.items()
        if k in old_ents and v != old_ents[k]
    }
    added_caps = {k: v for k, v in new_caps.items() if k not in old_caps}
    removed_caps = {k: v for k, v in old_caps.items() if k not in new_caps}
    added_rels = [r for r in new_rels if rel_key(r) not in {rel_key(r2) for r2 in old_rels}]
    removed_rels = [r for r in old_rels if rel_key(r) not in {rel_key(r2) for r2 in new_rels}]

    # Detect changed relationships: same from/to/type but different other fields
    old_rel_map = {rel_key(r): r for r in old_rels}
    changed_rels = []
    for r in new_rels:
        k = rel_key(r)
        if k in old_rel_map and r != old_rel_map[k]:
            changed_rels.append(r)

    return {
        "added": {"entities": added_ents, "capabilities": added_caps, "relationships": added_rels},
        "removed": {"entities": removed_ents, "capabilities": removed_caps, "relationships": removed_rels},
        "changed": {"entities": changed_ents, "relationships": changed_rels},
    }

# ── Blast radius ──────────────────────────────────────────────────────────────

def get_blast_radius(entity_id: str) -> dict:
    """Calculate blast radius for an entity: how many relationships point to it (readers/consumers)."""
    graph = load_graph()
    incoming = [r for r in graph["relationships"] if r.get("to") == entity_id]
    count = len(incoming)
    if count == 0:
        level = "none"
    elif count >= 5:
        level = "high"
    elif count >= 2:
        level = "medium"
    else:
        level = "low"
    return {"entity_id": entity_id, "level": level, "count": count, "incoming": incoming}

# ── YAML export ───────────────────────────────────────────────────────────────

def export_to_yaml(output_path: Path | str) -> None:
    """Export current runtime graph to a YAML file."""
    import yaml
    graph = load_graph()
    with open(output_path, "w") as f:
        yaml.dump(graph, f, default_flow_style=False, sort_keys=False)
