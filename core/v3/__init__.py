"""Kai AI Software Factory V3 — Standalone Orchestrator.

This package replaces core/scheduler.py and core/orchestrator_cycle.py with
a standalone elastic GPU orchestration system.  It reuses proven modules
(memory, lifecycle, llm_clients, ai_router, deployment_manager, etc.) as
libraries but owns its own cycle, build pipeline, and GPU lifecycle.

Modules (all implemented 2026-08-06):
    gpu_manager       — RunPod pod lifecycle, auto start/stop, health checks
    cost_tracker      — Per-pod runtime/cost accounting
    worker_pool       — Pod A (generator) / Pod B (reviewer) worker routing
    sandbox_manager   — Git worktree isolation per build
    build_contract    — Structured build task definitions
    build_manager     — Build lifecycle with sandbox, contracts, multi-review
    review_pipeline   — Multi-agent review with configurable voting
    approval_system   — Multi-agent approval with configurable voting
    roadmap_compiler  — Roadmap → DAG → priority queue compilation
    roadmap_manager   — Phase-to-build lifecycle with compiler integration
    monitoring        — Health monitoring and per-cycle reporting
    recovery          — Retry logic, failure reassignment, recovery
    cycle             — New orchestration loop wiring everything together
    scheduler_v3      — Entry point (systemd service), 60s loop
"""

__version__ = "3.0.0"
