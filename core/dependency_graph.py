"""Phase 19U: Dependency Graph - Cross-project relationship mapping.

Models relationships between projects, artifacts, services, modules, teams,
and environments. Supports dependency queries, impact analysis, and cycle
detection. Persists to memory/dependency_graph.json via the existing memory
system.

Node types: Project, Artifact, Service, Module, Team, Environment
Edge types: DEPENDS_ON, IMPORTS, CALLS, PRODUCES, DEPLOYED_TO, OWNS
"""

from collections import deque
from datetime import datetime

from core.id_generator import generate_id
from core.logger import info
from core.memory import load, save

GRAPH_FILE = "dependency_graph.json"

NODE_TYPES = frozenset({
    "Project", "Artifact", "Service", "Module", "Team", "Environment",
})

EDGE_TYPES = frozenset({
    "DEPENDS_ON", "IMPORTS", "CALLS", "PRODUCES", "DEPLOYED_TO", "OWNS",
})


def _empty_graph():
    return {
        "nodes": {},
        "edges": [],
        "last_modified": datetime.now().isoformat(),
    }


def _load_graph():
    graph = load(GRAPH_FILE)
    if not isinstance(graph, dict) or "nodes" not in graph or "edges" not in graph:
        return _empty_graph()
    return graph


def _save_graph(graph):
    graph["last_modified"] = datetime.now().isoformat()
    save(GRAPH_FILE, graph)


def _build_adjacency(edges, reverse=False):
    adj = {}
    for edge in edges:
        from_id = edge["to"] if reverse else edge["from"]
        to_id = edge["from"] if reverse else edge["to"]
        adj.setdefault(from_id, []).append(to_id)
    return adj


def _bfs(start_ids, adj, max_depth=None):
    visited = set(start_ids)
    queue = deque((node_id, 1) for node_id in start_ids)
    result = dict.fromkeys(start_ids, 0)
    while queue:
        node_id, depth = queue.popleft()
        if max_depth is not None and depth > max_depth:
            continue
        for neighbor in adj.get(node_id, []):
            if neighbor not in visited:
                visited.add(neighbor)
                result[neighbor] = depth
                queue.append((neighbor, depth + 1))
    return result


def _dfs_cycle(root, adj, visited, stack, path_set):
    visited.add(root)
    stack.add(root)
    path_set.add(root)
    for neighbor in adj.get(root, []):
        if neighbor not in visited:
            cycle = _dfs_cycle(neighbor, adj, visited, stack, path_set)
            if cycle is not None:
                return cycle
        elif neighbor in stack:
            return [neighbor]
    stack.discard(root)
    path_set.discard(root)
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_graph():
    return _load_graph()


def get_node(node_id):
    graph = _load_graph()
    return graph["nodes"].get(node_id)


def add_node(node_type, name, **fields):
    if node_type not in NODE_TYPES:
        raise ValueError(f"Unknown node_type {node_type!r}. Must be one of: {', '.join(sorted(NODE_TYPES))}")

    graph = _load_graph()

    node_id = generate_id()
    now = datetime.now().isoformat()

    node = {
        "id": node_id,
        "type": node_type,
        "name": name,
        "created": now,
        "updated": now,
    }
    node.update(fields)

    graph["nodes"][node_id] = node
    _save_graph(graph)

    info(f"dependency_graph: added {node_type} node {name!r} ({node_id})")
    return node


def update_node(node_id, **fields):
    graph = _load_graph()
    node = graph["nodes"].get(node_id)
    if node is None:
        return None

    for key in ("id", "type", "created"):
        fields.pop(key, None)

    node.update(fields)
    node["updated"] = datetime.now().isoformat()
    _save_graph(graph)
    return node


def remove_node(node_id):
    graph = _load_graph()
    if node_id not in graph["nodes"]:
        return False

    del graph["nodes"][node_id]
    graph["edges"] = [
        e for e in graph["edges"]
        if e["from"] != node_id and e["to"] != node_id
    ]
    _save_graph(graph)
    return True


def add_edge(from_id, to_id, edge_type, **fields):
    if edge_type not in EDGE_TYPES:
        raise ValueError(f"Unknown edge_type {edge_type!r}. Must be one of: {', '.join(sorted(EDGE_TYPES))}")

    graph = _load_graph()

    if from_id not in graph["nodes"]:
        raise ValueError(f"Source node {from_id!r} not found")
    if to_id not in graph["nodes"]:
        raise ValueError(f"Target node {to_id!r} not found")

    for existing in graph["edges"]:
        if existing["from"] == from_id and existing["to"] == to_id and existing["type"] == edge_type:
            return existing

    edge = {
        "from": from_id,
        "to": to_id,
        "type": edge_type,
        "created": datetime.now().isoformat(),
    }
    edge.update(fields)

    graph["edges"].append(edge)
    _save_graph(graph)

    info(f"dependency_graph: added {edge_type} edge {from_id!r} -> {to_id!r}")
    return edge


