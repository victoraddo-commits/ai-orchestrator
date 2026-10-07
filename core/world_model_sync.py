"""World Model sync (roadmap 22L).

Upserts verified subsystems as entities and records relationships (edges) so
every VERIFIED subsystem has an entity and the World Model graph stays in
sync with the live estate.
"""
from __future__ import annotations

import time


def upsert_entity(world: dict, entity_id: str, kind: str, **attrs) -> dict:
    entities = world.setdefault("entities", {})
    record = entities.get(entity_id, {})
    record.update({"id": entity_id, "kind": kind, **attrs,
                   "updated_at": time.time()})
    entities[entity_id] = record
    return record


def add_edge(world: dict, src: str, dst: str, relation: str) -> dict:
    edges = world.setdefault("edges", [])
    edge = {"src": src, "dst": dst, "relation": relation}
    if edge not in edges:
        edges.append(edge)
    return edge


def sync_subsystems(world: dict, subsystems) -> dict:
    """Ensure every verified subsystem has an entity; link to its parent."""
    added, missing = [], []
    for sub in (subsystems or []):
        sid = sub.get("id")
        if not sid:
            continue
        if sub.get("verified") is not True:
            missing.append(sid)
            continue
        upsert_entity(world, sid, sub.get("kind", "subsystem"),
                      name=sub.get("name", sid),
                      verified=True)
        if sub.get("parent"):
            add_edge(world, sub["parent"], sid, "contains")
        added.append(sid)
    return {"added": added, "unverified": missing}
