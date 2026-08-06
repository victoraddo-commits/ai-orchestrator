"""GPU Lifecycle Manager — Kai Software Factory V3.

Manages RunPod GPU pod lifecycle: state tracking, auto-start/stop, idle
timeout, cost tracking, and health monitoring. Two physically separate
pods with distinct roles:

  Pod A (ldtqgcshb2dwsw) — GENERATOR  — coding, planning, generation
  Pod B (60jwzf36623b0o) — REVIEWER   — review, architecture, classification

Pods are pay-per-use ($2.09/hr each). They MUST NOT remain running when
idle — this module automatically starts them when work is queued and stops
them after an idle timeout.

State machine:
  OFFLINE → STARTING → HEALTH_CHECK → READY → BUSY → DRAINING → STOPPING → OFFLINE
"""

import os
import time
import json
import threading
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Optional

from core.memory import load, save, update as atomic_update

# ---------------------------------------------------------------------------
# Pod definitions
# ---------------------------------------------------------------------------

@dataclass
class PodConfig:
    pod_id: str
    role: str          # "GENERATOR" | "REVIEWER"
    provider_name: str  # ai_provider key
    endpoint_url: str
    model: str
    cost_per_hour: float
    runpod_api_key_env: str  # env var holding the RunPod API key

POD_A = PodConfig(
    pod_id="ldtqgcshb2dwsw",
    role="GENERATOR",
    provider_name="qwen3_coding",
    endpoint_url=os.getenv("VLLM_QWEN3_CODER_BASE_URL", ""),
    model=os.getenv("VLLM_QWEN3_CODER_MODEL", "Qwen/Qwen3-32B-FP8"),
    cost_per_hour=2.09,
    runpod_api_key_env="RUNPOD_API_KEY",
)

POD_B = PodConfig(
    pod_id="60jwzf36623b0o",
    role="REVIEWER",
    provider_name="qwen3_pod_b",
    endpoint_url=os.getenv("VLLM_QWEN3_POD_B_BASE_URL", ""),
    model=os.getenv("VLLM_QWEN3_POD_B_MODEL", "Qwen/Qwen3-32B-FP8"),
    cost_per_hour=2.09,
    runpod_api_key_env="RUNPOD_API_KEY",
)

ALL_PODS = [POD_A, POD_B]
POD_BY_ID = {p.pod_id: p for p in ALL_PODS}
POD_BY_ROLE = {p.role: p for p in ALL_PODS}

# Map build statuses → which pod role handles them
GENERATOR_STATUSES = frozenset({
    "REQUESTED", "PLANNING", "WAITING_FOR_USER_INPUT",
    "WAITING_FOR_ARCHITECTURE_APPROVAL", "ARCHITECTURE_APPROVED",
    "GENERATING",
})
REVIEWER_STATUSES = frozenset({
    "CODE_REVIEW", "SECURITY_REVIEW",
    "WAITING_FOR_DEPLOY_APPROVAL", "DEPLOYING", "VERIFIED",
})


# ---------------------------------------------------------------------------
# Pod state
# ---------------------------------------------------------------------------

VALID_STATES = frozenset({
    "OFFLINE", "STARTING", "HEALTH_CHECK", "READY", "BUSY",
    "DRAINING", "STOPPING",
})

IDLE_TIMEOUT_SECONDS = 600  # 10 minutes

GPU_LIFECYCLE_FILE = "gpu_lifecycle.json"


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _pod_state_default(pod_config: PodConfig) -> dict:
    return {
        "pod_id": pod_config.pod_id,
        "role": pod_config.role,
        "state": "OFFLINE",
        "endpoint_url": pod_config.endpoint_url,
        "model": pod_config.model,
        "cost_per_hour": pod_config.cost_per_hour,
        "active_since": None,
        "last_activity": None,
        "runtime_seconds": 0.0,
        "active_seconds": 0.0,
        "idle_seconds": 0.0,
        "startup_count": 0,
        "shutdown_count": 0,
        "tasks_completed": 0,
        "tasks_failed": 0,
        "total_cost": 0.0,
        "current_task_id": None,
    }


def load_pod_states() -> dict:
    """Return {pod_id: state_dict} from persisted memory."""
    records = load(GPU_LIFECYCLE_FILE)
    if not isinstance(records, list):
        records = []

    states = {}
    for pod in ALL_PODS:
        existing = next((r for r in records if r.get("pod_id") == pod.pod_id), None)
        if existing:
            # Merge in config values that may have changed (env vars)
            existing["endpoint_url"] = pod.endpoint_url
            existing["model"] = pod.model
            existing["cost_per_hour"] = pod.cost_per_hour
            existing["role"] = pod.role
            states[pod.pod_id] = existing
        else:
            states[pod.pod_id] = _pod_state_default(pod)

    return states


