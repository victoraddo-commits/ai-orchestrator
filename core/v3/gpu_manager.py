"""Kai V3 GPU Lifecycle Manager — Pod A (generator) / Pod B (reviewer).

Two RunPod GPU pods with distinct roles. Auto start/stop via RunPod API.
Pod states: OFFLINE → STARTING → HEALTH_CHECK → READY → BUSY → DRAINING → STOPPING → OFFLINE.

Pod A (Qwen4): ldtqgcshb2dwsw — generator. Started when GENERATING queue > 0.
Pod B (Qwen6): 60jwzf36623b0o — reviewer/deployer. Started when CODE_REVIEW/DEPLOYING queue > 0.
Pod B NEVER waits behind Pod A workloads.
"""

import os
import time
from datetime import datetime, timezone

import requests

from core.logger import info, error as log_error

# ── Pod definitions ──────────────────────────────────────────────────────

POD_A = "qwen4"  # Generator
POD_B = "qwen6"  # Reviewer / Deployer

POD_CONFIGS = {
    POD_A: {
        "runpod_id": os.getenv("QWEN4_RUNPOD_POD_ID", "ldtqgcshb2dwsw"),
        "base_url": os.getenv("VLLM_QWEN3_CODER_BASE_URL",
                              "https://ldtqgcshb2dwsw-8000.proxy.runpod.net/v1"),
        "model": os.getenv("VLLM_QWEN3_CODER_MODEL", "Qwen/Qwen3-32B-FP8"),
        "role": "generator",
        "gpu_type": "RTX PRO 6000 96GB",
        "cost_per_hour": 2.09,
    },
    POD_B: {
        "runpod_id": os.getenv("QWEN6_RUNPOD_POD_ID", "60jwzf36623b0o"),
        "base_url": os.getenv("QWEN6_BASE_URL",
                              "https://60jwzf36623b0o-8000.proxy.runpod.net/v1"),
        "model": os.getenv("QWEN6_MODEL", "Qwen/Qwen3-32B-FP8"),
        "role": "reviewer",
        "gpu_type": "RTX PRO 6000 96GB",
        "cost_per_hour": 2.09,
    },
}

# ── State machine ─────────────────────────────────────────────────────────

POD_STATES = [
    "OFFLINE", "STARTING", "HEALTH_CHECK", "READY",
    "BUSY", "DRAINING", "STOPPING",
]

VALID_TRANSITIONS = {
    "OFFLINE": ["STARTING"],
    "STARTING": ["HEALTH_CHECK", "OFFLINE"],
    "HEALTH_CHECK": ["READY", "OFFLINE"],
    "READY": ["BUSY", "DRAINING", "OFFLINE"],
    "BUSY": ["READY", "DRAINING"],
    "DRAINING": ["STOPPING", "READY"],
    "STOPPING": ["OFFLINE"],
}

# 10 minute idle timeout before auto-shutdown
IDLE_TIMEOUT_SECONDS = 600

# Health check interval (how often we ping /v1/models)
HEALTH_CHECK_INTERVAL = 30

# Startup timeout — give a pod up to 5 minutes to become healthy
STARTUP_TIMEOUT = 300

# ── Runtime state ─────────────────────────────────────────────────────────

_pod_state: dict[str, dict] = {
    POD_A: {
        "status": "OFFLINE",
        "last_transition": None,
        "last_health_check": None,
        "last_active": None,
        "current_task": None,
        "total_tasks_completed": 0,
        "startup_count": 0,
        "shutdown_count": 0,
        "runtime_active_seconds": 0.0,
        "runtime_idle_seconds": 0.0,
    },
    POD_B: {
        "status": "OFFLINE",
        "last_transition": None,
        "last_health_check": None,
        "last_active": None,
        "current_task": None,
        "total_tasks_completed": 0,
        "startup_count": 0,
        "shutdown_count": 0,
        "runtime_active_seconds": 0.0,
        "runtime_idle_seconds": 0.0,
    },
}


# ── Public API ────────────────────────────────────────────────────────────

def get_pod_state(pod_name: str) -> dict | None:
    """Return the current state of a pod, or None if unknown."""
    return _pod_state.get(pod_name)


def get_all_pod_states() -> dict:
    """Return current state of all pods."""
    return dict(_pod_state)


