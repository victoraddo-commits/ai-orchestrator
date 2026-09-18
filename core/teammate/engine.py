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
import uuid
from dataclasses import asdict, replace
from datetime import datetime, timezone
from typing import Any, Optional

from core.memory import load as _load, save as _save

logger = logging.getLogger(__name__)

TEAMS_STORE = "workforce_teams.json"
MISSIONS_STORE = "factory_missions.json"
SCHEMA_VERSION = 1

_ENGINEERING_KEYWORDS = (
    "build", "implement", "feature", "code", "coding", "fix", "refactor",
    "develop", "software", "module", "api", "endpoint", "script",
)
_ENGINEERING_TEAM = ("planner", "coder", "qa", "reviewer")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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

    @staticmethod
    def _teammate_dict(t: Any, created: Optional[bool] = None) -> dict:
        d = asdict(t)
        d["teammate_id"] = d.get("id")
        if created is not None:
            d["created"] = created
        return d

    # ── teams ───────────────────────────────────────────────────────────────
    def _plan_for(self, requirement: Any):
        from core.teammate.planner import plan_team
        plan = plan_team(requirement, self.runtime.skills)
        text = _mission_text(requirement).lower()
        if any(k in text for k in _ENGINEERING_KEYWORDS):
            specs = list(plan.specializations)
            for spec in _ENGINEERING_TEAM:
                if spec not in specs:
                    specs.append(spec)
            ordered = [s for s in _ENGINEERING_TEAM if s in specs]
            ordered += [s for s in specs if s not in ordered]
            plan = replace(plan, specializations=ordered)
        return plan

    def form_team(self, requirement: Any, mission_id: Optional[str] = None,
                  plan: Any = None) -> dict:
        text = _mission_text(requirement)
        if not text:
            raise ValueError("a requirement (or mission) is required")
        plan = plan or self._plan_for(requirement)
        team_id = f"team-{uuid.uuid4().hex[:10]}"
        members = self.runtime.linker.assign_team(mission_id or team_id, requirement, plan=plan)
        record = {
            "id": team_id,
            "requirement": text,
            "mission_id": mission_id,
            "status": "READY",
            "member_ids": [getattr(m, "id", None) for m in members],
            "members": [self._teammate_dict(m) for m in members],
            "specializations": list(getattr(plan, "specializations", []) or []),
            "plan": plan.to_dict() if hasattr(plan, "to_dict") else {},
            "created_at": _now(),
            "updated_at": _now(),
        }
        with self._lock:
            data = self._load_teams()
            data["teams"][team_id] = record
            self._save_teams(data)
        if self.runtime.bus is not None:
            try:
                self.runtime.bus.publish("team.formed", {
                    "team_id": team_id, "mission_id": mission_id,
                    "members": record["member_ids"]}, source="workforce_engine")
            except Exception:
                pass
        return record

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
                       mission_id: Optional[str] = None) -> dict:
        if not goal:
            raise ValueError("goal is required")
        mid = mission_id or f"mis-{uuid.uuid4().hex[:10]}"
        if team_id:
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
            return

        team = Team(mission_id=mission["id"], members=members, plan=plan,
                    bus=self.runtime.bus, skill_registry=self.runtime.skills)
        runner = self.runtime.team_task_runner(mission["goal"], project_path=project_path)
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

        mission["verification"] = self.verify_mission(mission, members)
        all_ok = all(t["status"] == "COMPLETED" for t in mission["tasks"])
        verified = bool(mission["verification"].get("passed"))
        mission["status"] = "COMPLETED" if (all_ok and verified) else (
            "FAILED" if not all_ok else "COMPLETED_UNVERIFIED")
        mission["updated_at"] = _now()
        self._put_mission(mission)
        if self.runtime.bus is not None:
            try:
                self.runtime.bus.publish("mission.completed", {
                    "mission_id": mission["id"], "status": mission["status"],
                    "verified": verified}, source="workforce_engine")
            except Exception:
                pass

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

    def _put_mission(self, mission: dict) -> None:
        with self._lock:
            data = self._load_missions()
            data["missions"][mission["id"]] = mission
            self._save_missions(data)

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