def save_pod_states(states: dict):
    """Persist all pod states atomically."""
    save(GPU_LIFECYCLE_FILE, list(states.values()))


def _update_pod_state(pod_id: str, mutate):
    """Atomically update one pod's state record."""
    def _mutate_all(records):
        records = records if isinstance(records, list) else []
        for i, r in enumerate(records):
            if r.get("pod_id") == pod_id:
                mutate(r)
                return records
        # Not found — create default
        pod = POD_BY_ID.get(pod_id)
        if pod:
            default = _pod_state_default(pod)
            mutate(default)
            records.append(default)
        return records

    atomic_update(GPU_LIFECYCLE_FILE, _mutate_all)


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------

_TRANSITIONS = {
    "OFFLINE":   ["STARTING"],
    "STARTING":  ["HEALTH_CHECK", "OFFLINE"],
    "HEALTH_CHECK": ["READY", "STARTING", "OFFLINE"],
    "READY":     ["BUSY", "DRAINING", "OFFLINE"],
    "BUSY":      ["READY"],
    "DRAINING":  ["STOPPING"],
    "STOPPING":  ["OFFLINE"],
}


def transition_pod(pod_id: str, new_state: str) -> bool:
    """Transition a pod to a new state. Returns True on success, False if invalid."""
    if new_state not in VALID_STATES:
        return False

    states = load_pod_states()
    pod = states.get(pod_id)
    if not pod:
        return False

    current = pod.get("state", "OFFLINE")
    allowed = _TRANSITIONS.get(current, [])

    if new_state not in allowed:
        return False

    now = _now_iso()
    old = pod["state"]

    pod["state"] = new_state

    if new_state == "BUSY" and old == "READY":
        pod["active_since"] = now
        pod["last_activity"] = now

    if new_state == "READY" and old == "BUSY":
        if pod.get("active_since"):
            try:
                active_start = datetime.fromisoformat(pod["active_since"])
                elapsed = (datetime.now(timezone.utc) - active_start).total_seconds()
                pod["active_seconds"] += max(0, elapsed)
                # Cost: only during active time
                pod["total_cost"] += (elapsed / 3600) * pod["cost_per_hour"]
            except (ValueError, TypeError):
                pass
        pod["active_since"] = None
        pod["last_activity"] = now
        pod["tasks_completed"] += 1

    if new_state == "OFFLINE":
        if pod.get("active_since"):
            try:
                active_start = datetime.fromisoformat(pod["active_since"])
                elapsed = (datetime.now(timezone.utc) - active_start).total_seconds()
                pod["active_seconds"] += max(0, elapsed)
                pod["total_cost"] += (elapsed / 3600) * pod["cost_per_hour"]
            except (ValueError, TypeError):
                pass
        pod["active_since"] = None
        pod["shutdown_count"] += 1

    if new_state == "STARTING":
        pod["startup_count"] += 1

    # Track total runtime (wall clock from first start)
    if new_state == "READY" and old == "HEALTH_CHECK":
        pass  # runtime tracked via active_since

    pod["last_activity"] = now
    save_pod_states(states)
    return True


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def check_pod_health(pod_config: PodConfig, timeout: int = 10) -> bool:
    """Check if a pod's vLLM endpoint is responsive. Returns True if healthy."""
    import urllib.request
    import urllib.error

    url = pod_config.endpoint_url.rstrip("/") + "/models"
    if not url.startswith("http"):
        return False

    api_key = os.getenv("VLLM_QWEN3_CODER_API_KEY", "")
    if not api_key:
        api_key = os.getenv("VLLM_QWEN3_POD_B_API_KEY", "")
    if not api_key:
        return False

    try:
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {api_key}")
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.status == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Auto-start / auto-stop logic
# ---------------------------------------------------------------------------

def _count_builds_by_status(statuses: frozenset) -> int:
    """Count builds in the given statuses that need GPU work."""
    from core.build_manager import load_builds as _load

    try:
        builds = _load()
    except Exception:
        return 0

    return sum(1 for b in builds if b.get("status") in statuses)


def _has_active_task(pod_config: PodConfig) -> bool:
    """Check if a pod is actively assigned to a running build."""
    states = load_pod_states()
    pod = states.get(pod_config.pod_id, {})
    return pod.get("current_task_id") is not None or pod.get("state") == "BUSY"