def transition_pod(pod_name: str, new_status: str, note: str = "") -> dict:
    """Transition a pod to a new status, validating the state machine.

    Raises ValueError on invalid transitions.
    """
    if pod_name not in _pod_state:
        raise ValueError(f"Unknown pod: {pod_name}")

    current = _pod_state[pod_name]["status"]
    allowed = VALID_TRANSITIONS.get(current, [])

    if new_status not in allowed:
        raise ValueError(
            f"Cannot transition pod {pod_name} from {current} to {new_status}. "
            f"Allowed: {allowed}"
        )

    now = datetime.now(timezone.utc).isoformat()
    _pod_state[pod_name]["status"] = new_status
    _pod_state[pod_name]["last_transition"] = now

    if new_status == "BUSY":
        _pod_state[pod_name]["last_active"] = now

    elif new_status == "READY" and current == "BUSY":
        _pod_state[pod_name]["last_active"] = now

    elif new_status == "OFFLINE":
        _pod_state[pod_name]["last_active"] = None
        _pod_state[pod_name]["current_task"] = None

    info(f"Pod {pod_name}: {current} → {new_status}" +
         (f" ({note})" if note else ""))

    return _pod_state[pod_name]


def is_pod_available(pod_name: str) -> bool:
    """Check if a pod is ready to accept work."""
    state = _pod_state.get(pod_name, {})
    return state.get("status") in {"READY", "BUSY"}


def is_pod_busy(pod_name: str) -> bool:
    """Check if a pod is currently processing a task."""
    return _pod_state.get(pod_name, {}).get("status") == "BUSY"


def should_start_pod(pod_name: str, pending_tasks: dict[str, int]) -> bool:
    """Determine if a pod should be started based on pending tasks.

    Pod A: start if GENERATING queue > 0.
    Pod B: start if CODE_REVIEW or DEPLOYING queue > 0.
    """
    state = _pod_state.get(pod_name, {})
    current_status = state.get("status", "OFFLINE")

    # Only start offline pods
    if current_status not in {"OFFLINE"}:
        return False

    if pod_name == POD_A:
        return pending_tasks.get("GENERATING", 0) > 0

    if pod_name == POD_B:
        return (pending_tasks.get("CODE_REVIEW", 0) > 0 or
                pending_tasks.get("DEPLOYING", 0) > 0)

    return False


def should_stop_pod(pod_name: str) -> bool:
    """Determine if a pod should be stopped due to idle timeout.

    A pod should stop if:
    - It's READY (idle, not BUSY)
    - Its last_active time exceeds IDLE_TIMEOUT_SECONDS
    - No current task is assigned
    """
    state = _pod_state.get(pod_name, {})
    current_status = state.get("status", "OFFLINE")

    if current_status != "READY":
        return False

    last_active = state.get("last_active")
    if last_active is None:
        # Never been active — check how long it's been READY
        last_transition = state.get("last_transition")
        if last_transition is None:
            return False
        last_active = last_transition

    try:
        last_dt = datetime.fromisoformat(last_active)
        idle_seconds = (datetime.now(timezone.utc) - last_dt).total_seconds()
        return idle_seconds >= IDLE_TIMEOUT_SECONDS
    except (ValueError, TypeError):
        return False


def assign_task_to_pod(pod_name: str, task_id: str) -> bool:
    """Assign a task to a pod, marking it BUSY."""
    if not is_pod_available(pod_name):
        return False

    transition_pod(pod_name, "BUSY", note=f"task={task_id[:12]}")
    _pod_state[pod_name]["current_task"] = task_id
    return True


def release_task_from_pod(pod_name: str):
    """Release the current task from a pod, marking it READY."""
    if pod_name in _pod_state:
        _pod_state[pod_name]["current_task"] = None
        _pod_state[pod_name]["total_tasks_completed"] += 1
        if _pod_state[pod_name]["status"] == "BUSY":
            transition_pod(pod_name, "READY", note="task completed")


