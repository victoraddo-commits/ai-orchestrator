"""Kai V3 Worker Pool — Pod routing and worker assignment.

All workers are specialized — no unlimited generic agents.

Management workers (management tasks, no GPU):
  - Scheduler, ContextBuilder, DependencyResolver, SandboxManager, CostManager

Build workers (Pod A — Qwen4 generator):
  - FeatureBuilder, BackendBuilder, FrontendBuilder, APIBuilder,
    DatabaseBuilder, InfraBuilder, RefactoringBuilder, BugFixBuilder,
    OptimizationBuilder

Review workers (Pod B — Qwen6 reviewer):
  - ArchitectureReviewer, SecurityReviewer, PerformanceReviewer,
    QAReviewer, DocumentationReviewer, DeploymentValidator, ApprovalAgent
"""

from datetime import datetime, timezone
from enum import Enum

from core.logger import info


class WorkerStatus(str, Enum):
    IDLE = "IDLE"
    BUSY = "BUSY"
    ERROR = "ERROR"
    OFFLINE = "OFFLINE"


# ── Worker type definitions ───────────────────────────────────────────────

POD_A_WORKERS = {
    "FeatureBuilder": {
        "description": "Implements new features and functionality",
        "task_types": ["GENERATING", "IMPLEMENT"],
        "pod": "qwen4",
    },
    "BackendBuilder": {
        "description": "Builds backend services and APIs",
        "task_types": ["GENERATING", "BACKEND"],
        "pod": "qwen4",
    },
    "FrontendBuilder": {
        "description": "Builds frontend UI and components",
        "task_types": ["GENERATING", "FRONTEND"],
        "pod": "qwen4",
    },
    "APIBuilder": {
        "description": "Designs and implements API endpoints",
        "task_types": ["GENERATING", "API"],
        "pod": "qwen4",
    },
    "DatabaseBuilder": {
        "description": "Database schema design and migrations",
        "task_types": ["GENERATING", "DATABASE"],
        "pod": "qwen4",
    },
    "InfraBuilder": {
        "description": "Infrastructure and deployment configuration",
        "task_types": ["GENERATING", "INFRA"],
        "pod": "qwen4",
    },
    "RefactoringBuilder": {
        "description": "Code refactoring and cleanup",
        "task_types": ["GENERATING", "REFACTOR"],
        "pod": "qwen4",
    },
    "BugFixBuilder": {
        "description": "Fixes bugs and addresses issues",
        "task_types": ["GENERATING", "BUGFIX"],
        "pod": "qwen4",
    },
    "OptimizationBuilder": {
        "description": "Performance optimization",
        "task_types": ["GENERATING", "OPTIMIZE"],
        "pod": "qwen4",
    },
}

POD_B_WORKERS = {
    "ArchitectureReviewer": {
        "description": "Reviews design correctness, maintainability, scalability",
        "task_types": ["CODE_REVIEW", "ARCHITECTURE_REVIEW"],
        "pod": "qwen6",
    },
    "SecurityReviewer": {
        "description": "Checks vulnerabilities, permissions, secrets, attack surface",
        "task_types": ["CODE_REVIEW", "SECURITY_REVIEW"],
        "pod": "qwen6",
    },
    "PerformanceReviewer": {
        "description": "Checks speed, resource usage, bottlenecks",
        "task_types": ["CODE_REVIEW", "PERFORMANCE_REVIEW"],
        "pod": "qwen6",
    },
    "QAReviewer": {
        "description": "Verifies tests, regression, user requirements",
        "task_types": ["CODE_REVIEW", "QA_REVIEW"],
        "pod": "qwen6",
    },
    "DocumentationReviewer": {
        "description": "Checks documentation completeness",
        "task_types": ["CODE_REVIEW", "DOCS_REVIEW"],
        "pod": "qwen6",
    },
    "DeploymentValidator": {
        "description": "Validates deployment readiness",
        "task_types": ["DEPLOYING", "DEPLOY_VALIDATION"],
        "pod": "qwen6",
    },
    "ApprovalAgent": {
        "description": "Reviews and votes on deployment approval",
        "task_types": ["DEPLOYING", "APPROVAL"],
        "pod": "qwen6",
    },
}

MANAGEMENT_WORKERS = {
    "Scheduler": {
        "description": "Assigns tasks, balances queues, selects workers and pods",
        "task_types": ["MANAGEMENT"],
        "pod": None,  # Runs locally, no GPU needed
    },
    "ContextBuilder": {
        "description": "Collects required files, deps, git history, docs",
        "task_types": ["MANAGEMENT"],
        "pod": None,
    },
    "DependencyResolver": {
        "description": "Detects blockers, orders tasks, maintains DAG",
        "task_types": ["MANAGEMENT"],
        "pod": None,
    },
    "SandboxManager": {
        "description": "Creates environments, manages git worktrees, cleanup",
        "task_types": ["MANAGEMENT"],
        "pod": None,
    },
    "CostManager": {
        "description": "Tracks GPU usage, controls spending, stops waste",
        "task_types": ["MANAGEMENT"],
        "pod": None,
    },
}

