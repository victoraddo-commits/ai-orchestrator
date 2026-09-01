"""KAI World Model — JARVIS P4.

A persistent operational model of the environment (§11/§12): entities
(nodes, guests, docker containers, services, operations, workers) plus typed
DEPENDENCY EDGES between them, so impact queries work:

    "If the P40 fails, what is affected?"

Sources are the existing verified collectors — proxmox_registry, docker,
workforce registry, money-center ops — never invented state. State is
snapshotted to memory/world_model.json with change detection vs previous
snapshot (current / previous / changed).

Impact traversal follows edges in the direction "depends_on". If X fails,
everything that transitively depends on X is impacted.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

_MEMORY_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "memory"
WORLD_PATH = _MEMORY_DIR / "world_model.json"

# ---------------------------------------------------------------------------
# Static dependency knowledge — the relationships discovery can't infer.
# This encodes real topology as of 2026-08-24; update when infra changes.
# ---------------------------------------------------------------------------
STATIC_EDGES = [
    # (source depends_on target)  — if TARGET fails, SOURCE is impacted
    {"src": "host:pve-a", "dst": "guest:100", "kind": "hosts", "note": "claude-code LXC (Kai Brain host)"},
    {"src": "host:pve-b", "dst": "ct:100", "kind": "hosts", "note": "kai-legal-brain"},
    {"src": "host:pve-b", "dst": "ct:101", "kind": "hosts", "note": "it-manager"},
    {"src": "host:pve-b", "dst": "ct:102", "kind": "hosts", "note": "network-core-b"},
    {"src": "host:pve-b", "dst": "ct:103", "kind": "hosts", "note": "bet-susu"},
    {"src": "host:pve-b", "dst": "ct:104", "kind": "hosts", "note": "net-services (NPM+AdGuard)"},
    {"src": "host:pve-b", "dst": "ct:105", "kind": "hosts", "note": "deerude-site"},
    {"src": "host:pve-b", "dst": "ct:106", "kind": "hosts", "note": "talent"},
    {"src": "host:pve-b", "dst": "ct:107", "kind": "hosts", "note": "kai-vault"},
    {"src": "host:pve-b", "dst": "ct:108", "kind": "hosts", "note": "kai-money"},
    {"src": "host:pve-b", "dst": "ct:109", "kind": "hosts", "note": "kai-android-factory"},
    {"src": "host:pve-b", "dst": "ct:110", "kind": "hosts", "note": "kai-browser sandbox"},
    {"src": "svc:kai-browser", "dst": "ct:110", "kind": "runs_on"},
    {"src": "app:android-factory", "dst": "ct:109", "kind": "runs_on"},
    # Tailscale VPN backbone (replaced ZeroTier 2026-08-30)
    {"src": "host:pve-a", "dst": "host:pve-b", "kind": "transit",
     "note": "A<->B via Tailscale (100.89.97.76 on pve-b)"},
    # Deprecated: network-core-b was the ZeroTier relay LXC; removed 2026-08-30
    # Deprecated: zerotier-backbone service entity; removed 2026-08-30
    {"src": "service:npm-ct104", "dst": "ct:104", "kind": "runs_on"},
    {"src": "service:vault-api", "dst": "ct:107", "kind": "runs_on"},
    {"src": "app:kai-money", "dst": "ct:108", "kind": "runs_on"},
    {"src": "app:kai-vault", "dst": "ct:107", "kind": "runs_on"},
    {"src": "app:susu", "dst": "ct:103", "kind": "runs_on"},
    {"src": "app:kai-betting", "dst": "ct:103", "kind": "runs_on"},
    {"src": "app:it-manager", "dst": "ct:101", "kind": "runs_on"},
    {"src": "app:proxdash", "dst": "ct:104", "kind": "runs_on"},
    {"src": "app:deerude-site", "dst": "ct:105", "kind": "runs_on"},
    {"src": "app:kai-android-factory", "dst": "ct:109", "kind": "runs_on"},
    # service-level: NPM fronts everything public
    {"src": "public:vault.sso.deerude.com", "dst": "service:npm-ct104", "kind": "fronted_by"},
    {"src": "public:it.local", "dst": "service:npm-ct104", "kind": "fronted_by"},
    {"src": "public:susu.local", "dst": "service:npm-ct104", "kind": "fronted_by"},
    {"src": "public:bet.local", "dst": "service:npm-ct104", "kind": "fronted_by"},
    {"src": "public:kai.local", "dst": "service:npm-ct104", "kind": "fronted_by"},
    # money ecosystem internals (CT108 compose)
    {"src": "app:kai-money", "dst": "svc:money-db", "kind": "depends_on"},
    {"src": "app:kai-money", "dst": "svc:ai-orchestrator", "kind": "notifies_via"},  # was kai-notify, merged
    {"src": "op:quant", "dst": "app:kai-money", "kind": "reports_to"},
    {"src": "op:automatron", "dst": "app:kai-money", "kind": "reports_to"},
    {"src": "op:defi", "dst": "app:kai-money", "kind": "reports_to"},
    {"src": "op:arbitrage", "dst": "app:kai-money", "kind": "reports_to"},
    {"src": "op:market-making", "dst": "app:kai-money", "kind": "reports_to"},
    {"src": "op:franklin", "dst": "app:kai-money", "kind": "reports_to"},
    {"src": "op:franklin", "dst": "svc:ollama-local", "kind": "uses_model"},
    {"src": "svc:ollama-local", "dst": "host:pve-a", "kind": "runs_on"},
    # brain dependencies
    {"src": "app:kai-brain", "dst": "ct:100", "kind": "runs_on"},
    {"src": "app:kai-brain", "dst": "svc:kai-vault-api", "kind": "secrets_from"},
    {"src": "svc:kai-vault-api", "dst": "ct:107", "kind": "runs_on"},
    {"src": "device:s23-ultra", "dst": "app:kai-brain", "kind": "controlled_by"},
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def collect_entities() -> dict:
    """Gather live entities from existing verified collectors. Best-effort per
    source: one failing collector must not blank the whole model."""
    entities = {}

    # Proxmox nodes + guests (both hosts)
    try:
        from core.proxmox_monitor import PROXMOX_NODES
        from core.proxmox_registry import discover_node_inventory
        for n in PROXMOX_NODES:
            inv = discover_node_inventory(n)
            nid = f"host:{n['name']}"
            entities[nid] = {
                "type": "proxmox_node", "label": n["name"], "host": n.get("host"),
                "status": "online" if inv.get("reachable") else "unreachable",
            }
            for c in inv.get("containers", []):
                gid = f"ct:{c.get('vmid')}"
                entities[gid] = {
                    "type": "lxc", "label": c.get("name"), "vmid": c.get("vmid"),
                    "node": n["name"], "status": c.get("status"),
                }
            for v in inv.get("vms", []):
                vid = f"vm:{v.get('vmid')}"
                entities[vid] = {
                    "type": "vm", "label": v.get("name"), "vmid": v.get("vmid"),
                    "node": n["name"], "status": v.get("status"),
                }
    except Exception:
        pass

    # Docker containers on this host
    try:
        import subprocess
        r = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}\t{{.Status}}"],
            capture_output=True, text=True, timeout=10)
        for line in r.stdout.strip().splitlines():
            name, _, status = line.partition("\t")
            entities[f"docker:{name}"] = {
                "type": "docker_container", "label": name,
                "status": "running" if "Up" in status else status.split()[0].lower(),
                "node": "pve-a",
            }
    except Exception:
        pass

    # AI workforce workers
    try:
        from core.workforce import registry
        for w in registry.list_workers() or []:
            wid = getattr(w, "worker_id", None) or (w.get("worker_id") if isinstance(w, dict) else None)
            if wid:
                st = getattr(w, "status", None) or (w.get("status") if isinstance(w, dict) else None)
                entities[f"worker:{wid}"] = {
                    "type": "ai_worker", "label": wid, "status": st,
                }
    except Exception:
        pass

    # Known logical services (always present so static edges resolve even if
    # a probe is down — their STATUS comes from probes when available).
    for sid, label, st in [
        ("svc:kai-vault-api", "Kai Vault API (CT107 :8120)", None),
        ("svc:npm-ct104", "Nginx Proxy Manager (CT104)", None),
        ("svc:ollama-local", "Ollama local models (.109 :11434)", None),
        ("svc:money-db", "Money Center Postgres (CT108)", None),
        ("svc:kai-notify", "KAI Notify hub (DEPRECATED — merged into ai-orchestrator)", None),
    ]:
        entities.setdefault(sid, {"type": "service", "label": label, "status": "unknown"})
    entities.setdefault("svc:kai-browser", {"type": "service", "label": "KAI Browser Sandbox (CT110 :8140)", "status": "unknown"})
    for aid, label in [
        ("app:kai-brain", "KAI Orchestrator"), ("app:kai-money", "Money Center"),
        ("app:kai-vault", "Kai Vault"), ("app:susu", "SUSU"), ("app:kai-betting", "Kai Betting"),
        ("app:it-manager", "IT Manager"), ("app:proxdash", "ProxDash"),
        ("app:deerude-site", "Deerude site"), ("app:kai-android-factory", "Android Factory"),
        ("app:android-factory", "Android Factory app layer"),
    ]:
        entities.setdefault(aid, {"type": "application", "label": label, "status": "unknown"})
    entities.setdefault("device:s23-ultra", {"type": "device", "label": "S23 Ultra", "status": "unknown"})
    for op in ("quant", "automatron", "defi", "arbitrage", "market-making", "franklin"):
        entities.setdefault(f"op:{op}", {"type": "money_operation", "label": op, "status": "unknown"})

    return entities


def build_snapshot() -> dict:
    """Collect live state, diff against previous snapshot, persist."""
    previous = _load()
    entities = collect_entities()

    prev_status = {eid: e.get("status") for eid, e in (previous.get("entities") or {}).items()}
    changes = []
    for eid, e in entities.items():
        cur = e.get("status")
        old = prev_status.get(eid)
        if old is not None and old != cur:
            changes.append({"entity": eid, "from": old, "to": cur})

    snapshot = {
        "schema_version": 1,
        "updated_at": _now_iso(),
        "entities": entities,
        "edges": STATIC_EDGES,
        "changes_since_previous": changes,
        "counts": {
            "entities": len(entities),
            "by_type": _count_by_type(entities),
        },
    }
    _save(snapshot)
    return snapshot


def _count_by_type(entities: dict) -> dict:
    out: dict[str, int] = {}
    for e in entities.values():
        t = e.get("type", "?")
        out[t] = out.get(t, 0) + 1
    return out


def _load() -> dict:
    try:
        with open(WORLD_PATH) as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save(snap: dict) -> None:
    tmp = WORLD_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(snap, default=str))
    os.replace(tmp, WORLD_PATH)


# --- queries ----------------------------------------------------------------

def get_state(entity_id: str | None = None) -> dict:
    snap = _load()
    if entity_id:
        e = (snap.get("entities") or {}).get(entity_id)
        return {"entity_id": entity_id, "entity": e}
    return {"updated_at": snap.get("updated_at"), **(snap.get("counts") or {}),
            "changes_since_previous": snap.get("changes_since_previous", [])}


def impact_of(entity_id: str) -> dict:
    """If entity_id fails, what is affected?

    Two propagation rules over the edge graph (edges point src depends_on dst):
      1. CONTAINMENT (hosting/running): if X fails, everything X hosts/runs
         fails too — traverse src side where kind in containment kinds
         (hosts / runs_on / fronted_by chains collapse upward first).
      2. DEPENDENCY: anything that transitively depends_on a failed thing is
         impacted — reverse traversal of dst→src.
    Both propagate together BFS-style; hop count = blast-radius depth.
    """
    snap = _load()
    if not snap:
        snap = build_snapshot()
    edges = snap.get("edges") or []

    # Only physical hosting is containment (failure flows DOWNWARD: lose the
    # node, you lose its guests). Every other edge kind means the src side
    # DEPENDS on dst being alive — failure propagates UPWARD to src.
    containment = {"hosts"}
    down_adj: dict[str, list[str]] = {}   # failed -> [things it hosts]
    up_adj: dict[str, list[str]] = {}     # failed -> [things depending on it]
    for e in edges:
        if e.get("kind") in containment:
            down_adj.setdefault(e["src"], []).append(e["dst"])
        else:
            up_adj.setdefault(e["dst"], []).append(e["src"])

    seen, frontier, levels = set(), [entity_id], {}
    depth = 0
    while frontier and depth < 8:
        nxt = []
        for node in frontier:
            # downward: children of a failed node fail
            for child in down_adj.get(node, []):
                if child not in seen and child != entity_id:
                    seen.add(child); levels[child] = depth + 1; nxt.append(child)
            # upward: dependents of a failed node are impacted (not dead)
            for dependent in up_adj.get(node, []):
                if dependent not in seen and dependent != entity_id:
                    seen.add(dependent); levels[dependent] = depth + 1; nxt.append(dependent)
            # bidirectional bridge: a dead dependent also kills ITS children,
            # and a dead child impacts ITS dependents (handled by continuing BFS)
        frontier = nxt
        depth += 1

    entities = snap.get("entities") or {}
    out = []
    for k, v in sorted(levels.items(), key=lambda kv: kv[1]):
        etype = (entities.get(k) or {}).get("type", "?")
        out.append({"entity": k, "type": etype, "severity_hops": v,
                    "status": (entities.get(k) or {}).get("status"),
                    "impact": "down" if etype in ("lxc", "vm", "docker_container") else "degraded"})
    return {
        "failed": entity_id,
        "impacted_count": len(out),
        "impacted": out,
        "note": "hypothesis based on dependency graph; verify before acting",
    }
