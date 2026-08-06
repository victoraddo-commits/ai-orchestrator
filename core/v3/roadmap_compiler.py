"""Kai V3 Roadmap Compiler — Converts roadmap into executable tasks.

Input: roadmap.json phases
Output: Dependency graph, priority queue, executable task list

Phases only enter the execution queue when all dependencies are completed.
Blocked phases show "BLOCKED (waiting on: 15A, 17Z)" in Kai Command Center.

Priority scoring: (dependency_depth * 100) + priority
  - Lower number = more urgent
  - Deeply-blocked-away phases don't starve
"""

from collections import deque
from dataclasses import dataclass, field
import json

from core.logger import info


NON_TERMINAL_PHASE_STATUSES = {
    "pending", "in_progress", "proposed", "planned",
}

TERMINAL_PHASE_STATUSES = {
    "completed", "failed", "rolled_back",
}


@dataclass
class PhaseNode:
    """A node in the dependency graph representing a roadmap phase."""
    phase_id: str
    name: str
    status: str
    priority: int
    dependencies: list[str] = field(default_factory=list)
    description: str = ""
    acceptance_criteria: list[str] = field(default_factory=list)
    depth: int = 0  # Computed depth in DAG


@dataclass
class CompiledTask:
    """A task ready for execution by a worker."""
    phase_id: str
    name: str
    priority: int
    depth: int
    score: int  # Computed: (depth * 100) + priority
    dependencies: list[str]
    description: str
    status: str


def compile_roadmap(roadmap_path: str | None = None) -> dict:
    """Compile the roadmap into a dependency graph, priority queue, and task list.

    Args:
        roadmap_path: Path to roadmap.json. Defaults to memory/roadmap.json
                      via the memory API.

    Returns:
        {
            "dag": {phase_id: PhaseNode},
            "queue": [CompiledTask, ...],  # Sorted by execution order
            "tasks": [CompiledTask, ...],  # Ready-to-execute tasks
            "blocked": [CompiledTask, ...],  # Tasks waiting on dependencies
            "completed": [str, ...],  # Completed phase IDs
            "failed": [str, ...],  # Failed phase IDs
        }
    """
    phases = _load_roadmap(roadmap_path)

    # Build phase nodes
    nodes: dict[str, PhaseNode] = {}
    for phase in phases:
        phase_id = phase.get("id", "")
        if not phase_id:
            continue

        node = PhaseNode(
            phase_id=phase_id,
            name=phase.get("name", "unnamed"),
            status=phase.get("status", "pending"),
            priority=phase.get("priority", 50),
            dependencies=list(phase.get("dependencies", []) or []),
            description=phase.get("description", ""),
            acceptance_criteria=list(phase.get("acceptance_criteria", []) or []),
        )
        nodes[phase_id] = node

    # Compute dependency depths (longest path from any leaf)
    _compute_depths(nodes)

    # Classify phases
    completed: list[str] = []
    failed: list[str] = []
    ready: list[CompiledTask] = []
    blocked: list[CompiledTask] = []

    for phase_id, node in nodes.items():
        if node.status in ("completed",):
            completed.append(phase_id)
            continue
        if node.status in ("failed", "rolled_back"):
            failed.append(phase_id)
            continue

        task = _node_to_task(node)

        # Check if all dependencies are completed
        if _dependencies_met(node, completed):
            ready.append(task)
        else:
            # Determine what's blocking this phase
            missing = [d for d in node.dependencies if d not in completed]
            task.description = (f"BLOCKED (waiting on: {', '.join(missing)}) — "
                                f"{task.description}")
            blocked.append(task)

    # Sort ready tasks by score (lower = more urgent)
    ready.sort(key=lambda t: t.score)
    blocked.sort(key=lambda t: t.score)

    # Execution queue = ready first, then blocked (for visibility)
    queue = ready + blocked

    info(f"Roadmap compiled: {len(ready)} ready, {len(blocked)} blocked, "
         f"{len(completed)} completed, {len(failed)} failed")

    return {
        "dag": nodes,
        "queue": queue,
        "tasks": ready,
        "blocked": blocked,
        "completed": completed,
        "failed": failed,
    }


def get_next_task(compiled: dict, current_tasks: set[str] | None = None) -> CompiledTask | None:
    """Get the next ready task, excluding any already in progress.

    Args:
        compiled: Output from compile_roadmap()
        current_tasks: Set of phase IDs currently being worked on
    """
    current = current_tasks or set()
    for task in compiled["tasks"]:
        if task.phase_id not in current:
            return task
    return None


