# Kai AI Software Factory V3 — Design Spec

**Date:** 2026-08-06
**Status:** Approved
**Deployment:** Full replacement — stop old orchestrator, start v3

## Architecture

All new modules live in `core/v3/`. Entry point is `core/v3/scheduler_v3.py`.

```
core/v3/
├── __init__.py              # Package identity (already exists)
├── scheduler_v3.py          # Entry point — systemd service, 60s loop
├── cycle.py                 # run_cycle() — wires everything together
├── gpu_manager.py           # Pod A (generator) / Pod B (reviewer) lifecycle
├── cost_tracker.py          # Per-pod runtime, cost, tasks completed
├── worker_pool.py           # Pod routing, worker assignment
├── build_manager.py         # Build lifecycle — sandboxes, contracts, multi-review
├── review_pipeline.py       # 5-reviewer pipeline with configurable voting
├── roadmap_compiler.py      # Roadmap → DAG → priority queue
├── roadmap_manager.py       # Phase-to-build lifecycle
├── sandbox_manager.py       # Git worktree isolation per build
├── build_contract.py        # Structured build task definitions
├── approval_system.py       # Multi-agent approval with voting
├── monitoring.py            # Health monitoring, pod/worker status
└── recovery.py              # Retry logic, failure reassignment
```

**Replaced modules:** scheduler.py, orchestrator_cycle.py, build_manager.py, roadmap_manager.py, sandbox.py
**Reused as libraries:** memory.py, lifecycle.py, llm_clients.py, ai_provider.py, ai_router.py, deployment_manager.py, memory_manager.py

## GPU Lifecycle

Two RunPod pods with distinct roles. State machine: OFFLINE → STARTING → HEALTH_CHECK → READY → BUSY → DRAINING → STOPPING → OFFLINE.

- **Pod A (Qwen4):** `ldtqgcshb2dwsw-8000.proxy.runpod.net/v1` — Generator.
  Started when GENERATING queue > 0 AND no generator available.
  Stopped after 10min idle.

- **Pod B (Qwen6):** `60jwzf36623b0o-8000.proxy.runpod.net/v1` — Reviewer/Deployer.
  Started when CODE_REVIEW or DEPLOYING queue > 0 AND Pod B offline.
  Pod B NEVER waits behind Pod A workloads.

Both running Qwen/Qwen3-32B-FP8 via vLLM. Auth: VLLM_QWEN3_CODER_API_KEY.

## Worker Routing

- **Pod A workers:** FeatureBuilder, BackendBuilder, FrontendBuilder, APIBuilder, DBBuilder, InfraBuilder, RefactoringBuilder, BugFixBuilder, Optimizer
- **Pod B workers:** ArchitectureReviewer, SecurityReviewer, PerformanceReviewer, QAReviewer, DocumentationReviewer, DeploymentValidator, ApprovalAgent

## Build Pipeline

**Two-pass processing:** Pass 1 processes DEPLOYING and CODE_REVIEW builds (completion-near, never starved). Pass 2 processes everything else.

**Timeouts:** GENERATION_TIMEOUT=2400s, DEPLOYING_TIMEOUT=1800s. Stuck builds auto-fail.

**Load filtering:** `load_builds()` excludes COMPLETED/FAILED/ROLLED_BACK by default. Include with `include_terminal=True`. Completed builds archived to `memory/builds_archive.json`.

**Duplicate prevention:** `create_build()` checks same name + non-terminal status → returns existing build.

**Stale references:** Roadmap phase referencing missing build → auto-fail with reason.

**Sandbox isolation:** Every build gets a git worktree on `build/{build_id}` branch. Separate environment, separate logs. Cleaned up after terminal. No shared writable files.

**Build contracts:** JSON with task_id, objective, acceptance_criteria, files_allowed, files_forbidden, dependencies, expected_runtime_s, expected_cost_usd, required_reviewers, rollback_plan.

## Review Pipeline

Five reviewers — all Pod B: ArchitectureReviewer, SecurityReviewer, PerformanceReviewer, QAReviewer, DocumentationReviewer.

Configurable voting: 4/5 approvals required for deploy gate.

Flow: Builder → Architecture → Security → Performance → QA → Docs → Approval → Deploy.

Failed review → return to builder with findings → re-submit (only failing reviewers re-run).

## Roadmap Compiler

Converts roadmap.json → DAG → topological sort → priority queue → executable tasks. Phases only enter queue when dependencies are completed. Priority scoring: `(dependency_depth * 100) + priority`.

## Monitoring

Per-cycle report: pipeline status counts, GPU status (VRAM, temp, utilization, tokens/s, queue depth), worker status (role, pod, current task, health, success rate), cost ($/hr per pod, today total, month total).

Health checks every cycle: vLLM endpoint, pod availability, worker heartbeat, stuck builds, provider quota.

## Recovery

Provider failure → retry once → omniroute fallback. Generation failure → return to builder. Timeout → retry once. Repeated failure → human approval queue via Telegram.

## Deployment Sequence

1. Backup memory/ to memory_backup_v2_<date>/
2. Write all v3 modules
3. Write systemd unit
4. Stop old orchestrator
5. Run tests
6. Start v3 service
7. Watch first cycle
8. Report production validation