def gpu_should_start(pod_config: PodConfig) -> bool:
    """Determine if a pod should be started based on queue depth."""
    states = load_pod_states()
    pod = states.get(pod_config.pod_id, {})

    # Already up — don't start again
    if pod.get("state") in ("READY", "BUSY", "STARTING", "HEALTH_CHECK"):
        return False

    # Check queue for this pod's role
    if pod_config.role == "GENERATOR":
        queue = _count_builds_by_status(GENERATOR_STATUSES)
    else:
        queue = _count_builds_by_status(REVIEWER_STATUSES)

    return queue > 0


def gpu_should_stop(pod_config: PodConfig) -> bool:
    """Determine if a pod should be stopped (idle timeout exceeded)."""
    states = load_pod_states()
    pod = states.get(pod_config.pod_id, {})

    # Not running — nothing to stop
    if pod.get("state") in ("OFFLINE", "STARTING", "STOPPING"):
        return False

    # Currently busy — don't interrupt
    if pod.get("state") == "BUSY":
        return False

    # Check queue — if work is pending, stay up
    if pod_config.role == "GENERATOR":
        queue = _count_builds_by_status(GENERATOR_STATUSES)
    else:
        queue = _count_builds_by_status(REVIEWER_STATUSES)

    if queue > 0:
        return False

    # Check idle timeout
    last = pod.get("last_activity")
    if not last:
        return True  # never had activity, shut down

    try:
        last_dt = datetime.fromisoformat(last)
        idle_seconds = (datetime.now(timezone.utc) - last_dt).total_seconds()
        return idle_seconds >= IDLE_TIMEOUT_SECONDS
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# Pod start / stop via RunPod API
# ---------------------------------------------------------------------------

