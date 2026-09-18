"""Team Execution (TF Phase 8 / 21J).

``Team.execute`` orchestrates a mission's skills across its members: it runs
the plan's parallelizable skills concurrently, then the sequential skills in
dependency order, and emits ``teammate.progress`` per task and
``teammate.blocked`` when a dependency failed. Execution is delegated to a
caller-supplied ``task_runner(teammate, skill_id)`` so the scheduler (not the
planner) owns model/tool dispatch.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

SOURCE = "teammate_execution"


@dataclass
class TaskResult:
    skill_id: str
    teammate_id: str = ""
    status: str = "pending"          # completed | failed | blocked
    output: Any = None
    error: str = ""


@dataclass
class Team:
    mission_id: str
    members: list = field(default_factory=list)
    plan: Any = None
    bus: Any = None
    skill_registry: Any = None

    # -- events -------------------------------------------------------------
    def _emit(self, topic: str, payload: dict) -> None:
        if self.bus is None:
            return
        publish = getattr(self.bus, "publish", None)
        if callable(publish):
            try:
                publish(topic, payload, source=SOURCE)
            except TypeError:
                publish(topic, payload)
        elif callable(self.bus):
            self.bus(topic, payload)

    def _emit_progress(self, result: TaskResult) -> None:
        self._emit("teammate.progress", {
            "mission_id": self.mission_id,
            "skill_id": result.skill_id,
            "teammate_id": result.teammate_id,
            "status": result.status,
        })

    # -- helpers ------------------------------------------------------------
    def _member_for(self, skill_id: str):
        for m in self.members:
            if skill_id in (getattr(m, "skills", None) or []):
                return m
        return self.members[0] if self.members else None

    def _deps(self, skill_id: str, tasks: set) -> list[str]:
        if self.skill_registry is None:
            return []
        rec = self.skill_registry.get(skill_id)
        if rec is None:
            return []
        return [d for d in (rec.dependencies or []) if d in tasks]

    def _run_one(self, task_runner: Callable, skill_id: str) -> TaskResult:
        mate = self._member_for(skill_id)
        tid = getattr(mate, "id", "")
        try:
            output = task_runner(mate, skill_id)
            return TaskResult(skill_id=skill_id, teammate_id=tid,
                              status="completed", output=output)
        except Exception as exc:
            return TaskResult(skill_id=skill_id, teammate_id=tid,
                              status="failed", error=str(exc))

    # -- public API ---------------------------------------------------------
    def execute(self, task_runner: Callable,
                tasks: Optional[list] = None) -> list[TaskResult]:
        if tasks is None:
            tasks = list(getattr(self.plan, "required_skills", []) or [])
        tasks = list(tasks)
        task_set = set(tasks)

        parallel = [s for s in (getattr(self.plan, "parallelizable", []) or [])
                    if s in task_set]
        sequential = [s for s in (getattr(self.plan, "sequential", []) or [])
                      if s in task_set]

        results: dict[str, TaskResult] = {}

        if parallel:
            with ThreadPoolExecutor(max_workers=len(parallel)) as pool:
                futures = {pool.submit(self._run_one, task_runner, s): s
                           for s in parallel}
                for fut in as_completed(futures):
                    result = fut.result()
                    results[result.skill_id] = result
                    self._emit_progress(result)

        for sid in sequential:
            if sid in results:
                continue
            failed = [d for d in self._deps(sid, task_set)
                      if results.get(d) and results[d].status != "completed"]
            if failed:
                blocked = TaskResult(skill_id=sid, status="blocked",
                                     error=f"blocked by {failed}")
                results[sid] = blocked
                self._emit("teammate.blocked", {
                    "mission_id": self.mission_id,
                    "skill_id": sid,
                    "blocked_by": failed,
                })
                continue
            result = self._run_one(task_runner, sid)
            results[sid] = result
            self._emit_progress(result)

        for sid in tasks:
            if sid not in results:
                result = self._run_one(task_runner, sid)
                results[sid] = result
                self._emit_progress(result)

        return [results[sid] for sid in tasks]
