"""Restart the live orchestrator services after a self-modifying merge.

Confirmed live 2026-07-30 (17C): 17B merged GET /kai/chat into core/api.py
cleanly and the deploy reported COMPLETED/VERIFIED, but the running
ai-orchestrator-api.service process had been up since 04:25 that morning --
Python does not hot-reload, so the new route returned a genuine 404 until
the service was manually restarted. Every deploy that touches any module a
running service has already imported is silently stale until that service
restarts.

This module decides, from the list of files a merge actually changed,
which of the two live services (if any) must be restarted, and performs
the restart via systemctl:

- ai-orchestrator-api.service  -- the FastAPI process (core/api.py)
- ai-orchestrator.service      -- the scheduler daemon (core/scheduler.py)

Rules (per roadmap phase 17C acceptance criteria):
- Only a successful self-modifying merge may trigger a restart. Failed or
  rolled-back deploys never restart anything (callers gate on
  deploy_result["deployed"] and this module double-checks).
- A change that touches no runtime code (roadmap.json, docs, tests, ...)
  triggers no restart -- unnecessary restarts drop in-flight requests for
  no reason.
- If we cannot determine what changed, restart both services: stale live
  code is a silent, hard-to-diagnose failure, an extra restart is a blip.
"""

import subprocess

from core.logger import error, info, warning


API_SERVICE = "ai-orchestrator-api.service"
SCHEDULER_SERVICE = "ai-orchestrator.service"

# Modules imported only by the API process (core/api.py and everything
# reachable from it but NOT from core/scheduler.py).
_API_ONLY_FILES = {
    "core/api.py",
    "core/roadmap_engine.py",
}
_API_ONLY_PREFIXES = ("core/kai/",)

# Modules imported only by the scheduler daemon (core/scheduler.py ->
# core/orchestrator_cycle.py and its scheduler-side dependencies).
_SCHEDULER_ONLY_FILES = {
    "core/scheduler.py",
    "core/orchestrator_cycle.py",
    "core/telegram_bridge.py",
    "core/state_manager.py",
    "core/remediation_runner.py",
}

# Paths never imported by either running process: changes here must not
# trigger a restart.
_NON_RUNTIME_PREFIXES = (
    "tests/",
    "docs/",
    "scripts/",
    "skills/",
    ".opencode/",
    ".claude/",
)

# Non-.py files that still invalidate both running processes.
_RUNTIME_NON_PY_FILES = {"requirements.txt"}


def _is_runtime_code(path):
    path = path.replace("\\", "/")
    if any(path.startswith(prefix) for prefix in _NON_RUNTIME_PREFIXES):
        return False
    if path in _RUNTIME_NON_PY_FILES:
        return True
    return path.endswith(".py")


def _service_scope_for(path):
    """Which running process imports this file: 'api', 'scheduler', or 'both'."""
    path = path.replace("\\", "/")
    if path in _API_ONLY_FILES or any(path.startswith(p) for p in _API_ONLY_PREFIXES):
        return "api"
    if path in _SCHEDULER_ONLY_FILES:
        return "scheduler"
    # Everything else that is runtime code (core/build_manager.py,
    # core/deployment_manager.py, providers/, router.py, ...) is in the
    # shared import closure of both processes -- or we can't prove it
    # isn't, which must mean restart, never stale code.
    return "both"


def services_needing_restart(changed_files):
    """Map a merge's changed-file list to the services that must restart.

    Returns a list ordered so the API service restarts first and the
    scheduler daemon -- the process running this very code -- restarts
    last. `changed_files=None` means "unknown": restart both rather than
    risk running stale code.
    """
    if changed_files is None:
        warning(
            "service-restart: changed files of merge are unknown -- "
            "conservatively restarting both services rather than risk stale code"
        )
        return [API_SERVICE, SCHEDULER_SERVICE]

    runtime_changes = [f for f in changed_files if _is_runtime_code(f)]
    if not runtime_changes:
        return []

    scopes = {_service_scope_for(f) for f in runtime_changes}

    services = []
    if scopes & {"api", "both"}:
        services.append(API_SERVICE)
    if scopes & {"scheduler", "both"}:
        services.append(SCHEDULER_SERVICE)
    return services


def _systemctl(*args, timeout=60):
    return subprocess.run(
        ["systemctl", *args], capture_output=True, text=True, timeout=timeout
    )


def _restart_service(service, trigger):
    # Restarting ai-orchestrator.service restarts the scheduler daemon --
    # the very process executing this code. --no-block queues the restart
    # with systemd and returns immediately instead of deadlocking waiting
    # for our own death.
    no_block = service == SCHEDULER_SERVICE
    args = ("restart", "--no-block", service) if no_block else ("restart", service)

    info(f"service-restart: restarting {service} (trigger: {trigger})")

    try:
        result = _systemctl(*args)
    except Exception as err:  # systemctl missing, timeout, ...
        error(f"service-restart: restart of {service} raised: {err}")
        return {"service": service, "restarted": False, "error": str(err)}

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        error(f"service-restart: restart of {service} failed: {detail}")
        return {"service": service, "restarted": False, "error": detail}

    info(f"service-restart: {service} restart {'queued' if no_block else 'succeeded'}")
    return {"service": service, "restarted": True, "queued": no_block}


def restart_services_if_needed(build, deploy_result):
    """Restart live services made stale by a successful self-modifying merge.

    Called by build_manager after a deploy reaches VERIFIED/COMPLETED.
    No-op for failed deploys, container deploys, and merges that touched
    no runtime code. Restart records are attached to the deploy result
    (deploy_result["service_restarts"]) for observability and returned.
    """
    if not deploy_result or not deploy_result.get("deployed"):
        return []
    if not deploy_result.get("merged_branch"):
        # Container deploy -- the orchestrator's own processes are untouched.
        return []

    changed_files = deploy_result.get("changed_files", None)
    services = services_needing_restart(changed_files)

    build_name = build.get("name", build.get("id", "?"))
    if not services:
        info(
            f"service-restart: deploy of build {build_name} touched no live-imported "
            f"code (changed: {changed_files}) -- no restart needed"
        )
        deploy_result["service_restarts"] = []
        return []

    changed_summary = "unknown" if changed_files is None else ", ".join(changed_files[:20])
    trigger = f"self-modifying merge of build {build_name} (changed: {changed_summary})"

    records = [_restart_service(service, trigger) for service in services]
    deploy_result["service_restarts"] = records
    return records
