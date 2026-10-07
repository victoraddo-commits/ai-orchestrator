"""19O: Cerebrum Command Center — mission control for the brain.

Discovers and monitors all cerebrum subsystems (memory, knowledge,
trust, brain health, AI providers, build pipeline, roadmap, etc.),
produces full status reports, and formats alerts/dashboards.

Used by:
  - core.api.py for GET /kai/command-center endpoint
  - core/kai/command_center.html dashboard panel
  - Telegram bridge for !health and !cerebrum commands
"""

import json
import os
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class CommandCenter:
    """Mission control for all cerebrum modules.

    Discovers available subsystems, aggregates their status, and
    produces human-readable dashboards and alerts.
    """

    CEREBRUM_VERSION = "19O"

    def __init__(self, db_path, memory_dir=None):
        """
        Args:
            db_path: Path to the legal brain SQLite database
            memory_dir: Path to the memory directory (default: from env)
        """
        self.db_path = Path(db_path)
        self.memory_dir = Path(memory_dir) if memory_dir else Path(
            os.environ.get("AI_ORCHESTRATOR_MEMORY_DIR",
                          str(Path(__file__).resolve().parent.parent.parent / "memory"))
        )

    # -- Module discovery ---------------------------------------------------

    def discover_modules(self) -> Dict[str, Any]:
        """Discover and assess all cerebrum subsystems.

        Returns a dict keyed by module name, each with {available, status, ...}
        """
        modules = {}

        # Memory system
        modules["memory_system"] = self._check_memory()

        # Kai identity
        modules["kai_identity"] = self._check_kai_identity()

        # Build pipeline
        modules["build_pipeline"] = self._check_build_pipeline()

        # Roadmap
        modules["roadmap"] = self._check_roadmap()

        # Knowledge engine
        modules["knowledge_engine"] = self._check_knowledge()

        # Trust engine
        modules["trust_engine"] = self._check_trust()

        # Brain health
        modules["brain_health"] = self._check_brain_health()

        # AI providers
        modules["ai_providers"] = self._check_providers()

        # Conversation
        modules["conversation"] = self._check_conversation()

        # Research sessions
        modules["research_sessions"] = self._check_research_sessions()

        # Workspace
        modules["workspace"] = self._check_workspace()

        return modules

    def _check_memory(self) -> Dict[str, Any]:
        """Check memory directory health."""
        available = self.memory_dir.exists() and self.memory_dir.is_dir()
        file_count = 0
        if available:
            try:
                file_count = len(list(self.memory_dir.glob("*.json")))
            except OSError:
                pass
        return {
            "available": available,
            "status": "healthy" if available and file_count > 0 else "degraded",
            "memory_dir": str(self.memory_dir),
            "memory_file_count": file_count,
        }

    def _check_kai_identity(self) -> Dict[str, Any]:
        """Check Kai identity config."""
        claude_md = Path(__file__).resolve().parent.parent.parent / "CLAUDE.md"
        name = "Kai"
        version = "13A"
        if claude_md.exists():
            try:
                for line in claude_md.read_text().splitlines():
                    if line.startswith("- **Name**:") or line.startswith("- Name:"):
                        name = line.split(":")[1].strip()
                    elif "version" in line.lower() and ":" in line:
                        version = line.split(":")[1].strip().split()[0]
            except OSError:
                pass
        return {
            "available": claude_md.exists(),
            "status": "healthy" if claude_md.exists() else "degraded",
            "name": name,
            "version": version,
        }

    def _check_build_pipeline(self) -> Dict[str, Any]:
        """Check build pipeline from memory/builds.json."""
        builds_file = self.memory_dir / "builds.json"
        total = completed = active = failed = 0
        available = builds_file.exists()
        if available:
            try:
                data = json.loads(builds_file.read_text())
                records = data.get("records", [])
                total = len(records)
                completed = sum(1 for r in records if r.get("status") == "COMPLETED")
                active = sum(1 for r in records if r.get("status") in ("GENERATING", "ACTIVE", "BUILDING"))
                failed = sum(1 for r in records if r.get("status") == "FAILED")
            except (json.JSONDecodeError, OSError):
                available = False
        return {
            "available": available,
            "status": "healthy" if available else "unavailable",
            "total_builds": total,
            "completed": completed,
            "active": active,
            "failed": failed,
        }

    def _check_roadmap(self) -> Dict[str, Any]:
        """Check roadmap from memory/roadmap.json."""
        roadmap_file = self.memory_dir / "roadmap.json"
        total_phases = comp = prog = pen = fld = 0
        available = roadmap_file.exists()
        if available:
            try:
                data = json.loads(roadmap_file.read_text())
                phases = data.get("phases", [])
                total_phases = len(phases)
                comp = sum(1 for p in phases if p.get("status") == "completed")
                prog = sum(1 for p in phases if p.get("status") == "in_progress")
                pen = sum(1 for p in phases if p.get("status") == "pending")
                fld = sum(1 for p in phases if p.get("status") == "failed")
            except (json.JSONDecodeError, OSError):
                available = False
        return {
            "available": available,
            "status": "healthy" if available else "degraded",
            "total_phases": total_phases,
            "completed": comp,
            "in_progress": prog,
            "pending": pen,
            "failed": fld,
        }

    def _check_knowledge(self) -> Dict[str, Any]:
        """Check knowledge engine via legal brain DB."""
        db_path = self.db_path
        available = db_path.exists()
        entities = 0
        if available:
            try:
                import sqlite3
                conn = sqlite3.connect(str(db_path))
                entities = conn.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]
                conn.close()
            except Exception:
                entities = 0
        return {
            "available": available,
            "status": "healthy" if available and entities > 0 else "degraded",
            "entities": entities,
        }

    def _check_trust(self) -> Dict[str, Any]:
        """Check trust engine via legal brain DB."""
        db_path = self.db_path
        available = db_path.exists()
        sources_scored = 0
        if available:
            try:
                import sqlite3
                conn = sqlite3.connect(str(db_path))
                sources_scored = conn.execute(
                    "SELECT COUNT(*) FROM knowledge_entries WHERE trust_score IS NOT NULL"
                ).fetchone()[0]
                conn.close()
            except Exception:
                sources_scored = 0
        return {
            "available": available,
            "status": "healthy" if available else "degraded",
            "sources_scored": sources_scored,
        }

    def _check_brain_health(self) -> Dict[str, Any]:
        """Check brain health via legal brain DB."""
        db_path = self.db_path
        available = db_path.exists()
        issues_found = 0
        if available:
            try:
                import sqlite3
                conn = sqlite3.connect(str(db_path))
                # Check for stale entries, corruption markers, etc.
                issues_found = conn.execute(
                    "SELECT COUNT(*) FROM knowledge_entries WHERE confidence = 0"
                ).fetchone()[0]
                conn.close()
            except Exception:
                issues_found = 0
        return {
            "available": available,
            "status": "healthy" if available and issues_found == 0 else "degraded",
            "issues_found": issues_found,
        }

    def _check_providers(self) -> Dict[str, Any]:
        """Check AI provider health."""
        providers = []
        available = True
        try:
            from core.ai_provider import list_providers
            provider_list = list_providers()
            # list_providers might return dict of provider_name -> ProviderInfo
            if isinstance(provider_list, dict):
                for name, info in provider_list.items():
                    providers.append({
                        "name": name,
                        "enabled": getattr(info, "enabled", True),
                        "health_status": "healthy",
                    })
            elif isinstance(provider_list, list):
                for p in provider_list:
                    if isinstance(p, dict):
                        providers.append(p)
                    else:
                        providers.append({"name": str(p), "enabled": True, "health_status": "healthy"})
        except ImportError:
            available = False
        except Exception as e:
            logger.warning(f"Provider check failed: {e}")
        return {
            "available": available,
            "status": "healthy" if available else "degraded",
            "providers": providers,
        }

    def _check_conversation(self) -> Dict[str, Any]:
        """Check conversation memory availability."""
        available = False
        try:
            from core.kai.conversation import get_conversations
            available = True
        except ImportError:
            pass
        return {"available": available, "status": "healthy" if available else "degraded"}

    def _check_research_sessions(self) -> Dict[str, Any]:
        """Check research sessions via legal brain DB."""
        db_path = self.db_path
        available = db_path.exists()
        return {"available": available, "status": "healthy" if available else "degraded"}

    def _check_workspace(self) -> Dict[str, Any]:
        """Check workspace module availability."""
        # Workspace is the project directory itself
        workspace_dir = Path(__file__).resolve().parent.parent.parent
        available = workspace_dir.exists() and workspace_dir.is_dir()
        return {"available": available, "status": "healthy" if available else "degraded"}

    # -- Full status report ------------------------------------------------

    def get_full_status(self) -> Dict[str, Any]:
        """Generate a complete cerebrum status report."""
        modules = self.discover_modules()
        available = sum(1 for m in modules.values() if m.get("available"))
        total = len(modules)
        healthy = sum(1 for m in modules.values() if m.get("status") == "healthy")
        degraded = sum(1 for m in modules.values() if m.get("status") == "degraded")
        unavailable = sum(1 for m in modules.values() if m.get("status") == "unavailable")

        # Overall health: healthy if all available, degraded if any degraded, unhealthy if any unavailable
        if unavailable > 0:
            overall = "unhealthy"
        elif degraded > 0:
            overall = "degraded"
        else:
            overall = "healthy"

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cerebrum_version": self.CEREBRUM_VERSION,
            "overall_health": overall,
            "module_summary": {
                "total": total,
                "available": available,
                "healthy": healthy,
                "degraded": degraded,
                "unavailable": unavailable,
            },
            "modules": modules,
        }

    # -- Alert formatting ---------------------------------------------------

    def format_alert(self, status: Dict[str, Any]) -> Optional[str]:
        """Format an alert string from a status report.

        Returns None when overall health is healthy (no alert needed).
        Returns an alert string when degraded or unhealthy.
        """
        if status.get("overall_health") == "healthy":
            return None

        modules = status.get("modules", {})
        degraded_modules = [
            name for name, info in modules.items()
            if info.get("status") in ("degraded", "unavailable")
            and not info.get("available", True)
        ]

        health = status["overall_health"].upper()
        alerts = [f"🚨 Cerebrum Command Center — {health}"]

        for name in degraded_modules:
            info = modules[name]
            error_msg = info.get("error", "Module unavailable")
            alerts.append(f"  ❌ {name}: {error_msg}")

        # Check individual modules for errors
        for name, info in modules.items():
            if info.get("error") and name not in degraded_modules:
                alerts.append(f"  ⚠️ {name}: {info['error']}")

        return "\n".join(alerts)

    # -- Dashboard formatting -----------------------------------------------

    def format_dashboard(self, status: Dict[str, Any]) -> str:
        """Format a human-readable dashboard from a status report."""
        lines = []
        lines.append("=" * 50)
        lines.append("CEREBRUM COMMAND CENTER — DASHBOARD")
        lines.append("=" * 50)
        lines.append(f"Version: {status.get('cerebrum_version', '?')}")
        lines.append(f"Timestamp: {status.get('timestamp', '?')[:19]}")
        lines.append(f"Overall: {status.get('overall_health', '?').upper()}")
        lines.append("")

        summary = status.get("module_summary", {})
        lines.append(f"Modules: {summary.get('total', 0)} total")
        lines.append(f"  Healthy: {summary.get('healthy', 0)}")
        lines.append(f"  Degraded: {summary.get('degraded', 0)}")
        lines.append(f"  Unavailable: {summary.get('unavailable', 0)}")

        modules = status.get("modules", {})
        if modules:
            lines.append("")
            lines.append("-" * 40)
            for name, info in sorted(modules.items()):
                icon = "✅" if info.get("status") == "healthy" else "⚠️" if info.get("status") == "degraded" else "❌"
                lines.append(f"  {icon} {name}: {info.get('status', '?')}")

        lines.append("")
        lines.append("=" * 50)
        return "\n".join(lines)


def get_command_center(db_path, memory_dir=None):
    """Factory function for CommandCenter.

    Args:
        db_path: Path to the legal brain SQLite database
        memory_dir: Optional path to memory directory
    """
    return CommandCenter(db_path, memory_dir=memory_dir)
