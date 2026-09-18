"""Team performance tracking + learning (roadmap 21R).

Records per-team outcomes and derives the evidence-based learning signal:
which models/workers/teams perform best per task type, plus quality, retry,
and verification-failure rates.
"""
from __future__ import annotations


class PerformanceTracker:
    def __init__(self):
        self._records: list = []

    def record(self, team_id: str, task_type: str, *, model: str = "",
               success: bool = True, duration_s: float = 0.0,
               retries: int = 0, verification_failed: bool = False,
               security_findings: int = 0) -> dict:
        entry = {
            "team_id": team_id, "task_type": task_type, "model": model,
            "success": bool(success), "duration_s": duration_s,
            "retries": retries, "verification_failed": verification_failed,
            "security_findings": security_findings,
        }
        self._records.append(entry)
        return entry

    def team_summary(self, team_id: str) -> dict:
        rows = [r for r in self._records if r["team_id"] == team_id]
        total = len(rows)
        if not total:
            return {"team_id": team_id, "tasks": 0}
        successes = sum(1 for r in rows if r["success"])
        return {
            "team_id": team_id,
            "tasks": total,
            "success_rate": round(successes / total, 3),
            "avg_duration_s": round(sum(r["duration_s"] for r in rows) / total, 2),
            "retry_rate": round(sum(r["retries"] for r in rows) / total, 3),
            "verification_failures": sum(1 for r in rows if r["verification_failed"]),
            "security_findings": sum(r["security_findings"] for r in rows),
        }

    def best_for(self, task_type: str) -> str:
        """Team with the highest success rate for a task type (ties: most runs)."""
        rows = [r for r in self._records if r["task_type"] == task_type]
        if not rows:
            return ""
        by_team: dict = {}
        for r in rows:
            s = by_team.setdefault(r["team_id"], {"n": 0, "ok": 0})
            s["n"] += 1
            s["ok"] += 1 if r["success"] else 0
        return max(by_team, key=lambda t: (by_team[t]["ok"] / by_team[t]["n"],
                                           by_team[t]["n"]))
