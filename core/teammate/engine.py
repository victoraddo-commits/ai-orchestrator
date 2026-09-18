"""Workforce / team / mission engine (KAI 2.0 Phase 1).

Thin orchestration layer on top of :class:`core.teammate.runtime.TeammateRuntime`:

* teammates  — create/reuse + persisted listing
* teams      — form from a requirement (planner→coder→qa→reviewer for
               engineering work) and persist the membership
* missions   — decompose a goal into a task graph, assign teammates, execute
               the graph through ``Team.execute(team_task_runner)``, then
               verify independently. Mission state is persisted on every
               transition so it survives a restart (directive §21/§52).

Nothing here re-implements the factory, planner, dispatcher or verification —
they are the library's real APIs, now wired to a live runner.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, replace
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from core.memory import load as _load, save as _save, update as _update

logger = logging.getLogger(__name__)

TEAMS_STORE = "workforce_teams.json"
MISSIONS_STORE = "factory_missions.json"
SCHEMA_VERSION = 1

# Auto-retire (§39): a teammate with no activity for this long is retired by
# the maintenance step unless it is bound to an active mission or marked
# persistent. Tunable for operators.
DEFAULT_IDLE_RETIRE_S = 86400.0

_ENGINEERING_KEYWORDS = (
    "build", "implement", "feature", "code", "coding", "fix", "refactor",
    "develop", "software", "module", "api", "endpoint", "script",
)
_ENGINEERING_TEAM = ("planner", "coder", "qa", "reviewer")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _model_of(output: Any) -> str:
    if isinstance(output, dict):
        for key in ("provider", "model"):
            val = output.get(key)
            if isinstance(val, str) and val:
                return val
    return ""


def _mission_text(obj: Any) -> str:
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        for key in ("goal", "requirement", "description", "objective", "mission", "name", "title"):
            if obj.get(key):
                return str(obj[key])
        return ""
    for attr in ("goal", "requirement", "description", "objective", "name", "title"):
        val = getattr(obj, attr, None)
        if val:
            return str(val)
    return str(obj)


def _lock_store():
    return threading.RLock()


class WorkforceEngine:
    def __init__(self, runtime: Any = None,
                 model_verify: Optional[bool] = None) -> None:
        if runtime is None:
            from core.teammate.runtime import get_runtime
            runtime = get_runtime()
        self.runtime = runtime
        if model_verify is None:
            model_verify = os.environ.get("KAI_TEAM_MODEL_VERIFY", "") == "1"
        self.model_verify = bool(model_verify)
        # Recovery caps (§26/§48): bounded retries on the same worker, then a
        # bounded number of attempts on the replacement teammate.
        self.max_task_retries = int(os.environ.get("KAI_MISSION_TASK_RETRIES", "2"))
        self.max_attempts = int(os.environ.get("KAI_MISSION_MAX_ATTEMPTS", "2"))
        self._lock = _lock_store()

    # ── teammates ───────────────────────────────────────────────────────────
    def create_teammate(self, role: str, skills: Optional[list[str]] = None,
                        model: Optional[str] = None,
                        name: Optional[str] = None) -> dict:
        if not role:
            raise ValueError("role is required")
        result = self.runtime.create_teammate(role, skills=skills, model=model, name=name)
        return self._teammate_dict(result.teammate, created=result.created)

    def list_teammates(self, status: Optional[str] = None,
                       specialization: Optional[str] = None) -> list[dict]:
        return [self._teammate_dict(t) for t in
                self.runtime.registry.list(status=status,
                                           specialization=specialization)]

    def get_teammate(self, teammate_id: str) -> Optional[dict]:
        t = self.runtime.registry.get(teammate_id)
        return self._teammate_dict(t) if t is not None else None

    def retire_teammate(self, teammate_id: str, reason: str = "operator retirement") -> Optional[dict]:
        """Retire a teammate (operator action from the Command Center)."""
        t = self.runtime.registry.get(teammate_id)
        if t is None:
            return None
        self.runtime.registry.retire(teammate_id, reason=reason)
        return self._teammate_dict(self.runtime.registry.get(teammate_id))

    def _active_mission_members(self) -> set:
        members: set = set()
        for mission in self.list_missions():
            if mission.get("status") in ("CREATED", "RUNNING"):
                members.update(mission.get("team_member_ids") or [])
                members.update(t.get("teammate_id") for t in mission.get("tasks", [])
                               if t.get("teammate_id"))
        return members

    def auto_retire(self, *, idle_seconds: Optional[float] = None,
                    retire_failed: bool = True,
                    reason: str = "auto-retire") -> list[dict]:
        """Retire idle/failed teammates per a rule (§39).

        Reuses :meth:`retire_teammate` (the same path the operator endpoint
        uses). Teammates bound to a mission that is still CREATED/RUNNING are
        never retired; a ``resource_limits.persistent`` teammate is never
        retired automatically. Returns the list of retirements performed.
        """
        if idle_seconds is None:
            idle_seconds = float(os.environ.get("KAI_TEAMMATE_IDLE_RETIRE_S",
                                                DEFAULT_IDLE_RETIRE_S))
        now = datetime.now(timezone.utc)
        protected = self._active_mission_members()
        retired: list[dict] = []
        for t in list(self.runtime.registry.list()):
            tid = getattr(t, "id", None)
            if not tid or tid in protected:
                continue
            if (getattr(t, "resource_limits", {}) or {}).get("persistent"):
                continue
            status = getattr(t, "status", "")
            if status == "RETIRED":
                continue
            why = ""
            if retire_failed and (status == "FAILED" or
                                  getattr(t, "health", "HEALTHY") == "FAILED"):
                why = "failed"
            else:
                last = (_parse_dt(getattr(t, "last_active", ""))
                        or _parse_dt(getattr(t, "created_at", "")))
                age = (now - last).total_seconds() if last else float("inf")
                if status in ("READY", "ASSIGNED", "WAITING", "COMPLETED") and \
                        age >= idle_seconds:
                    why = f"idle {int(age)}s"
            if not why:
                continue
            self.retire_teammate(tid, reason=f"{reason}: {why}")
            retired.append({"teammate_id": tid, "reason": why})
            self._emit("teammate.retired", {
                "teammate_id": tid, "reason": why, "automatic": True})
        return retired

    # ── performance / learning (§40/§41) ────────────────────────────────────
    @staticmethod
    def _perf_score(mate: Any) -> tuple:
        pm = getattr(mate, "performance_metrics", None) or {}
        return (float(pm.get("success_rate", 0.0) or 0.0),
                int(pm.get("tasks_completed", 0) or 0))

    def _best_capable_member(self, skill_id: str, members: list,
                             exclude_id: Optional[str] = None) -> Any:
        """Pick the highest-performing healthy member that has ``skill_id``.

        The learning signal (§40): among equally-capable teammates prefer the
        one with the best recorded success rate, then the most completed
        tasks. Falls back to None when nobody qualifies.
        """
        candidates = [
            m for m in members
            if getattr(m, "id", None) != exclude_id
            and skill_id in (getattr(m, "skills", None) or [])
            and getattr(m, "health", "HEALTHY") == "HEALTHY"
            and getattr(m, "status", "") != "RETIRED"
        ]
        if not candidates:
            return None
        return max(candidates, key=self._perf_score)

    @staticmethod
    def _teammate_dict(t: Any, created: Optional[bool] = None) -> dict:
        d = asdict(t)
        d["teammate_id"] = d.get("id")
        if created is not None:
            d["created"] = created
        return d

    # ── teams ───────────────────────────────────────────────────────────────
    def _plan_for(self, requirement: Any):
        from core.teammate.planner import (
            SPECIALIST_CATALOG, _max_level, _partition, plan_team,
        )
        plan = plan_team(requirement, self.runtime.skills)
        text = _mission_text(requirement).lower()
        if not any(k in text for k in _ENGINEERING_KEYWORDS):
            return plan

        specs = list(plan.specializations)
        for spec in _ENGINEERING_TEAM:
            if spec not in specs:
                specs.append(spec)
        ordered = [s for s in _ENGINEERING_TEAM if s in specs]
        ordered += [s for s in specs if s not in ordered]

        # Recompute the task set from the FULL team — plan_team only saw the
        # matched specializations (e.g. coder for "build a feature"), so the
        # augmented planner/qa/reviewer skills must be folded in explicitly.
        skill_ids: list[str] = []
        for spec in ordered:
            for sid in (SPECIALIST_CATALOG.get(spec, {}).get("skills") or []):
                if sid not in skill_ids and self.runtime.skills.get(sid) is not None:
                    skill_ids.append(sid)
        changed = True
        while changed:
            changed = False
            for sid in list(skill_ids):
                for dep in (self.runtime.skills.get(sid).dependencies or []):
                    if dep not in skill_ids and self.runtime.skills.get(dep) is not None:
                        skill_ids.append(dep)
                        changed = True

        skills = {sid: self.runtime.skills.get(sid) for sid in skill_ids}
        tools: set = set()
        perms: dict = {"secrets": set(), "network": set(), "filesystem": set()}
        models: list[str] = []
        verification: list[str] = []
        levels: list[str] = [SPECIALIST_CATALOG.get(s, {}).get("risk_level", "low")
                             for s in ordered]
        for sid in skill_ids:
            skill = skills[sid]
            forbidden = set(skill.forbidden_tools or [])
            tools.update(t for t in (skill.allowed_tools or []) if t not in forbidden)
            rp = skill.required_permissions or {}
            for bucket in ("secrets", "network", "filesystem"):
                perms[bucket].update(rp.get(bucket, []) or [])
            for role in (skill.model_requirements or {}).get("roles", []) or []:
                if role not in models:
                    models.append(role)
            if skill.verification_method and skill.verification_method not in verification:
                verification.append(skill.verification_method)
            levels.append((skill.security_requirements or {}).get("level", "low"))

        parallelizable, sequential = _partition(skills, skill_ids)
        expertise = [SPECIALIST_CATALOG[s]["expertise"] for s in ordered
                     if SPECIALIST_CATALOG.get(s, {}).get("expertise")]
        return replace(
            plan,
            specializations=ordered,
            required_expertise=expertise,
            required_skills=skill_ids,
            required_tools=sorted(tools),
            required_permissions={k: sorted(v) for k, v in perms.items()},
            required_models=models,
            expected_workload={
                "specialists": len(ordered),
                "skills": len(skill_ids),
                "estimated_timeout_s": (plan.expected_workload or {}).get(
                    "estimated_timeout_s", 0),
            },
            parallelizable=parallelizable,
            sequential=sequential,
            verification_requirements=verification,
            security_requirements={"level": _max_level(levels)},
            risk_level=_max_level(levels),
        )

    def _persist_team(self, team_id: str, requirement: str,
                      mission_id: Optional[str], plan: Any,
                      members: list) -> dict:
        record = {
            "id": team_id,
            "requirement": requirement,
            "mission_id": mission_id,
            "status": "READY",
            "member_ids": [getattr(m, "id", None) for m in members],
            "members": [self._teammate_dict(m) for m in members],
            "specializations": list(getattr(plan, "specializations", []) or []),
            "plan": plan.to_dict() if hasattr(plan, "to_dict") else {},
            "created_at": _now(),
            "updated_at": _now(),
        }
        self._atomic_put(TEAMS_STORE, "teams", team_id, record)
        if self.runtime.bus is not None:
            try:
                self.runtime.bus.publish("team.formed", {
                    "team_id": team_id, "mission_id": mission_id,
                    "members": record["member_ids"]}, source="workforce_engine")
            except Exception:
                pass
        return record

    def form_team(self, requirement: Any, mission_id: Optional[str] = None,
                  plan: Any = None) -> dict:
        text = _mission_text(requirement)
        if not text:
            raise ValueError("a requirement (or mission) is required")
        plan = plan or self._plan_for(requirement)
        team_id = f"team-{uuid.uuid4().hex[:10]}"
        members = self.runtime.linker.assign_team(mission_id or team_id, requirement, plan=plan)
        return self._persist_team(team_id, text, mission_id, plan, members)

    def _explicit_plan(self, skills: list, mission_id: str,
                       specialization: Optional[str]) -> Any:
        """Build a minimal TeamPlan for an explicit skill set (§37).

        Used by module capability requests: the module names the skills it
        needs, so the planner does not have to re-derive them from prose.
        """
        from core.teammate.planner import TeamPlan

        ids = [s for s in skills if self.runtime.skills.get(s) is not None]
        if not ids:
            raise ValueError("no registered skills for capability mission")
        spec = specialization or "module_specialist"
        return TeamPlan(
            mission_id=mission_id,
            specializations=[spec],
            required_expertise=[spec],
            required_skills=ids,
            parallelizable=list(ids),
            sequential=[],
            security_requirements={"level": "low"},
            risk_level="low",
            expected_workload={"specialists": 1, "skills": len(ids)},
        )

    def _explicit_team(self, goal: str, plan: Any,
                       mission_id: str) -> dict:
        spec = plan.specializations[0] if plan.specializations else "module_specialist"
        result = self.runtime.create_teammate(spec, skills=plan.required_skills)
        members = [result.teammate]
        return self._persist_team(
            f"team-{uuid.uuid4().hex[:10]}", goal, mission_id, plan, members)

    def list_teams(self) -> list[dict]:
        data = self._load_teams()
        return sorted(data["teams"].values(), key=lambda t: t.get("created_at", ""),
                      reverse=True)

    def get_team(self, team_id: str) -> Optional[dict]:
        return self._load_teams()["teams"].get(team_id)

    # ── missions ────────────────────────────────────────────────────────────
    def create_mission(self, goal: str, execute: bool = True,
                       background: bool = False,
                       project_path: Optional[str] = None,
                       team_id: Optional[str] = None,
                       mission_id: Optional[str] = None,
                       skills: Optional[list] = None,
                       specialization: Optional[str] = None) -> dict:
        if not goal:
            raise ValueError("goal is required")
        mid = mission_id or f"mis-{uuid.uuid4().hex[:10]}"
        if skills:
            plan = self._explicit_plan(skills, mid, specialization)
            team = self._explicit_team(goal, plan, mid)
            members = [self.runtime.registry.get(x) for x in team["member_ids"]]
            members = [m for m in members if m is not None]
        elif team_id:
            team = self.get_team(team_id)
            if team is None:
                raise KeyError(f"unknown team {team_id}")
            plan = self._plan_for(goal)
            members = [self.runtime.registry.get(x) for x in team["member_ids"]]
            members = [m for m in members if m is not None]
        else:
            plan = self._plan_for(goal)
            team = self.form_team(goal, mission_id=mid, plan=plan)
            members = [self.runtime.registry.get(x) for x in team["member_ids"]]
            members = [m for m in members if m is not None]

        tasks = []
        for skill_id in list(getattr(plan, "required_skills", []) or []):
            mate = next((m for m in members if skill_id in (m.skills or [])), None)
            tasks.append({
                "task_id": skill_id,
                "skill_id": skill_id,
                "teammate_id": getattr(mate, "id", None),
                "status": "PENDING",
                "output": None,
                "error": "",
                "verification": None,
                "recovery": [],
            })

        mission = {
            "id": mid,
            "goal": goal,
            "status": "CREATED",
            "team_id": team["id"],
            "team_member_ids": list(team["member_ids"]),
            "tasks": tasks,
            "verification": None,
            "project_path": project_path,
            "created_at": _now(),
            "updated_at": _now(),
        }
        self._put_mission(mission)
        if self.runtime.bus is not None:
            try:
                self.runtime.bus.publish("mission.created",
                                         {"mission_id": mid, "goal": goal},
                                         source="workforce_engine")
            except Exception:
                pass

        if execute:
            if background:
                t = threading.Thread(target=self._run_mission_safe,
                                     args=(mid, goal, members, plan, project_path),
                                     name=f"mission-{mid}", daemon=True)
                t.start()
            else:
                self._run_mission(mission, members, plan, project_path)
        return self.get_mission(mid)

    def execute_mission(self, mission_id: str,
                        project_path: Optional[str] = None) -> dict:
        mission = self.get_mission(mission_id)
        if mission is None:
            raise KeyError(mission_id)
        team = self.get_team(mission["team_id"]) or {"member_ids": mission.get("team_member_ids", [])}
        members = [self.runtime.registry.get(x) for x in team["member_ids"]]
        members = [m for m in members if m is not None]
        plan = self._plan_for(mission["goal"])
        self._run_mission(mission, members, plan, project_path or mission.get("project_path"))
        return self.get_mission(mission_id)

    def _run_mission_safe(self, mission_id, goal, members, plan, project_path):
        try:
            mission = self.get_mission(mission_id)
            self._run_mission(mission, members, plan, project_path)
        except Exception as exc:  # background thread must never crash the API
            logger.exception("mission %s failed: %s", mission_id, exc)
            m = self.get_mission(mission_id)
            if m is not None:
                m["status"] = "FAILED"
                m["error"] = f"{type(exc).__name__}: {exc}"
                m["updated_at"] = _now()
                self._put_mission(m)

    # ── recovery loop (§26 / §48 / §49) ─────────────────────────────────────
    def _emit(self, topic: str, payload: dict) -> None:
        if self.runtime.bus is None:
            return
        try:
            self.runtime.bus.publish(topic, payload, source="workforce_engine")
        except Exception:
            logger.debug("event publish failed for %s", topic, exc_info=True)

    @staticmethod
    def _classify_failure(error: str) -> str:
        """Map a task error string to a recovery failure class.

        Reuses the classes declared in :mod:`core.teammate.recovery`
        (ACTION_BY_CLASS) rather than inventing a parallel taxonomy.
        """
        text = (error or "").lower()
        if any(k in text for k in ("all providers failed", "no available provider",
                                   "provider", "model", "quota", "circuit",
                                   "not available", "disabled")):
            return "model_failure"
        if any(k in text for k in ("worker", "unavailable", "vanished",
                                   "gone", "offline", "dead")):
            return "worker_unavailable"
        if any(k in text for k in ("resource", "memory", "cpu", "exhaust",
                                   "timeout", "timed out")):
            return "resource_exhaustion"
        if any(k in text for k in ("security", "unauthorized", "forbidden",
                                   "guarddenied", "capabilit")):
            return "security_violation"
        return "tool_failure"

    def _replacement_member(self, failed_member: Any, skill_id: str,
                            members: list) -> Any:
        """Create/reuse a healthy replacement teammate for ``skill_id``.

        The replacement keeps the same specialization/skills so the mission's
        logical worker identity survives; only the physical teammate is new.
        Falls back to any existing healthy member that can run the skill.
        """
        spec = getattr(failed_member, "specialization", None)
        skill = self.runtime.skills.get(skill_id)
        caps = list((skill.required_capabilities if skill else None) or
                    getattr(failed_member, "capabilities", []) or [])
        try:
            result = self.runtime.factory.create_from_requirement(
                {"specialization": spec,
                 "required_skills": [skill_id],
                 "capabilities": caps,
                 "name": f"{spec or 'worker'}-replacement"},
                mission_id=None,
            )
            mate = result.teammate
            if mate is not None and getattr(mate, "id", None) != getattr(failed_member, "id", None):
                members.append(mate)
                return mate
        except Exception as exc:  # a replacement is best-effort; fall back
            logger.warning("replacement teammate creation failed: %s", exc)

        # Fall back to the highest-performing existing healthy member that can
        # run the skill (§40 learning signal).
        best = self._best_capable_member(
            skill_id, members, exclude_id=getattr(failed_member, "id", None))
        if best is not None:
            return best
        return failed_member

    def _retry_task(self, mission: dict, task: dict, members: list,
                    runner: Callable, attempts_left: int) -> bool:
        """Retry ``task`` in place up to ``attempts_left`` times.

        Returns True when the task completes. Each attempt is recorded on the
        task so the mission keeps an auditable recovery trail.
        """
        for n in range(attempts_left):
            mate = next((m for m in members
                         if getattr(m, "id", None) == task.get("teammate_id")), None)
            if mate is None:
                mate = (self._best_capable_member(task["skill_id"], members)
                        or (members[0] if members else None))
            try:
                output = runner(mate, task["skill_id"])
            except Exception as exc:  # retryable — record and try again
                task.setdefault("recovery", []).append(
                    {"action": "retry", "attempt": n + 1,
                     "error": f"{type(exc).__name__}: {exc}"})
                task["error"] = f"{type(exc).__name__}: {exc}"
                self._put_mission(mission)
                continue
            task["status"] = "COMPLETED"
            task["output"] = _truncate(output)
            task["error"] = ""
            task["teammate_id"] = getattr(mate, "id", task.get("teammate_id"))
            return True
        return False

    def _record_metric(self, mate: Any, skill_id: str, success: bool,
                       latency_ms: float, model: str) -> None:
        tid = getattr(mate, "id", None)
        if not tid:
            return
        try:
            self.runtime.registry.record_performance(
                tid, success=success, latency_ms=latency_ms, model=model,
                skill_id=skill_id)
        except Exception:  # metrics must never break a mission
            logger.debug("performance record failed", exc_info=True)

    def _instrumented_runner(self, base_runner: Callable) -> Callable:
        """Time every task attempt and persist §41 metrics on the teammate."""
        def _run(mate: Any, skill_id: str):
            start = time.monotonic()
            try:
                output = base_runner(mate, skill_id)
            except Exception:
                self._record_metric(mate, skill_id, False,
                                    (time.monotonic() - start) * 1000.0, "")
                raise
            self._record_metric(mate, skill_id, True,
                                (time.monotonic() - start) * 1000.0,
                                _model_of(output))
            return output
        return _run

    def _run_mission(self, mission: dict, members: list, plan: Any,
                     project_path: Optional[str]) -> None:
        from core.teammate.execution import Team

        mission["status"] = "RUNNING"
        mission["updated_at"] = _now()
        self._put_mission(mission)

        if not members:
            mission["status"] = "FAILED"
            mission["error"] = "no teammates available for mission"
            mission["updated_at"] = _now()
            self._put_mission(mission)
            self._emit("mission.failed", {
                "mission_id": mission["id"], "status": "FAILED",
                "diagnosis": mission["error"]})
            return

        team = Team(mission_id=mission["id"], members=members, plan=plan,
                    bus=self.runtime.bus, skill_registry=self.runtime.skills)
        failovers: list = []

        def _on_failover(skill_id, provider, error):
            failovers.append({"skill_id": skill_id, "from_model": provider,
                              "error": error})

        runner = self._instrumented_runner(self.runtime.team_task_runner(
            mission["goal"], project_path=project_path,
            mission_id=mission["id"], on_failover=_on_failover))
        results = team.execute(runner, tasks=[t["skill_id"] for t in mission["tasks"]])

        by_skill = {r.skill_id: r for r in results}
        for task in mission["tasks"]:
            r = by_skill.get(task["skill_id"])
            if r is None:
                continue
            task["status"] = "COMPLETED" if r.status == "completed" else r.status.upper()
            task["teammate_id"] = r.teammate_id or task.get("teammate_id")
            task["output"] = _truncate(r.output)
            task["error"] = r.error
        self._put_mission(mission)

        # Recover each failed task: bounded retry → replacement teammate →
        # (for a model failure) a compatible model chosen by the fabric.
        recovered_any = False
        for task in mission["tasks"]:
            if task["status"] != "FAILED":
                continue
            failure_class = self._classify_failure(task.get("error", ""))
            task["failure_class"] = failure_class
            task.setdefault("recovery", [])

            if self._retry_task(mission, task, members, runner,
                                self.max_task_retries):
                recovered_any = True
                task["recovered_by"] = "retry"
                self._put_mission(mission)
                continue

            failed_member = next(
                (m for m in members
                 if getattr(m, "id", None) == task.get("teammate_id")), None)
            replacement = self._replacement_member(
                failed_member, task["skill_id"], members)
            task["replaced_teammate_id"] = getattr(replacement, "id", None)
            task["recovery"].append(
                {"action": "replace_teammate",
                 "failure_class": failure_class,
                 "from": getattr(failed_member, "id", None),
                 "to": getattr(replacement, "id", None)})

            repl_runner = self._instrumented_runner(self.runtime.team_task_runner(
                mission["goal"], project_path=project_path,
                mission_id=mission["id"], on_failover=_on_failover))
            if self._retry_task(mission, task, [replacement], repl_runner,
                                self.max_attempts):
                recovered_any = True
                task["recovered_by"] = "replace_teammate"
            else:
                task["status"] = "FAILED"
                task["error"] = task.get("error") or "recovery exhausted"
            self._put_mission(mission)

        mission["verification"] = self.verify_mission(mission, members)
        all_ok = all(t["status"] == "COMPLETED" for t in mission["tasks"])
        verified = bool(mission["verification"].get("passed"))
        mission["status"] = "COMPLETED" if (all_ok and verified) else (
            "FAILED" if not all_ok else "COMPLETED_UNVERIFIED")
        mission["updated_at"] = _now()
        self._put_mission(mission)

        if failovers:
            for f in failovers:
                for t in mission["tasks"]:
                    if t["skill_id"] == f["skill_id"]:
                        t.setdefault("recovery", []).append(
                            {"action": "replace_model",
                             "from_model": f["from_model"],
                             "error": f["error"],
                             "failure_class": "model_failure"})
                        t["recovered_by"] = t.get("recovered_by") or "replace_model"
            self._put_mission(mission)

        if recovered_any or failovers:
            recovered = sorted({
                *(t["skill_id"] for t in mission["tasks"] if t.get("recovered_by")),
                *(f["skill_id"] for f in failovers),
            })
            self._emit("mission.recovered", {
                "mission_id": mission["id"],
                "status": mission["status"],
                "recovered": recovered,
                "tasks": recovered,
                "model_failovers": failovers,
                "verified": verified})
        if mission["status"] == "FAILED":
            diagnoses = [
                {"skill_id": t["skill_id"],
                 "failure_class": t.get("failure_class", ""),
                 "error": t.get("error", ""),
                 "recovery": t.get("recovery", [])}
                for t in mission["tasks"] if t["status"] != "COMPLETED"]
            mission["diagnosis"] = diagnoses
            self._put_mission(mission)
            self._emit("mission.failed", {
                "mission_id": mission["id"], "status": "FAILED",
                "diagnosis": diagnoses})
        else:
            self._emit("mission.completed", {
                "mission_id": mission["id"], "status": mission["status"],
                "verified": verified})

    # ── verification ────────────────────────────────────────────────────────
    def verify_mission(self, mission: dict, members: list) -> dict:
        from core.teammate.verification import select_verifier, verify_output

        producer = next((m for m in members if "write_code" in (m.skills or [])),
                        members[0] if members else None)
        verifier = select_verifier(producer, members, self.runtime.skills) if producer else None

        tasks = mission["tasks"]

        def _all_completed(_out):
            bad = [t["task_id"] for t in tasks if t["status"] != "COMPLETED"]
            return (not bad, f"incomplete: {bad}" if bad else "all tasks completed")

        def _outputs_present(_out):
            missing = [t["task_id"] for t in tasks
                       if t["status"] == "COMPLETED" and not t.get("output")]
            return (not missing, f"missing output: {missing}" if missing else "outputs present")

        checks = [("all_completed", _all_completed), ("outputs_present", _outputs_present)]

        if verifier is not None:
            result = verify_output(producer, verifier, tasks, checks,
                                   bus=self.runtime.bus)
            payload = result.to_dict()
        else:
            outcomes = []
            passed = True
            for name, fn in checks:
                ok, detail = fn(tasks)
                passed = passed and ok
                outcomes.append({"check": name, "passed": ok, "detail": detail})
            payload = {"passed": passed, "verifier_id": "",
                       "checks": outcomes, "feedback": ""}

        if self.model_verify and verifier is not None:
            payload["model"] = self._model_verify(verifier, mission)
            if payload["model"] is not None:
                payload["passed"] = bool(payload["passed"] and payload["model"].get("passed"))
        return payload

    def _model_verify(self, verifier: Any, mission: dict) -> Optional[dict]:
        """Independent model review: ask a second teammate to judge the outputs."""
        outputs = [
            {"skill": t["skill_id"], "status": t["status"],
             "output": (t.get("output") or "")[:1200]}
            for t in mission["tasks"]
        ]
        prompt = (
            "You are an independent verifier. Decide whether the mission output "
            "satisfies the goal. Reply with PASS or FAIL on the first line, then a "
            "one-sentence reason.\n\n"
            f"GOAL: {mission['goal']}\n\n"
            f"OUTPUTS: {outputs}\n"
        )
        skill_id = ("verify_endpoint" if "verify_endpoint" in (verifier.skills or [])
                    else "inspect_repository")
        try:
            result = self.runtime.ask(verifier, skill_id, prompt)
        except Exception as exc:
            logger.warning("model verification failed: %s", exc)
            return {"passed": False, "error": f"{type(exc).__name__}: {exc}", "raw": ""}
        text = _response_text(result)
        verdict = "PASS" if text.strip().upper().startswith("PASS") else "FAIL"
        return {"passed": verdict == "PASS", "verdict": verdict, "raw": text[:600]}

    # ── persistence ─────────────────────────────────────────────────────────
    def _load_teams(self) -> dict:
        raw = _load(TEAMS_STORE)
        if not isinstance(raw, dict) or not isinstance(raw.get("teams"), dict):
            return {"schema_version": SCHEMA_VERSION, "teams": {}}
        return raw

    def _save_teams(self, data: dict) -> None:
        data["schema_version"] = SCHEMA_VERSION
        _save(TEAMS_STORE, data)

    def _load_missions(self) -> dict:
        raw = _load(MISSIONS_STORE)
        if not isinstance(raw, dict) or not isinstance(raw.get("missions"), dict):
            return {"schema_version": SCHEMA_VERSION, "missions": {}}
        return raw

    def _save_missions(self, data: dict) -> None:
        data["schema_version"] = SCHEMA_VERSION
        _save(MISSIONS_STORE, data)

    def _atomic_put(self, store: str, collection: str, key: str,
                    record: dict) -> dict:
        """Cross-process safe read-modify-write of one record in ``store``.

        Two processes (the API and ``kai-scheduler``) both own a
        ``WorkforceEngine``. A process-local ``RLock`` cannot stop them from
        clobbering each other's whole-file load-modify-save, which is how the
        engine lost missions from ``factory_missions.json``. Reuse the existing
        fcntl.flock-backed :func:`core.memory.update` so the reload + merge +
        atomic replace runs inside ONE file lock (directive §21/§52).
        """

        def _apply(data: dict) -> dict:
            if not isinstance(data, dict) or not isinstance(data.get(collection), dict):
                data = {"schema_version": SCHEMA_VERSION, collection: {}}
            data["schema_version"] = SCHEMA_VERSION
            data[collection][key] = record
            return data

        with self._lock:
            return _update(store, _apply)

    def _put_mission(self, mission: dict) -> None:
        self._atomic_put(MISSIONS_STORE, "missions", mission["id"], mission)

    def get_mission(self, mission_id: str) -> Optional[dict]:
        return self._load_missions()["missions"].get(mission_id)

    def list_missions(self, status: Optional[str] = None) -> list[dict]:
        rows = list(self._load_missions()["missions"].values())
        if status:
            rows = [m for m in rows if m.get("status") == status]
        return sorted(rows, key=lambda m: m.get("created_at", ""), reverse=True)


# ── helpers ─────────────────────────────────────────────────────────────────
def _truncate(value: Any, limit: int = 2000) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        return value[:limit]
    try:
        return str(value)[:limit]
    except Exception:
        return value


def _response_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        resp = result.get("response", result)
        if isinstance(resp, str):
            return resp
        if isinstance(resp, dict):
            for key in ("response", "text", "content"):
                if isinstance(resp.get(key), str):
                    return resp[key]
        return str(resp)
    return str(result)