# ── Runtime worker registry ───────────────────────────────────────────────

_workers: dict[str, dict] = {}


def init_workers():
    """Initialize the worker pool with all worker types."""
    global _workers

    for role, config in POD_A_WORKERS.items():
        _workers[role] = {
            "role": role,
            "description": config["description"],
            "task_types": config["task_types"],
            "pod": config["pod"],
            "status": WorkerStatus.IDLE.value,
            "current_task": None,
            "tasks_completed": 0,
            "tasks_failed": 0,
            "last_active": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    for role, config in POD_B_WORKERS.items():
        _workers[role] = {
            "role": role,
            "description": config["description"],
            "task_types": config["task_types"],
            "pod": config["pod"],
            "status": WorkerStatus.IDLE.value,
            "current_task": None,
            "tasks_completed": 0,
            "tasks_failed": 0,
            "last_active": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    for role, config in MANAGEMENT_WORKERS.items():
        _workers[role] = {
            "role": role,
            "description": config["description"],
            "task_types": config["task_types"],
            "pod": config["pod"],
            "status": WorkerStatus.IDLE.value,
            "current_task": None,
            "tasks_completed": 0,
            "tasks_failed": 0,
            "last_active": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    info(f"Worker pool initialized: {len(_workers)} workers "
         f"({len(POD_A_WORKERS)} build, {len(POD_B_WORKERS)} review, "
         f"{len(MANAGEMENT_WORKERS)} management)")


def get_worker(role: str) -> dict | None:
    """Get a specific worker's state."""
    return _workers.get(role)


def get_all_workers() -> list[dict]:
    """Get all worker states."""
    return list(_workers.values())


def get_available_workers(pod: str | None = None,
                          task_type: str | None = None) -> list[dict]:
    """Get idle workers, optionally filtered by pod and task type."""
    available = []
    for worker in _workers.values():
        if worker["status"] != WorkerStatus.IDLE.value:
            continue
        if pod and worker["pod"] != pod:
            continue
        if task_type and task_type not in worker["task_types"]:
            continue
        available.append(worker)
    return available


def get_workers_by_pod(pod: str) -> list[dict]:
    """Get all workers assigned to a specific pod."""
    return [w for w in _workers.values() if w["pod"] == pod]


def assign_worker(role: str, task_id: str) -> bool:
    """Assign a task to a worker, marking it BUSY."""
    worker = _workers.get(role)
    if not worker or worker["status"] != WorkerStatus.IDLE.value:
        return False

    worker["status"] = WorkerStatus.BUSY.value
    worker["current_task"] = task_id
    worker["last_active"] = datetime.now(timezone.utc).isoformat()
    return True


def release_worker(role: str, success: bool = True):
    """Release a worker from its current task."""
    worker = _workers.get(role)
    if not worker:
        return

    if success:
        worker["tasks_completed"] += 1
    else:
        worker["tasks_failed"] += 1

    worker["status"] = WorkerStatus.IDLE.value
    worker["current_task"] = None
    worker["last_active"] = datetime.now(timezone.utc).isoformat()


def mark_worker_error(role: str, error: str):
    """Mark a worker as errored."""
    worker = _workers.get(role)
    if worker:
        worker["status"] = WorkerStatus.ERROR.value
        worker["last_error"] = error
        worker["last_active"] = datetime.now(timezone.utc).isoformat()


def assign_best_worker(task_type: str, pod: str | None = None) -> dict | None:
    """Find the best available worker for a task type.

    Prefers workers whose task_types match, then by idle duration (oldest first).
    """
    candidates = get_available_workers(pod=pod, task_type=task_type)

    if not candidates:
        # Fall back to any available worker on the right pod
        candidates = get_available_workers(pod=pod)

    if not candidates:
        return None

    # Pick the worker that's been idle longest — FIFO fairness
    def idle_duration(w: dict) -> float:
        last = w.get("last_active")
        if not last:
            return float("inf")
        try:
            dt = datetime.fromisoformat(last)
            return (datetime.now(timezone.utc) - dt).total_seconds()
        except (ValueError, TypeError):
            return 0.0

    candidates.sort(key=idle_duration, reverse=True)
    return candidates[0]


def get_pod_worker_summary(pod: str) -> dict:
    """Get a summary of worker activity on a pod."""
    workers = get_workers_by_pod(pod)
    return {
        "total": len(workers),
        "busy": sum(1 for w in workers if w["status"] == WorkerStatus.BUSY.value),
        "idle": sum(1 for w in workers if w["status"] == WorkerStatus.IDLE.value),
        "error": sum(1 for w in workers if w["status"] == WorkerStatus.ERROR.value),
        "tasks_completed": sum(w["tasks_completed"] for w in workers),
        "tasks_failed": sum(w["tasks_failed"] for w in workers),
    }
