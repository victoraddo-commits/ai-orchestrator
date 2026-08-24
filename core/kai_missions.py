"""KAI Goal & Mission Engine — JARVIS P11 (§15/§17/§58/§60).

Goals decompose into missions; missions carry tasks executed through the
TOOL BUS (policy-gated) and, where reasoning is needed, the existing
workforce/providers. Nothing executes outside policy.execute — HIGH_RISK
tools still require operator approval even inside an approved mission.

Governance (§58): max mission depth 1 (missions never spawn missions),
max concurrent missions, per-mission budget + deadline, cancellation,
parent always "kai". Workers are NOT silently promoted to permanent agents.

Progress (§60) is computed from task states — never estimated vibes:
    PENDING → RUNNING → DONE | FAILED | BLOCKED
Verification per task via its verify callable/result check (§36); a mission
is VERIFIED only when every task says DONE with evidence.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

_MEMORY_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "memory"
MISSIONS_PATH = _MEMORY_DIR / "kai_missions.json"

MAX_CONCURRENT_MISSIONS = 5
MAX_TASKS_PER_MISSION = 25

_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_all() -> dict:
    try:
        with open(MISSIONS_PATH) as fh:
            return json.load(fh)
    except Exception:
        return {"schema_version": 1, "goals": [], "missions": []}


def _save_all(data: dict) -> None:
    tmp = MISSIONS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, default=str))
    os.replace(tmp, MISSIONS_PATH)


# --- goals -------------------------------------------------------------------

def create_goal(objective: str, desired_outcome: str, constraints: list | None = None,
                deadline: str | None = None, budget_usd: float | None = None,
                priority: str = "normal") -> dict:
    goal = {
        "id": f"goal-{uuid.uuid4().hex[:10]}",
        "objective": objective, "desired_outcome": desired_outcome,
        "constraints": constraints or [], "deadline": deadline,
        "budget_usd": budget_usd, "priority": priority,
        "status": "active", "created_at": _now(), "updated_at": _now(),
        "mission_ids": [],
    }
    with _lock:
        d = _load_all()
        d["goals"].append(goal)
        _save_all(d)
    return goal


# --- missions ----------------------------------------------------------------

def create_mission(objective: str, tasks: list[dict], goal_id: str | None = None,
                   budget_usd: float | None = None, deadline: str | None = None,
                   requires_review: bool = True) -> dict:
    """tasks: [{tool_id, args, description?, verify?}] — tool_ids must exist
    in the registry; risk classes enforced at execution time."""
    from core.kai_tools.registry import REGISTRY
    if len(tasks) > MAX_TASKS_PER_MISSION:
        raise ValueError(f"mission capped at {MAX_TASKS_PER_MISSION} tasks")
    for t in tasks:
        if not t.get("tool_id") or REGISTRY.get(t["tool_id"]) is None:
            raise ValueError(f"unknown tool '{t.get('tool_id')}' in task plan")

    with _lock:
        d = _load_all()
        active = [m for m in d["missions"] if m["status"] == "running"]
        if len(active) >= MAX_CONCURRENT_MISSIONS:
            raise ValueError(f"max {MAX_CONCURRENT_MISSIONS} concurrent missions")

        mission = {
            "id": f"mis-{uuid.uuid4().hex[:10]}",
            "goal_id": goal_id,
            "parent": "kai",
            "objective": objective,
            "status": "planned",
            "requires_review": requires_review,
            "budget_usd": budget_usd,
            "deadline": deadline,
            "created_at": _now(), "updated_at": _now(),
            "checkpoints": [],
            "tasks": [{
                "idx": i,
                "description": t.get("description") or t["tool_id"],
                "tool_id": t["tool_id"],
                "args": t.get("args", {}),
                "verify": t.get("verify"),
                "state": "PENDING",
                "result": None,
                "evidence": None,
            } for i, t in enumerate(tasks)],
        }
        d["missions"].append(mission)
        if goal_id:
            for g in d["goals"]:
                if g["id"] == goal_id:
                    g["mission_ids"].append(mission["id"])
                    g["updated_at"] = _now()
        _save_all(d)
    return mission


def start_mission(mission_id: str) -> dict:
    with _lock:
        d = _load_all()
        m = next((x for x in d["missions"] if x["id"] == mission_id), None)
        if not m:
            raise KeyError(mission_id)
        if m["status"] == "running":
            return m  # idempotent
        if m["status"] != "planned":
            raise ValueError(f"mission {mission_id} is {m['status']}, cannot start")
        m["status"] = "running"
        m["updated_at"] = _now()
        _save_all(d)
        return m


def cancel_mission(mission_id: str, reason: str = "") -> dict:
    """§52-friendly: running tasks already executing finish; no new tasks start."""
    with _lock:
        d = _load_all()
        m = next((x for x in d["missions"] if x["id"] == mission_id), None)
        if not m:
            raise KeyError(mission_id)
        m["status"] = "cancelled"
        m["cancel_reason"] = reason
        m["updated_at"] = _now()
        for t in m["tasks"]:
            if t["state"] in ("PENDING",):
                t["state"] = "BLOCKED"
                t["evidence"] = "mission cancelled"
        _save_all(d)
        return m


def execute_mission(mission_id: str, operator: str = "kai") -> dict:
    """Run pending tasks sequentially through policy.execute. Stops on first
    failed/blocked task unless it's SAFE+failed (recorded, continue)."""
    from core.kai_tools import policy as tool_policy
    from core.kai_executive import log_operation

    start_mission(mission_id)
    summary = {"mission_id": mission_id, "executed": 0, "failed": 0, "blocked": 0}

    for _step in range(MAX_TASKS_PER_MISSION):
        m = get_mission(mission_id)
        if m["status"] == "cancelled":
            break
        task = next((t for t in m["tasks"] if t["state"] == "PENDING"), None)
        if task is None:
            break

        result = tool_policy.execute(task["tool_id"], task.get("args", {}),
                                     operator=operator,
                                     reason=f"mission {mission_id}: {task['description']}")
        with _lock:
            d = _load_all()
            m = next((x for x in d["missions"] if x["id"] == mission_id), {})
            t = m["tasks"][task["idx"]]
            if result.executed and result.ok:
                t["state"] = "DONE"
                t["result"] = _compact(result.data)
                t["evidence"] = f"ok in {result.duration_ms}ms"
                summary["executed"] += 1
            elif result.executed and not result.ok:
                t["state"] = "FAILED"
                t["result"] = None
                t["evidence"] = result.error
                summary["failed"] += 1
            else:  # blocked by policy → approval requested
                t["state"] = "BLOCKED"
                t["evidence"] = f"approval:{result.approval_id} — {result.error}"
                summary["blocked"] += 1
                m["checkpoints"].append({
                    "ts": _now(),
                    "note": f"task {task['idx']} needs approval ({task['tool_id']})"})
            m["updated_at"] = _now()
            # mission-level status rollup
            states = [t["state"] for t in m["tasks"]]
            if all(s == "DONE" for s in states):
                m["status"] = "verifying" if m.get("requires_review") else "done"
            elif any(s == "FAILED" for s in states):
                m["status"] = "failed"
            elif all(s in ("DONE", "BLOCKED", "FAILED") for s in states):
                m["status"] = "awaiting_approval" if any(s == "BLOCKED" for s in states) else "partial"
            else:
                m["status"] = "running"
            _save_all(d)

        log_operation(task["tool_id"], f"mission {mission_id} task {task['idx']}: {t['state']}",
                      ok=(t["state"] == "DONE"), details={"evidence": t["evidence"]})
        if t["state"] in ("FAILED", "BLOCKED"):
            break  # §37: no infinite retry loops; stop and surface

    return summary