def remove_edge(from_id, to_id, edge_type):
    graph = _load_graph()
    before = len(graph["edges"])
    graph["edges"] = [
        e for e in graph["edges"]
        if not (e["from"] == from_id and e["to"] == to_id and e["type"] == edge_type)
    ]
    if len(graph["edges"]) == before:
        return False
    _save_graph(graph)
    return True


def get_dependencies(node_id, edge_types=None, depth="direct"):
    graph = _load_graph()
    if node_id not in graph["nodes"]:
        return {}

    edges = graph["edges"]
    if edge_types is not None:
        edge_types = set(edge_types)
        edges = [e for e in edges if e["type"] in edge_types]

    adj = _build_adjacency(edges, reverse=False)
    max_depth = None if depth == "all" else 1
    deps = _bfs([node_id], adj, max_depth=max_depth)

    result = {}
    for dep_id, dist in deps.items():
        if dep_id == node_id:
            continue
        node = graph["nodes"].get(dep_id)
        if node:
            result[dep_id] = {**node, "distance": dist}
    return result


def get_dependents(node_id, edge_types=None, transitive=False):
    graph = _load_graph()
    if node_id not in graph["nodes"]:
        return {}

    edges = graph["edges"]
    if edge_types is not None:
        edge_types = set(edge_types)
        edges = [e for e in edges if e["type"] in edge_types]

    adj = _build_adjacency(edges, reverse=True)
    max_depth = 1 if not transitive else None
    deps = _bfs([node_id], adj, max_depth=max_depth)

    result = {}
    for dep_id, dist in deps.items():
        if dep_id == node_id:
            continue
        node = graph["nodes"].get(dep_id)
        if node:
            result[dep_id] = {**node, "distance": dist}
    return result


def impact_analysis(node_id, depth=3, edge_types=None):
    impacted = get_dependents(node_id, edge_types=edge_types, transitive=True)

    filtered = {}
    for imp_id, node in impacted.items():
        if node["distance"] <= depth:
            filtered[imp_id] = node

    node = graph_get_node(node_id)
    return {
        "source": node,
        "impacted": filtered,
        "total_impacted": len(filtered),
        "max_depth_reached": max((n["distance"] for n in filtered.values()), default=0),
    }


def find_cycles():
    graph = _load_graph()
    adj = _build_adjacency(graph["edges"], reverse=False)
    visited = set()
    cycles = []

    for node_id in graph["nodes"]:
        if node_id not in visited:
            path = set()
            cycle_entry = _dfs_cycle(node_id, adj, visited, set(), set())
            if cycle_entry is not None and cycle_entry not in visited:
                cycles.append(_trace_cycle(cycle_entry, adj))

    return cycles


def _trace_cycle(entry_id, adj):
    cycle = [entry_id]
    stack = [entry_id]
    while stack:
        current = stack.pop()
        for neighbor in adj.get(current, []):
            if neighbor == entry_id and len(cycle) > 1:
                cycle.append(entry_id)
                return cycle
            if neighbor not in cycle:
                cycle.append(neighbor)
                stack.append(neighbor)
    return cycle


def graph_get_node(node_id):
    graph = _load_graph()
    return graph["nodes"].get(node_id)


def list_nodes(node_type=None):
    graph = _load_graph()
    nodes = graph["nodes"]
    if node_type is not None:
        nodes = {k: v for k, v in nodes.items() if v.get("type") == node_type}
    return nodes


def list_edges(edge_type=None):
    graph = _load_graph()
    edges = graph["edges"]
    if edge_type is not None:
        edges = [e for e in edges if e["type"] == edge_type]
    return edges


def search_nodes(query, node_type=None):
    graph = _load_graph()
    q = query.lower()
    result = {}
    for node_id, node in graph["nodes"].items():
        if node_type is not None and node.get("type") != node_type:
            continue
        if q in node["name"].lower() or q in node.get("description", "").lower():
            result[node_id] = node
    return result


def get_graph_stats():
    graph = _load_graph()
    node_count = len(graph["nodes"])
    edge_count = len(graph["edges"])
    type_counts = {}
    for node in graph["nodes"].values():
        t = node.get("type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1
    edge_type_counts = {}
    for edge in graph["edges"]:
        t = edge.get("type", "unknown")
        edge_type_counts[t] = edge_type_counts.get(t, 0) + 1
    return {
        "total_nodes": node_count,
        "total_edges": edge_count,
        "node_types": type_counts,
        "edge_types": edge_type_counts,
        "last_modified": graph.get("last_modified"),
    }
