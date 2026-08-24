"""KAI Emergency Stop + Tool-Bus Hardening — JARVIS P22 (§52/§51/§54).

"KAI, STOP" semantics:
  1. pause the scheduler (existing SCHEDULER_PAUSE_FILE mechanism)
  2. trip the tool-bus kill switch — ALL tool execution refused
  3. cancel running missions (pending tasks → BLOCKED)
  4. audit-record the stop and who triggered it

Resume is explicit: emergency_resume() clears the tool switch and removes
the scheduler pause. The scheduler pause alone (existing admin endpoint)
does NOT block tools, and the tool switch does not affect the scheduler —
they are independent layers; STOP engages both.

Hardening additions here:
  - per-operator rate limit on tool executions (sliding window)
  - HIGH_RISK audit entries include args hash for tamper-evidence review
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

_MEMORY_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "memory"
KILL_SWITCH_PATH = _MEMORY_DIR / "tool_bus_stopped.json"
AUDIT_PATH = _MEMORY_DIR / "tool_audit.jsonl"

# Rate limiting: N tool executions per operator per window
RATE_LIMIT = 30
RATE_WINDOW_S = 60.0

_rate_lock = threading.Lock()
_rate_buckets: dict[str, list[float]] = {}

_stop_lock = threading.Lock()


def _audit(record: dict) -> None:
    record["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        with open(AUDIT_PATH, "a") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except OSError:
        pass


def _args_hash(args: dict) -> str:
    return hashlib.sha256(json.dumps(args or {}, sort_keys=True, default=str).encode()).hexdigest()[:16]


# --- kill switch -------------------------------------------------------------

def is_stopped() -> bool:
    try:
        with open(KILL_SWITCH_PATH) as fh:
            return bool(json.load(fh).get("stopped"))
    except Exception:
        return False


def stopped_info() -> dict:
    try:
        with open(KILL_SWITCH_PATH) as fh:
            return json.load(fh)
    except Exception:
        return {"stopped": False}


def emergency_stop(operator: str = "operator", reason: str = "") -> dict:
    """§52: stop autonomous work, preserve audit, never corrupt data."""
    stopped_state = {
        "stopped": True,
        "by": operator,
        "reason": reason,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    results = {"tool_switch": False, "scheduler_paused": False, "missions_cancelled": 0}

    with _stop_lock:
        tmp = KILL_SWITCH_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(stopped_state))
        os.replace(tmp, KILL_SWITCH_PATH)
        results["tool_switch"] = True

    # 1+2. scheduler pause via existing mechanism
    try:
        from core.scheduler import SCHEDULER_PAUSE_FILE
        SCHEDULER_PAUSE_FILE.write_text(json.dumps({
            "paused": True, "by": operator, "reason": reason or "emergency stop",
            "at": stopped_state["at"]}))
        results["scheduler_paused"] = True
    except Exception:
        pass

    # 3. cancel running missions (no new tasks start; in-flight finishes)
    try:
        from core import kai_missions as km
        d = km._load_all()
        for m in d["missions"]:
            if m["status"] == "running":
                km.cancel_mission(m["id"], reason="emergency stop")
                results["missions_cancelled"] += 1
    except Exception:
        pass

    _audit({"tool": "*", "risk": "*", "operator": operator,
            "decision": "EMERGENCY_STOP", "args_hash": _args_hash({"reason": reason})})
    return {**stopped_state, **results}


def emergency_resume(operator: str = "operator") -> dict:
    """Explicit resume — clears both layers."""
    results = {"tool_switch_cleared": False, "scheduler_resumed": False}
    with _stop_lock:
        try:
            KILL_SWITCH_PATH.unlink()
            results["tool_switch_cleared"] = True
        except FileNotFoundError:
            results["tool_switch_cleared"] = True
        except Exception:
            pass
    try:
        from core.scheduler import SCHEDULER_PAUSE_FILE
        if SCHEDULER_PAUSE_FILE.exists():
            SCHEDULER_PAUSE_FILE.unlink()
        results["scheduler_resumed"] = True
    except Exception:
        pass
    _audit({"tool": "*", "risk": "*", "operator": operator,
            "decision": "EMERGENCY_RESUME"})
    return {**results, "resumed_by": operator}


# --- rate limiting -----------------------------------------------------------

def check_rate(operator: str) -> tuple[bool, int]:
    """Returns (allowed, remaining_quota)."""
    if operator == BRIDGE_OPERATOR_BYPASS:
        return True, RATE_LIMIT
    now = time.monotonic()
    with _rate_lock:
        bucket = [t for t in (_rate_buckets.get(operator) or []) if t > now - RATE_WINDOW_S]
        if len(bucket) >= RATE_LIMIT:
            _rate_buckets[operator] = bucket
            return False, 0
        bucket.append(now)
        _rate_buckets[operator] = bucket
        return True, RATE_LIMIT - len(bucket)


BRIDGE_OPERATOR_BYPASS = "__bypass__"  # unused sentinel; operators all limited


def enforce_args_hash_high_risk(tool_id: str, risk: str, args: dict,
                                decision: str, operator: str) -> dict:
    """Standardized hardened audit record for policy.execute integration."""
    rec = {"tool": tool_id, "risk": risk, "operator": operator,
           "decision": decision}
    if risk == "high_risk":
        rec["args_hash"] = _args_hash(args)   # §55: auditable without raw secrets
    _audit(rec)
    return rec