def verify_mission(mission_id: str, approved: bool, note: str = "",
                   operator: str = "operator") -> dict:
    """Operator review gate: confirm outputs or reject. Only after review does
    a mission become done/verified (§36/§59)."""
    with _lock:
        d = _load_all()
        m = next((x for x in d["missions"] if x["id"] == mission_id), None)
        if not m:
            raise KeyError(mission_id)
        m["review"] = {"operator": operator, "approved": approved,
                       "note": note, "ts": _now()}
        m["status"] = ("done" if approved else "rejected") if m["status"] in (
            "verifying", "awaiting_approval", "partial", "failed") else m["status"]
        m["updated_at"] = _now()
        _save_all(d)
        return m


def _compact(data) -> str:
    s = json.dumps(data, default=str) if not isinstance(data, str) else data
    return s[:400]


# --- queries ------------------------------------------------------------------

def get_mission(mission_id: str) -> dict:
    d = _load_all()
    m = next((x for x in d["missions"] if x["id"] == mission_id), None)
    if not m:
        raise KeyError(mission_id)
    m["progress_pct"] = progress(m)
    return m


def progress(mission_or_id) -> int:
    """§60: progress from task states only."""
    m = mission_or_id if isinstance(mission_or_id, dict) else get_mission(mission_or_id)
    total = len(m["tasks"]) or 1
    weights = {"PENDING": 0.0, "RUNNING": 0.3, "BLOCKED": 0.4, "FAILED": 0.0, "DONE": 1.0}
    return round(sum(weights[t["state"]] for t in m["tasks"]) / total * 100)


def list_missions(status: str | None = None) -> list:
    d = _load_all()
    rows = d["missions"]
    if status:
        rows = [m for m in rows if m["status"] == status]
    out = []
    for m in sorted(rows, key=lambda x: x["created_at"], reverse=True):
        out.append({"id": m["id"], "objective": m["objective"], "status": m["status"],
                    "progress_pct": progress(m), "goal_id": m.get("goal_id"),
                    "tasks": len(m["tasks"]), "created_at": m["created_at"]})
    return out


def list_goals() -> list:
    d = _load_all()
    return [{**g, "progress_pct": _goal_progress(g)} for g in
            sorted(d["goals"], key=lambda x: x["created_at"], reverse=True)]