def get_pending_tasks_grouped(compiled: dict) -> dict[str, list[CompiledTask]]:
    """Group pending tasks by their status for the scheduler.

    Returns:
        {
            "GENERATING": [...],
            "CODE_REVIEW": [...],
            "DEPLOYING": [...],
            "BLOCKED": [...],
        }
    """
    # This grouping is used by the GPU manager to decide which pods to start
    grouped: dict[str, list[CompiledTask]] = {
        "GENERATING": [],
        "CODE_REVIEW": [],
        "DEPLOYING": [],
        "BLOCKED": [],
    }

    for task in compiled.get("tasks", []):
        grouped["GENERATING"].append(task)

    for task in compiled.get("blocked", []):
        grouped["BLOCKED"].append(task)

    return grouped


def get_phase_dependencies(compiled: dict, phase_id: str) -> list[str]:
    """Get all dependencies (direct + transitive) for a phase."""
    dag = compiled.get("dag", {})
    node = dag.get(phase_id)
    if not node:
        return []

    all_deps = set()
    queue = deque(node.dependencies)

    while queue:
        dep_id = queue.popleft()
        if dep_id in all_deps:
            continue
        all_deps.add(dep_id)
        dep_node = dag.get(dep_id)
        if dep_node:
            queue.extend(dep_node.dependencies)

    # Return in dependency order
    result = []
    for dep_id in node.dependencies:
        result.append(dep_id)
    result.extend(sorted(all_deps - set(node.dependencies)))

    return result


def is_phase_ready(compiled: dict, phase_id: str) -> bool:
    """Check if a phase's dependencies are all completed."""
    completed = set(compiled.get("completed", []))
    dag = compiled.get("dag", {})
    node = dag.get(phase_id)
    if not node:
        return False
    return _dependencies_met(node, completed)


def _load_roadmap(roadmap_path: str | None = None) -> list[dict]:
    """Load roadmap phases from file or memory API.

    Defaults to the project root roadmap.json (the authoritative source),
    NOT memory/roadmap.json (which may not exist or be stale).
    """
    if roadmap_path:
        from pathlib import Path
        data = json.loads(Path(roadmap_path).read_text())
    else:
        # Try project root first (systemd runs from /project/ai-orchestrator)
        from pathlib import Path as _Path
        root_roadmap = _Path("roadmap.json")
        if root_roadmap.exists():
            data = json.loads(root_roadmap.read_text())
        else:
            # Fall back to memory/roadmap.json
            from core.memory import load
            data = load("roadmap.json")

    if isinstance(data, dict):
        return data.get("phases", data.get("records", []))
    if isinstance(data, list):
        return data
    return []


def _compute_depths(nodes: dict[str, PhaseNode]):
    """Compute dependency depth for each node (longest path from a leaf).

    Leaf nodes (no dependencies) get depth 0.
    Nodes that depend on leaves get depth 1, etc.

    Uses iterative topological sort approach — no recursion depth issues.
    """
    # Compute in-degrees and adjacency
    in_degree: dict[str, int] = {pid: len(n.dependencies) for pid, n in nodes.items()}
    dependents: dict[str, list[str]] = {pid: [] for pid in nodes}

    for pid, node in nodes.items():
        for dep_id in node.dependencies:
            if dep_id in dependents:
                dependents[dep_id].append(pid)

    # Topological sort starting from leaves
    queue = deque(pid for pid, deg in in_degree.items() if deg == 0)
    depths: dict[str, int] = {}

    while queue:
        current = queue.popleft()
        current_depth = depths.get(current, 0)

        for dependent in dependents.get(current, []):
            depths[dependent] = max(depths.get(dependent, 0), current_depth + 1)
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    # Assign depths
    for pid in nodes:
        nodes[pid].depth = depths.get(pid, 0)


def _dependencies_met(node: PhaseNode, completed: set[str]) -> bool:
    """Check if all of a node's dependencies are completed."""
    if not node.dependencies:
        return True
    return set(node.dependencies).issubset(completed)


def _node_to_task(node: PhaseNode) -> CompiledTask:
    """Convert a PhaseNode to a CompiledTask with scoring."""
    score = (node.depth * 100) + node.priority
    return CompiledTask(
        phase_id=node.phase_id,
        name=node.name,
        priority=node.priority,
        depth=node.depth,
        score=score,
        dependencies=node.dependencies,
        description=node.description,
        status=node.status,
    )