def start_pod(pod_name: str) -> bool:
    """Start a pod via RunPod API. Returns True if start initiated successfully."""
    config = POD_CONFIGS.get(pod_name)
    if not config:
        log_error(f"Cannot start unknown pod: {pod_name}")
        return False

    try:
        transition_pod(pod_name, "STARTING", note="api call")
    except ValueError:
        return False

    # Attempt RunPod API resume
    runpod_id = config["runpod_id"]
    api_key = os.getenv("RUNPOD_API_KEY", "")
    if api_key:
        try:
            resp = requests.post(
                f"https://rest.runpod.io/v1/pods/{runpod_id}/resume",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=30,
            )
            if resp.status_code == 200:
                info(f"RunPod API: pod {pod_name} ({runpod_id}) resume request sent")
            else:
                info(f"RunPod API: resume returned {resp.status_code} — "
                     f"pod may already be running")
        except requests.RequestException as e:
            info(f"RunPod API: resume request failed ({e}) — "
                 f"pod may already be running or API unreachable")

    # Transition to health check
    transition_pod(pod_name, "HEALTH_CHECK", note="waiting for vLLM")
    _pod_state[pod_name]["startup_count"] += 1

    return True


def stop_pod(pod_name: str) -> bool:
    """Stop a pod via RunPod API. Returns True if stop initiated successfully."""
    config = POD_CONFIGS.get(pod_name)
    if not config:
        return False

    current = _pod_state[pod_name]["status"]
    try:
        transition_pod(pod_name, "DRAINING", note="preparing to stop")
    except ValueError:
        if current == "DRAINING":
            pass  # Already draining, continue
        else:
            return False

    # Attempt RunPod API stop
    runpod_id = config["runpod_id"]
    api_key = os.getenv("RUNPOD_API_KEY", "")
    if api_key:
        try:
            resp = requests.post(
                f"https://rest.runpod.io/v1/pods/{runpod_id}/stop",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=30,
            )
            if resp.status_code == 200:
                info(f"RunPod API: pod {pod_name} ({runpod_id}) stop request sent")
            else:
                info(f"RunPod API: stop returned {resp.status_code}")
        except requests.RequestException as e:
            info(f"RunPod API: stop request failed ({e})")

    transition_pod(pod_name, "STOPPING", note="api call sent")
    _pod_state[pod_name]["shutdown_count"] += 1
    transition_pod(pod_name, "OFFLINE", note="stopped")

    return True


def check_health(pod_name: str) -> bool:
    """Ping the pod's vLLM /v1/models endpoint. Returns True if healthy."""
    config = POD_CONFIGS.get(pod_name)
    if not config:
        return False

    base_url = config["base_url"]
    api_key = os.getenv("VLLM_QWEN3_CODER_API_KEY", "")

    try:
        resp = requests.get(
            f"{base_url}/models",
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            timeout=10,
        )
        is_healthy = resp.status_code == 200
    except requests.RequestException:
        is_healthy = False

    now = datetime.now(timezone.utc).isoformat()
    _pod_state[pod_name]["last_health_check"] = now

    current = _pod_state[pod_name]["status"]

    if is_healthy:
        if current == "HEALTH_CHECK":
            transition_pod(pod_name, "READY", note="vLLM healthy")
        return True
    else:
        if current in {"HEALTH_CHECK"}:
            # Check if startup timed out
            transition_time = _pod_state[pod_name].get("last_transition")
            if transition_time:
                try:
                    trans_dt = datetime.fromisoformat(transition_time)
                    elapsed = (datetime.now(timezone.utc) - trans_dt).total_seconds()
                    if elapsed > STARTUP_TIMEOUT:
                        log_error(f"Pod {pod_name} startup timed out "
                                  f"({elapsed:.0f}s > {STARTUP_TIMEOUT}s)")
                        transition_pod(pod_name, "OFFLINE", note="startup timeout")
                except (ValueError, TypeError):
                    pass
        elif current in {"READY", "BUSY"}:
            log_error(f"Pod {pod_name} ({current}) failed health check — "
                      f"may need restart")
            transition_pod(pod_name, "OFFLINE", note="health check failed")
        return False


def get_pod_queue_depth(pod_name: str) -> int:
    """Get an estimate of pending tasks for this pod."""
    # This is called by the scheduler; the actual queue depth
    # is computed from task counts in memory
    return 0  # Default — set by scheduler


def get_all_health_status() -> dict:
    """Return health status summary for all pods."""
    return {
        pod: {
            "status": state["status"],
            "last_health_check": state.get("last_health_check"),
            "current_task": state.get("current_task"),
            "healthy": state["status"] in {"READY", "BUSY"},
        }
        for pod, state in _pod_state.items()
    }