def _goal_progress(goal: dict) -> int:
    if not goal.get("mission_ids"):
        return 0
    d = _load_all()
    pcts = []
    for mid in goal["mission_ids"]:
        m = next((x for x in d["missions"] if x["id"] == mid), None)
        if m:
            pcts.append(progress(m))
    return round(sum(pcts) / len(pcts)) if pcts else 0


# --- recurring missions (audit follow-up 2026-08-24) --------------------------
# A scheduled mission template re-materializes as a fresh mission on its
# cadence. Schedules live in memory/kai_mission_schedules.json; the
# orchestrator cycle checks them hourly (same stamp-gate as proactive).

SCHEDULES_PATH = _MEMORY_DIR / "kai_mission_schedules.json"


def create_schedule(name: str, objective: str, tasks: list[dict],
                    interval_hours: float = 24.0, auto_execute: bool = True,
                    requires_review: bool = False) -> dict:
    """Define a recurring mission. Each run creates a NEW mission instance
    (history preserved); auto_execute runs it immediately after creation.
    requires_review=False means a successful run marks itself done without
    an operator gate — use only for SAFE-only task plans."""
    from core.kai_tools.registry import REGISTRY
    for t in tasks:
        if not t.get("tool_id") or REGISTRY.get(t["tool_id"]) is None:
            raise ValueError(f"unknown tool '{t.get('tool_id')}'")
        if requires_review is False:
            spec = REGISTRY.get(t["tool_id"])["spec"]
            if spec.risk != "safe":
                raise ValueError(
                    f"auto-executing schedules may only contain SAFE tools "
                    f"('{t['tool_id']}' is {spec.risk}) — set requires_review=True "
                    f"to gate this schedule through approval")
    try:
        with open(SCHEDULES_PATH) as fh:
            schedules = json.load(fh).get("schedules", [])
    except Exception:
        schedules = []
    sched = {
        "id": f"sched-{uuid.uuid4().hex[:8]}",
        "name": name,
        "objective": objective,
        "tasks": tasks,
        "interval_hours": interval_hours,
        "auto_execute": auto_execute,
        "requires_review": requires_review,
        "last_run": None,
        "mission_ids": [],
        "enabled": True,
        "created_at": _now(),
    }
    schedules.append(sched)
    tmp = SCHEDULES_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps({"schema_version": 1, "schedules": schedules}))
    os.replace(tmp, SCHEDULES_PATH)
    return sched


def list_schedules() -> list:
    try:
        with open(SCHEDULES_PATH) as fh:
            return json.load(fh).get("schedules", [])
    except Exception:
        return []


def due_schedules() -> list:
    now = datetime.now(timezone.utc)
    out = []
    for s in list_schedules():
        if not s.get("enabled"):
            continue
        last = s.get("last_run")
        if not last:
            out.append(s)
            continue
        try:
            elapsed = (now - datetime.fromisoformat(last)).total_seconds() / 3600
            if elapsed >= s["interval_hours"]:
                out.append(s)
        except Exception:
            out.append(s)
    return out


def run_due_schedules(max_runs: int = 3) -> dict:
    """Materialize + execute all due schedules. Called from orchestrator cycle.
    last_run is persisted per-run so a crash can't cause re-runs."""
    results = {"ran": [], "skipped": 0}
    try:
        with open(SCHEDULES_PATH) as fh:
            all_scheds = json.load(fh).get("schedules", [])
    except Exception:
        return results
    now = datetime.now(timezone.utc)
    ran_count = 0
    dirty = False
    for s in all_scheds:
        if not s.get("enabled"):
            continue
        last = s.get("last_run")
        due = False
        if not last:
            due = True
        else:
            try:
                due = (now - datetime.fromisoformat(last)).total_seconds() / 3600 >= s["interval_hours"]
            except Exception:
                due = True
        if not due:
            continue
        if ran_count >= max_runs:
            results["skipped"] += 1
            continue
        ran_count += 1
        try:
            m = create_mission(
                f"[scheduled] {s['name']}", s["tasks"],
                requires_review=s.get("requires_review", False))
            if s.get("auto_execute", True):
                execute_mission(m["id"], operator="kai-schedule")
            got = get_mission(m["id"])
            if not s.get("requires_review", False) and got["progress_pct"] == 100 \
               and got["status"] == "verifying":
                verify_mission(m["id"], approved=True, operator="kai-schedule",
                               note="auto-approved: SAFE-only recurring mission")
            s["last_run"] = _now()
            s.setdefault("mission_ids", []).append(m["id"])
            dirty = True
            results["ran"].append({"schedule": s["id"], "name": s["name"],
                                   "mission": m["id"],
                                   "status": get_mission(m["id"])["status"]})
        except Exception as e:
            results["ran"].append({"schedule": s.get("id"), "name": s.get("name"),
                                   "error": str(e)})
            s["last_run"] = _now()   # failed runs also count as run — no hot loops
            dirty = True
    if dirty:
        tmp = SCHEDULES_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps({"schema_version": 1, "schedules": all_scheds}))
        os.replace(tmp, SCHEDULES_PATH)
    return results