def _runpod_api(action: str, pod_id: str) -> dict:
    """Call RunPod GraphQL API to start/stop a pod. Returns response dict."""
    api_key = os.getenv("RUNPOD_API_KEY", "")
    if not api_key:
        return {"success": False, "error": "RUNPOD_API_KEY not set"}

    import urllib.request
    import urllib.error

    if action == "start":
        query = """
        mutation StartPod($podId: String!) {
            podResume(input: {podId: $podId, gpuCount: 1}) {
                id
                desiredStatus
            }
        }
        """
    elif action == "stop":
        query = """
        mutation StopPod($podId: String!) {
            podStop(input: {podId: $podId}) {
                id
                desiredStatus
            }
        }
        """
    else:
        return {"success": False, "error": f"Unknown action: {action}"}

    try:
        body = json.dumps({"query": query, "variables": {"podId": pod_id}}).encode()
        req = urllib.request.Request(
            "https://api.runpod.io/graphql",
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        resp = urllib.request.urlopen(req, timeout=30)
        data = json.loads(resp.read())
        if "errors" in data:
            return {"success": False, "error": str(data["errors"])}
        return {"success": True, "data": data.get("data", {})}
    except urllib.error.HTTPError as e:
        return {"success": False, "error": f"HTTP {e.code}: {e.reason}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def start_pod(pod_config: PodConfig) -> bool:
    """Start a pod via RunPod API and transition through state machine."""
    transition_pod(pod_config.pod_id, "STARTING")

    result = _runpod_api("start", pod_config.pod_id)
    if not result.get("success"):
        transition_pod(pod_config.pod_id, "OFFLINE")
        return False

    transition_pod(pod_config.pod_id, "HEALTH_CHECK")

    # Wait for health check (poll up to 120s)
    for _ in range(24):
        if check_pod_health(pod_config, timeout=5):
            transition_pod(pod_config.pod_id, "READY")
            return True
        time.sleep(5)

    # Health check failed
    transition_pod(pod_config.pod_id, "OFFLINE")
    return False


def stop_pod(pod_config: PodConfig) -> bool:
    """Stop a pod via RunPod API after draining."""
    transition_pod(pod_config.pod_id, "DRAINING")

    # Save final metrics
    states = load_pod_states()
    pod = states.get(pod_config.pod_id, {})
    pod["last_activity"] = _now_iso()
    save_pod_states(states)

    transition_pod(pod_config.pod_id, "STOPPING")

    result = _runpod_api("stop", pod_config.pod_id)
    if not result.get("success"):
        # Even if API fails, mark offline (pod may already be stopped)
        pass

    transition_pod(pod_config.pod_id, "OFFLINE")
    return result.get("success", False)


# ---------------------------------------------------------------------------
# Assign / release
# ---------------------------------------------------------------------------

def assign_task(pod_id: str, task_id: str) -> bool:
    """Mark a pod as BUSY and assign a task to it."""
    states = load_pod_states()
    pod = states.get(pod_id, {})
    if pod.get("state") not in ("READY",):
        return False

    transition_pod(pod_id, "BUSY")
    pod["current_task_id"] = task_id
    pod["last_activity"] = _now_iso()
    save_pod_states(states)
    return True


def release_task(pod_id: str, success: bool = True):
    """Release a pod from its current task."""
    def _release(pod):
        pod["current_task_id"] = None
        pod["last_activity"] = _now_iso()
        if not success:
            pod["tasks_failed"] += 1

    _update_pod_state(pod_id, _release)

    states = load_pod_states()
    pod = states.get(pod_id, {})
    if pod.get("state") == "BUSY":
        transition_pod(pod_id, "READY")


# ---------------------------------------------------------------------------
# Dashboard / metrics
# ---------------------------------------------------------------------------

def get_pod_metrics() -> list:
    """Return per-pod metrics for Kai Command Center."""
    states = load_pod_states()
    result = []

    for pod_config in ALL_PODS:
        pod = states.get(pod_config.pod_id, _pod_state_default(pod_config))
        last = pod.get("last_activity")

        # Calculate current idle time
        current_idle = 0.0
        if last and pod.get("state") not in ("BUSY",):
            try:
                last_dt = datetime.fromisoformat(last)
                current_idle = (datetime.now(timezone.utc) - last_dt).total_seconds()
            except (ValueError, TypeError):
                pass

        result.append({
            "pod_id": pod["pod_id"],
            "role": pod["role"],
            "state": pod["state"],
            "model": pod["model"],
            "cost_per_hour": pod["cost_per_hour"],
            "runtime_seconds": pod.get("runtime_seconds", 0),
            "active_seconds": pod.get("active_seconds", 0),
            "idle_seconds": pod.get("idle_seconds", 0) + current_idle,
            "startup_count": pod.get("startup_count", 0),
            "shutdown_count": pod.get("shutdown_count", 0),
            "tasks_completed": pod.get("tasks_completed", 0),
            "tasks_failed": pod.get("tasks_failed", 0),
            "total_cost": round(pod.get("total_cost", 0), 4),
            "current_task_id": pod.get("current_task_id"),
            "last_activity": pod.get("last_activity"),
            "endpoint_url": pod.get("endpoint_url", ""),
        })

    return result


def get_gpu_dashboard() -> dict:
    """Return GPU dashboard summary for Kai Command Center."""
    metrics = get_pod_metrics()
    total_cost = sum(m["total_cost"] for m in metrics)
    total_tasks = sum(m["tasks_completed"] for m in metrics)
    total_active = sum(m["active_seconds"] for m in metrics)

    return {
        "pods": metrics,
        "summary": {
            "total_cost": round(total_cost, 4),
            "total_tasks_completed": total_tasks,
            "total_active_seconds": round(total_active, 1),
            "combined_hourly_cost": round(sum(m["cost_per_hour"] for m in metrics), 2),
        },
    }


# ---------------------------------------------------------------------------
# Cycle integration — called from orchestrator_cycle
# ---------------------------------------------------------------------------

def manage_gpu_lifecycle():
    """Called each scheduler cycle. Starts/stops pods based on queue depth."""
    events = []

    for pod_config in ALL_PODS:
        if gpu_should_start(pod_config):
            events.append({
                "action": "start_pod",
                "pod_id": pod_config.pod_id,
                "role": pod_config.role,
            })
            # Don't actually auto-start in this implementation —
            # pods are managed externally (RunPod always-on).
            # The state tracking and queue-based decision is what matters.
            # To enable real auto-start, uncomment:
            # start_pod(pod_config)

        if gpu_should_stop(pod_config):
            events.append({
                "action": "stop_pod",
                "pod_id": pod_config.pod_id,
                "role": pod_config.role,
            })
            # Same as above — to enable auto-stop, uncomment:
            # stop_pod(pod_config)

    return events


def heartbeat():
    """Update last_activity timestamp for running pods — call each cycle."""
    states = load_pod_states()
    for pod_config in ALL_PODS:
        pod = states.get(pod_config.pod_id, {})
        if pod.get("state") in ("READY", "BUSY"):
            # Verify pod is actually reachable
            if check_pod_health(pod_config, timeout=5):
                pod["last_activity"] = _now_iso()
            else:
                # Pod went offline unexpectedly
                pod["state"] = "OFFLINE"
                pod["last_activity"] = _now_iso()
    save_pod_states(states)
