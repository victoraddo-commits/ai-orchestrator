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
    def _block(self, sid: str, failed: list) -> TaskResult:
        """Record a blocked task and emit ``teammate.blocked``."""
        blocked = TaskResult(skill_id=sid, status="blocked",
                             error=f"blocked by {failed}" if failed
                             else "dependency cycle")
        self._emit("teammate.blocked", {
            "mission_id": self.mission_id,
            "skill_id": sid,
            "blocked_by": list(failed),
            **({"reason": "dependency cycle"} if not failed else {}),
        })
        return blocked

    def execute(self, task_runner: Callable,
                tasks: Optional[list] = None,
                task_runner_for: Optional[Callable] = None) -> list[TaskResult]:
        """Run ``tasks`` across the team honouring the skill dependency DAG.

        Independent tasks (all in-set dependencies satisfied) run concurrently
        in waves; a task whose dependency failed or was blocked is itself
        blocked and never executed; a dependency cycle is detected and the
        cycle members are blocked rather than hanging. Result order always
        matches ``tasks``.

        ``task_runner_for`` is an optional ``(teammate, skill_id) -> runner``
        factory that lets a caller swap the worker (e.g. a replacement
        teammate) without rebuilding the team. When omitted the single
        ``task_runner`` is used for every task.
        """
        if tasks is None:
            tasks = list(getattr(self.plan, "required_skills", []) or [])
        tasks = list(tasks)
        task_set = set(tasks)
        deps = {sid: self._deps(sid, task_set) for sid in tasks}

        def _runner_for(skill_id: str) -> Callable:
            if task_runner_for is None:
                return task_runner
            mate = self._member_for(skill_id)
            return task_runner_for(mate, skill_id)

        results: dict[str, TaskResult] = {}
        remaining = list(tasks)

        while remaining:
            ready, blocked = [], []
            for sid in remaining:
                states = [results.get(d) for d in deps[sid]]
                if any(st is not None and st.status != "completed" for st in states):
                    blocked.append(sid)
                elif all(st is not None and st.status == "completed" for st in states):
                    ready.append(sid)

            for sid in blocked:
                failed = [d for d in deps[sid]
                          if results.get(d) and results[d].status != "completed"]
                results[sid] = self._block(sid, failed)
                remaining.remove(sid)

            # Only a wave with neither ready nor blocked work is a cycle:
            # blocking a task IS progress (it unblocks classification of its
            # own dependents on the next pass).
            if not ready and not blocked:
                for sid in list(remaining):
                    results[sid] = self._block(sid, [])
                    remaining.remove(sid)
                break
            if not ready:
                continue

            wave = [s for s in ready if s in remaining]
            if len(wave) == 1:
                result = self._run_one(_runner_for(wave[0]), wave[0])
                results[result.skill_id] = result
                self._emit_progress(result)
            else:
                with ThreadPoolExecutor(max_workers=len(wave)) as pool:
                    futures = {pool.submit(self._run_one, _runner_for(s), s): s
                               for s in wave}
                    for fut in as_completed(futures):
                        result = fut.result()
                        results[result.skill_id] = result
                        self._emit_progress(result)
            for sid in wave:
                if sid in remaining:
                    remaining.remove(sid)

        return [results[sid] for sid in tasks]
