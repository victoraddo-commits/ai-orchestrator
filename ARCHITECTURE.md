# Architecture

## The live pipeline

Everything runs from one entry point, on a 300-second loop:

```mermaid
flowchart TD
    scheduler["core/scheduler.py<br/>(systemd, 300s loop)"] --> cycle["core/orchestrator_cycle.py::run_cycle()"]

    cycle --> state["state_manager.refresh_state()<br/>scanner + inventory + Proxmox API"]
    cycle --> health["health.analyze()<br/>docker_analyzer + service_monitor + proxmox_health"]
    health --> incidents["incident_manager.create_incident()<br/>dedup by (service, issue)"]
    incidents --> decisions["decision_engine.evaluate_incidents()<br/>reasoning + risk scoring"]
    decisions --> approval["approval.py + approval_manager.py<br/>pending approval_queue.json"]
    approval -.human approves via approval_cli.py.-> approved["status: approved"]
    approved --> remediation["remediation_runner.process()"]
    remediation --> docker["docker_actions.execute_action()<br/>gated by core/security.py"]
    remediation --> remrecord["remediation.py<br/>own lifecycle object + snapshot"]
    docker --> verify["verification.verify_service()"]
    verify -->|unresolved| rollback["remediation.attempt_rollback()"]
    verify --> memory_hist["remediation_memory.py<br/>outcome history"]
    memory_hist --> learning["core/learning.py<br/>trusted / observe / avoid"]
    learning -.feeds back into.-> decisions
```

## The five lifecycle objects

Every incident/decision/approval/remediation/verification record shares one
schema, produced by `core/lifecycle.py::new_object()`:

```json
{
  "id": "8-char uuid",
  "trace_id": "the originating incident's id",
  "status": "...",
  "created": "iso timestamp",
  "updated": "iso timestamp",
  "history": [{"status": "...", "timestamp": "...", "note": "..."}]
}
```

`trace_id` is what lets you reconstruct the full causal chain for one
incident: every decision, approval, remediation, and verification record
spawned in response to it carries the same `trace_id` (the incident's own
`id`). See `tests/test_full_lifecycle_integration.py` for a worked example.

Each object type has its own validated state machine (`core/lifecycle.py`
raises `InvalidTransition` on an illegal move):

| Object | File | States |
|---|---|---|
| Incident | `core/incident_manager.py` | open -> investigating -> approved -> executing -> verifying -> resolved -> closed (+ failed retry loop) |
| Decision | `core/decision_engine.py` | proposed -> approved/rejected -> executed |
| Approval | `core/approval.py` | pending -> approved/rejected -> executed |
| Remediation | `core/remediation.py` | queued -> executing -> completed/failed -> rolled_back |
| Verification | `core/verification.py` | verifying -> resolved/unresolved |

Decision status is kept in sync with its linked approval's status every time
`evaluate_incidents()` runs (`sync_decision_with_approval`), catching up
through intermediate states even if several approval transitions happened
between two scheduler cycles.

## Reasoning and risk (Phases 5-6)

`decision_engine.analyze_incident()` considers: current incident
severity/occurrences, the historical success rate of the recommended action
(`remediation_memory.get_action_success_rate`), and whether other open
issues exist on the same service (a noisier situation lowers confidence).
`evaluate_risk()` scores the result into low/medium/high.

`core/action_policy.py` classifies every action name into a risk tier
(low/medium/high) independent of the above. `requires_approval(action)`
only ever returns `False` for a low-risk action, and only when
`core/config.py::AUTONOMOUS_MODE` is `True`. It is `False` by default —
every proposed action requires a human today. See `SECURITY.md`.

## Rollback (Phase 7)

Every remediation captures a snapshot (`previous state`, `command`,
`expected result`) before executing. If verification later reports
`unresolved`, `orchestrator_cycle.py` calls
`remediation.attempt_rollback()`, which looks up a per-action strategy in
`ROLLBACK_STRATEGIES` (registered via `register_rollback(action, fn)`).
**No strategy is registered for `restart_container`** — there's no
meaningful inverse of a container restart — so today `attempt_rollback()`
always records `{"attempted": true, "available": false}` for it. The
registry exists for future actions (e.g. `resize_resources`,
`migrate_vm`) that do have a real inverse operation.

## Memory layer (Phase 2)

`core/memory.py` (the API every module uses: `load(name)` / `save(name,
data)`) delegates to `core/memory_manager.py`, which:
- writes atomically (temp file + `os.replace`),
- keeps a rolling `.bak` backup before every write, falling back to it on
  read if the primary file is corrupt,
- wraps every file on disk as `{"schema_version": 1, "records": [...]}`,
  transparently upgrading old bare-format files the next time anything
  writes to them (no migration script needed),
- guards against test code writing into the real `memory/` directory
  (`ProductionMemoryWriteBlocked`, raised whenever `PYTEST_CURRENT_TEST` is
  set and the target resolves to the production path).

## Observability API (Phase 3)

`core/api.py` (FastAPI) exposes read-only routes over the same lifecycle
modules: `/health`, `/incidents`, `/decisions`, `/approvals`, `/actions`,
`/verifications`, `/learning`. No write/mutate endpoints exist — approving
or rejecting still only happens through `approval_cli.py`. Not deployed as
a standing service; run it manually (see `README.md`).

## Dead-code cleanup note

A large amount of unreached, mutually-incompatible scaffolding (a
class-based agent pipeline, a separate execution queue, several competing
incident-state-machine implementations) was found during Phase 1 and either
consolidated into the modules above or deleted outright — see the Phase 1
commit message (`git log --grep "Phase 1"`) for the full inventory of what
was removed and why.
