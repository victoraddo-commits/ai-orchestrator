"""Lightweight task progress tracker with optional Telegram reporting."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

PROGRESS_FILE = Path(os.environ.get(
    "TASK_PROGRESS_FILE",
    os.path.join(os.path.dirname(__file__), "..", "memory", "task_progress.json"),
))


class TaskProgressMonitor:
    def __init__(self, task_name: str):
        self.task_name = task_name
        self.tasks: list[dict] = []
        self.start_time = datetime.now(timezone.utc)

    def add_task(self, name: str, estimated_minutes: int = 0):
        self.tasks.append({
            "name": name,
            "status": "pending",
            "estimated_minutes": estimated_minutes,
            "started_at": None,
            "completed_at": None,
        })

    def start_task(self, name: str):
        task = self._find(name)
        task["status"] = "in_progress"
        task["started_at"] = datetime.now(timezone.utc).isoformat()
        self._save()

    def complete_task(self, name: str):
        task = self._find(name)
        task["status"] = "completed"
        task["completed_at"] = datetime.now(timezone.utc).isoformat()
        self._save()

    def fail_task(self, name: str, reason: str = ""):
        task = self._find(name)
        task["status"] = "failed"
        task["completed_at"] = datetime.now(timezone.utc).isoformat()
        task["failure_reason"] = reason
        self._save()

    @property
    def completed_count(self) -> int:
        return sum(1 for t in self.tasks if t["status"] == "completed")

    @property
    def total_count(self) -> int:
        return len(self.tasks)

    @property
    def progress_pct(self) -> float:
        return (self.completed_count / self.total_count * 100) if self.total_count else 0

    def report(self) -> str:
        lines = [f"📊 {self.task_name} Progress\n"]
        for t in self.tasks:
            if t["status"] == "completed":
                icon = "✅"
            elif t["status"] == "in_progress":
                icon = "🔄"
            elif t["status"] == "failed":
                icon = "❌"
            else:
                icon = "⏳"
            lines.append(f"{icon} {t['name']}")
        lines.append(f"\nOverall: {self.progress_pct:.0f}% ({self.completed_count}/{self.total_count})")
        return "\n".join(lines)

    def send_report(self):
        try:
            from core.telegram_bridge import send_message
            send_message(self.report())
        except Exception:
            pass

    def _find(self, name: str) -> dict:
        for t in self.tasks:
            if t["name"] == name:
                return t
        raise KeyError(f"Task not found: {name}")

    def _save(self):
        data = {
            "task_name": self.task_name,
            "start_time": self.start_time.isoformat(),
            "tasks": self.tasks,
        }
        try:
            PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
            PROGRESS_FILE.write_text(json.dumps(data, indent=2))
        except OSError:
            pass
